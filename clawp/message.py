# Copyright 2026 Marc Lehmann

# This file is part of clawp.
#
# clawp is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# clawp is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with clawp. If not, see <https://www.gnu.org/licenses/>.

import abc
import asyncio
import collections.abc as cl_abc
import dataclasses as dc
import json
import logging
import typing as t

import openrouter
import openrouter.components as or_comp
import tool

OpenRouterMessage = (
    or_comp.ChatAssistantMessage | or_comp.ChatDeveloperMessage
    | or_comp.ChatSystemMessage
    | or_comp.ChatToolMessage | or_comp.ChatUserMessage)
MessageRole = t.Literal["assistant", "developer", "system", "tool", "user"]


class Message(abc.ABC):
    @property
    @abc.abstractmethod
    def role(self) -> MessageRole:
        return self._role

    @property
    @abc.abstractmethod
    async def content(self) -> t.Awaitable[str]:
        return self._content


class SimpleMessage(Message):
    def __init__(self, role: MessageRole, content: str) -> None:
        if role not in t.get_args(MessageRole):
            raise ValueError(f"Invalid role {role}.")
        self._role = role
        self._content = content

    @property
    def role(self) -> MessageRole:
        return self._role

    @property
    async def content(self) -> t.Awaitable[str]:
        return self._content


class SystemMessage(SimpleMessage):
    def __init__(self, content: str) -> None:
        super().__init__("system", content)


class DeveloperMessage(SimpleMessage):
    def __init__(self, content: str) -> None:
        super().__init__("developer", content)


class UserMessage(SimpleMessage):
    def __init__(self, content: str) -> None:
        super().__init__("user", content)


class ToolMessage(SimpleMessage):
    def __init__(self, content: str, tool_call_id: str) -> None:
        super().__init__("tool", content)
        self._tool_call_id = tool_call_id

    @property
    def tool_call_id(self) -> str:
        return self._tool_call_id


class StreamableList:
    def __init__(self):
        self._list = []
        self._stream_condition = asyncio.Condition()
        self._finalized_event = asyncio.Event()
        self._finalized_wait_task = asyncio.create_task(
            self._finalized_event.wait())

    def __bool__(self) -> bool:
        return bool(self._list)

    def __getitem__(self, index):
        return self._list[index]

    def __iter__(self) -> cl_abc.Iterator:
        return iter(self._list)

    async def append(self, item) -> None:
        if self._finalized_event.is_set():
            raise ValueError("StreamableList has already been finalized.")
        self._list.append(item)
        async with self._stream_condition:
            self._stream_condition.notify_all()

    def finalize(self) -> None:
        self._finalized_event.set()

    async def wait_finalized(self) -> None:
        await self._finalized_wait_task

    async def stream(self) -> cl_abc.AsyncGenerator:
        i = 0
        while True:
            if i < len(self._list):
                yield self._list[i]
                i += 1
                continue
            elif self._finalized_event.is_set():
                return
            condition_wait_task = asyncio.create_task(
                self._wait_for_condition())
            await asyncio.wait(
                {condition_wait_task, self._finalized_wait_task},
                return_when=asyncio.FIRST_COMPLETED)
            condition_wait_task.cancel()

    async def _wait_for_condition(self):
        async with self._stream_condition:
            await self._stream_condition.wait()


@dc.dataclass
class ToolCallFunction:
    name: str = ""
    arguments: str = ""


@dc.dataclass
class ToolCall:
    id: str
    function: ToolCallFunction = dc.field(default_factory=ToolCallFunction)


class AssistantMessagePart:
    VALID_TYPES = t.Literal["content", "reasoning", "tool"]

    def __init__(self, part_type: VALID_TYPES):
        if part_type not in t.get_args(self.VALID_TYPES):
            raise ValueError(f"Invalid type {part_type}.")
        self._type = part_type
        self._fragments = StreamableList()

    @property
    def type(self) -> VALID_TYPES:
        return self._type

    async def append(self, fragment: str | ToolCall) -> None:
        await self._fragments.append(fragment)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[str | ToolCall]:
        async for fragment in self._fragments.stream():
            yield fragment

    def finalize(self) -> None:
        self._fragments.finalize()


class AssistantMessageTextPart(AssistantMessagePart):
    VALID_TYPES = t.Literal["content", "reasoning"]

    def __init__(self, part_type: VALID_TYPES):
        super().__init__(part_type)

    async def append(self, text: str) -> None:
        await super().append(text)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[str]:
        async for fragment in self._fragments.stream():
            yield fragment

    def finalize(self) -> None:
        super().finalize()


class AssistantMessageToolPart(AssistantMessagePart):
    VALID_TYPES = t.Literal["tool"]

    def __init__(self):
        super().__init__("tool")

    async def append(self, tool_call: ToolCall) -> None:
        await super().append(tool_call)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[ToolCall]:
        async for fragment in super().stream_fragments():
            yield fragment


class AssistantMessage(Message):
    def __init__(self, stream: or_comp.EventStreamAsync) -> None:
        self._parts = StreamableList()
        asyncio.create_task(self._read_stream(stream))

    @property
    def role(self) -> t.Literal["assistant"]:
        return "assistant"

    @property
    async def content(self) -> t.Awaitable[str]:
        return await self._concat_part_text("content")

    @property
    async def reasoning(self) -> t.Awaitable[str]:
        return await self._concat_part_text("reasoning")

    async def _concat_part_text(
            self, part_type: AssistantMessageTextPart.VALID_TYPES):
        await self._parts.wait_finalized()
        prepend_newline = False
        text = ""
        for part in self._parts:
            if part.type != part_type:
                continue
            if prepend_newline:
                text += "\n"
            prepend_newline = True
            async for fragment in part.stream_fragments():
                text += fragment

    @property
    async def tool_calls(self) -> t.Awaitable[list[ToolCall]]:
        """
        Tool calls made in this message.

        These are not streamed and will only be available once the entire
        message has arrived.
        """
        await self._parts.wait_finalized()
        tool_calls = []
        for part in self._parts:
            if part.type != "tool":
                continue
            async for tool_call in part.stream_fragments():
                tool_calls.append(tool_call)
        return tool_calls

    async def stream_parts(
            self) -> cl_abc.AsyncGenerator[AssistantMessagePart]:
        async for part in self._parts.stream():
            yield part

    async def _read_stream(self, stream: or_comp.EventStreamAsync) -> None:
        tool_calls_kwargs = {}
        async for chunk in stream:
            if not isinstance(chunk, or_comp.ChatStreamChunk):
                raise ValueError(
                    f"Unexpected chunk type {type(chunk)} in stream.")
            if len(chunk.choices) != 1:
                raise ValueError(
                    f"Unexpected number of choices ({len(chunk.choices)}) in "
                    "chunk.")
            delta = chunk.choices[0].delta
            if delta.role != "assistant":
                raise ValueError(
                    f"Unexpected role {delta.role} in assistant message.")
            if delta.content and delta.reasoning:
                raise ValueError(
                    "Assistant message contains both content "
                    f"('{delta.content}') and reasoning ('{delta.reasoning}')."
                )
            for tool_call in delta.tool_calls or []:
                tool_call_kwargs = tool_calls_kwargs.setdefault(
                    tool_call.index, {})
                tool_call_kwargs.setdefault("id", "")
                tool_call_kwargs.setdefault("name", "")
                tool_call_kwargs.setdefault("arguments", "")
                tool_call_kwargs["id"] += tool_call.id or ""
                tool_call_kwargs["name"] += tool_call.function.name or ""
                tool_call_kwargs["arguments"] += (
                    tool_call.function.arguments or "")
            if not delta.content and not delta.reasoning:
                continue
            part_type = "content" if delta.content else "reasoning"
            text = delta.content or delta.reasoning
            try:
                if self._parts[-1].type != part_type:
                    self._parts[-1].finalize()
                    await self._parts.append(
                        AssistantMessageTextPart(part_type))
            except IndexError:
                await self._parts.append(AssistantMessageTextPart(part_type))
            await self._parts[-1].append(text)
        try:
            # All parts have been parsed, now also finalize the last one.
            self._parts[-1].finalize()
        except IndexError:
            pass
        if tool_calls_kwargs:
            await self._parts.append(AssistantMessageToolPart())
            for _, tool_call_kwargs in sorted(tool_calls_kwargs.items()):
                function = ToolCallFunction(
                    name=tool_call_kwargs["name"],
                    arguments=tool_call_kwargs["arguments"])
                await self._parts[-1].append(
                    ToolCall(id=tool_call_kwargs["id"], function=function))
            self._parts[-1].finalize()
        self._parts.finalize()


class Session:
    def __init__(
            self, openrouter_client: openrouter.OpenRouter,
            mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._openrouter_client = openrouter_client
        self._messages: list[Message] = []
        self._mcp_client = mcp_client

    @property
    def tools(self) -> list[or_comp.ChatFunctionToolFunction]:
        return [
            or_comp.ChatFunctionToolFunction(
                type="function",
                function=or_comp.ChatFunctionToolFunctionFunction(
                    name=t.name, description=t.description,
                    parameters=t.inputSchema, strict=True))
            for t in self._mcp_client.tools.values()]

    async def process_user_message(self,
                                   user_message_content: str) -> list[Message]:
        self._messages.append(UserMessage(user_message_content))
        while True:
            assistant_message = AssistantMessage(await self._request_stream())
            self._messages.append(assistant_message)
            yield assistant_message
            if not await assistant_message.tool_calls:
                return
            for tool_call in await assistant_message.tool_calls:
                self._logger.debug(f"Handling tool call {tool_call}.")
                try:
                    arguments_dict = json.loads(tool_call.function.arguments)
                    result = await self._mcp_client.call_tool(
                        tool_call.function.name, arguments_dict)
                    self._messages.append(
                        ToolMessage(
                            content=str(result.data),
                            tool_call_id=tool_call.id))
                except Exception as e:
                    self._messages.append(
                        ToolMessage(
                            content="Error in tool call: " + str(e),
                            tool_call_id=tool_call.id))
                    self._logger.exception("Error in tool call.")

    async def _request_stream(self):
        return await self._openrouter_client.chat.send_async(
            messages=await self._as_openrouter_message_list(),
            model="stepfun/step-3.5-flash:free", tools=self.tools, stream=True)

    async def _as_openrouter_message_list(self) -> list[OpenRouterMessage]:
        openrouter_messages = []
        for message in self._messages:
            if message.role == "assistant":
                openrouter_message = (
                    await self._create_openrouter_assistant_message(message))
            elif message.role == "developer":
                openrouter_message = or_comp.ChatDeveloperMessage(
                    role=message.role, content=await message.content)
            elif message.role == "system":
                openrouter_message = or_comp.ChatSystemMessage(
                    role=message.role, content=await message.content)
            elif message.role == "tool":
                openrouter_message = or_comp.ChatToolMessage(
                    role=message.role, content=await message.content,
                    tool_call_id=message.tool_call_id)
            elif message.role == "user":
                openrouter_message = or_comp.ChatUserMessage(
                    role=message.role, content=await message.content)
            else:
                raise ValueError(f"Invalid message role {message.role}.")
            openrouter_messages.append(openrouter_message)
        return openrouter_messages

    @staticmethod
    async def _create_openrouter_assistant_message(
            message: AssistantMessage) -> or_comp.AssistantMessage:
        tool_calls = []
        for tc in await message.tool_calls:
            function = or_comp.ChatToolCallFunction(
                name=tc.function.name, arguments=tc.function.arguments)
            tool_calls.append(
                or_comp.ChatToolCall(
                    id=tc.id, type="function", function=function))
        return or_comp.ChatAssistantMessage(
            role=message.role, content=await message.content, reasoning=await
            message.reasoning, tool_calls=tool_calls)
