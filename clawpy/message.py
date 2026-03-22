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
import dataclasses as dc
import json
import logging
import typing as t

import openrouter
import openrouter.components as or_comp
import tool

OpenRouterMessage = (
    or_comp.AssistantMessage | or_comp.DeveloperMessage | or_comp.SystemMessage
    | or_comp.ToolResponseMessage | or_comp.UserMessage)
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


@dc.dataclass
class ToolCallFunction:
    name: str = ""
    arguments: str = ""


@dc.dataclass
class ToolCall:
    id: str
    function: ToolCallFunction = dc.field(default_factory=ToolCallFunction)


@dc.dataclass
class AssistantMessagePart:
    type: t.Literal["content", "reasoning"]
    text: str = ""


class AssistantMessage(Message):
    def __init__(self, stream: or_comp.EventStreamAsync) -> None:
        self._parts: AssistantMessagePart = []
        self._tool_calls = []
        self._read_stream_task = asyncio.create_task(self._read_stream(stream))

    @property
    def role(self) -> t.Literal["assistant"]:
        return "assistant"

    @property
    async def content(self) -> t.Awaitable[str]:
        await self._read_stream_task
        return "\n".join(
            part.text for part in self._parts if part.type == "content")

    @property
    async def reasoning(self) -> t.Awaitable[str]:
        await self._read_stream_task
        return "\n".join(
            part.text for part in self._parts if part.type == "reasoning")

    @property
    async def tool_calls(self) -> t.Awaitable[list[ToolCall]]:
        """
        Tool calls made in this message.

        These are not streamed and will only be available once the entire
        message has arrived.
        """
        await self._read_stream_task
        return self._tool_calls

    async def _read_stream(self, stream: or_comp.EventStreamAsync) -> None:
        tool_calls_kwargs = {}
        async for chunk in stream:
            if not isinstance(chunk, or_comp.ChatStreamingResponseChunk):
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
            if not self._parts or self._parts[-1].type != part_type:
                self._parts.append(AssistantMessagePart(type=part_type))
            self._parts[-1].text += text
        for _, tool_call_kwargs in sorted(tool_calls_kwargs.items()):
            function = ToolCallFunction(
                name=tool_call_kwargs["name"],
                arguments=tool_call_kwargs["arguments"])
            self._tool_calls.append(
                ToolCall(id=tool_call_kwargs["id"], function=function))


class Session:
    def __init__(
            self, openrouter_client: openrouter.OpenRouter,
            mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._openrouter_client = openrouter_client
        self._messages: list[Message] = []
        self._mcp_client = mcp_client

    @property
    def tools(self) -> list[or_comp.ToolDefinitionJSON]:
        return [
            or_comp.ToolDefinitionJSON(
                type="function", function=or_comp.ToolDefinitionJSONFunction(
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
                openrouter_message = or_comp.DeveloperMessage(
                    role=message.role, content=await message.content)
            elif message.role == "system":
                openrouter_message = or_comp.SystemMessage(
                    role=message.role, content=await message.content)
            elif message.role == "tool":
                openrouter_message = or_comp.ToolResponseMessage(
                    role=message.role, content=await message.content,
                    tool_call_id=message.tool_call_id)
            elif message.role == "user":
                openrouter_message = or_comp.UserMessage(
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
            function = or_comp.ChatMessageToolCallFunction(
                name=tc.function.name, arguments=tc.function.arguments)
            tool_calls.append(
                or_comp.ChatMessageToolCall(
                    id=tc.id, type="function", function=function))
        return or_comp.AssistantMessage(
            role=message.role, content=await message.content, reasoning=await
            message.reasoning, tool_calls=tool_calls)
