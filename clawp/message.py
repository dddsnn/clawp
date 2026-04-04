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

import tool

if t.TYPE_CHECKING:
    import provider as prov

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
    VALID_TYPES = t.Literal["content", "error", "reasoning", "tool"]

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


class AssistantMessageErrorPart(AssistantMessagePart):
    VALID_TYPES = t.Literal["error"]

    def __init__(self):
        super().__init__("error")

    async def append(self, error: Exception) -> None:
        await super().append(error)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[Exception]:
        async for fragment in super().stream_fragments():
            yield fragment


class AssistantMessage(Message):
    def __init__(self, parts: StreamableList) -> None:
        self._parts = parts

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


class Session:
    def __init__(
            self, provider: "prov.Provider", mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._provider = provider
        self._mcp_client = mcp_client
        self._messages: list[Message] = []

    async def process_user_message(self,
                                   user_message_content: str) -> list[Message]:
        self._messages.append(UserMessage(user_message_content))
        while True:
            assistant_message = await self._provider.request_assistant_message(
                self._messages, self._mcp_client.tools.values())
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

