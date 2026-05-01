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
import typing as t

import whenever as we

import model
import util

MessageRole = t.Literal["assistant", "developer", "system", "tool", "user"]


@dc.dataclass
class MessageMetadata:
    seq_in_session: t.Optional[int]
    """
    The message's sequence number in its session.

    A None value means the message is transient and will disappear again.
    """


class Message(abc.ABC):
    def __init__(self, metadata: MessageMetadata) -> None:
        self._metadata = metadata

    @property
    def metadata(self) -> MessageMetadata:
        return self._metadata

    @property
    @abc.abstractmethod
    async def time(self) -> cl_abc.Awaitable[we.Instant]:
        """The time the message was fully received."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def role(self) -> MessageRole:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    async def content(self) -> cl_abc.Awaitable[str]:
        """The full content of the message."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    async def model(self) -> cl_abc.Awaitable[model.Message]:
        """Model representation of this message."""
        raise NotImplementedError

    @property
    async def _metadata_model(self) -> model.MessageMetadata:
        return model.MessageMetadata(
            time=await self.time, seq_in_session=self.metadata.seq_in_session)

    @classmethod
    def from_model(cls, message_model: model.Message) -> t.Self:
        if isinstance(message_model, model.AssistantMessage):
            return AssistantMessage.from_model(message_model)
        elif isinstance(message_model, model.DeveloperMessage):
            return DeveloperMessage.from_model(message_model)
        elif isinstance(message_model, model.SystemMessage):
            return SystemMessage.from_model(message_model)
        elif isinstance(message_model, model.ToolMessage):
            return ToolMessage.from_model(message_model)
        else:
            assert isinstance(message_model, model.UserMessage)
            return UserMessage.from_model(message_model)


class SimpleMessage(Message):
    def __init__(
            self, metadata: MessageMetadata, role: MessageRole, content: str,
            time: t.Optional[we.Instant] = None) -> None:
        super().__init__(metadata)
        if role not in t.get_args(MessageRole):
            raise ValueError(f"invalid role {role}")
        self._time = time or we.Instant.now()
        self._role = role
        self._content = content

    @property
    async def time(self) -> cl_abc.Awaitable[we.Instant]:
        return self._time

    @property
    def role(self) -> MessageRole:
        return self._role

    @property
    async def content(self) -> cl_abc.Awaitable[str]:
        return self._content

    @classmethod
    def from_model(cls, message_model: model.Message) -> t.Self:
        metadata = MessageMetadata(message_model.metadata.seq_in_session)
        return cls(
            metadata, message_model.content, message_model.metadata.time)


class SystemMessage(SimpleMessage):
    def __init__(
            self, metadata: MessageMetadata, content: str,
            time: t.Optional[we.Instant] = None) -> None:
        super().__init__(metadata, "system", content, time)

    @property
    async def model(self) -> cl_abc.Awaitable[model.SystemMessage]:
        return model.SystemMessage(
            metadata=await self._metadata_model, content=await self.content)


class DeveloperMessage(SimpleMessage):
    def __init__(
            self, metadata: MessageMetadata, content: str,
            time: t.Optional[we.Instant] = None) -> None:
        super().__init__(metadata, "developer", content, time)

    @property
    async def model(self) -> cl_abc.Awaitable[model.DeveloperMessage]:
        return model.DeveloperMessage(
            metadata=await self._metadata_model, content=await self.content)


class UserMessage(SimpleMessage):
    """Message sent by the user."""
    def __init__(
            self, metadata: MessageMetadata, content: str,
            time: t.Optional[we.Instant] = None) -> None:
        super().__init__(metadata, "user", content, time)

    @property
    async def model(self) -> cl_abc.Awaitable[model.UserMessage]:
        return model.UserMessage(
            metadata=await self._metadata_model, content=await self.content)


class ToolMessage(SimpleMessage):
    """Message sent by the system in response to a tool call."""
    def __init__(
            self, metadata: MessageMetadata, content: str, tool_call_id: str,
            time: t.Optional[we.Instant] = None) -> None:
        super().__init__(metadata, "tool", content, time)
        self._tool_call_id = tool_call_id

    @property
    def tool_call_id(self) -> str:
        return self._tool_call_id

    @property
    async def model(self) -> cl_abc.Awaitable[model.ToolMessage]:
        return model.ToolMessage(
            metadata=await self._metadata_model, content=await self.content,
            tool_call_id=self.tool_call_id)

    @classmethod
    def from_model(cls, message_model: model.ToolMessage) -> t.Self:
        metadata = MessageMetadata(message_model.metadata.seq_in_session)
        return cls(
            metadata, message_model.content, message_model.tool_call_id,
            message_model.metadata.time)


@dc.dataclass
class ToolCallFunction:
    """A named function used in the assistant's tool call."""
    name: str = ""
    arguments: str = ""


@dc.dataclass
class ToolCall:
    """A tool call requested by the assistant."""
    id: str
    function: ToolCallFunction = dc.field(default_factory=ToolCallFunction)

    @property
    def model(self) -> model.ToolCall:
        return model.ToolCall(
            id=self.id, function=model.ToolCallFunction(
                name=self.function.name, arguments=self.function.arguments))


class AssistantMessagePart:
    """
    One part of an AssistantMessage.

    AssistantMessageParts consist of fragments that can be streamed. The type
    of these fragments depends on the type of part.

    A part can be initialized with fragments, but then it is immediately
    finalized and nothing more can be appended.
    """
    VALID_TYPES = t.Literal["content", "error", "reasoning", "tool"]

    def __init__(self, part_type: VALID_TYPES, fragments: list | None = None):
        if part_type not in t.get_args(self.VALID_TYPES):
            raise ValueError(f"invalid type {part_type}")
        self._type = part_type
        self._fragments = util.StreamableList(fragments)

    @property
    def type(self) -> VALID_TYPES:
        return self._type

    async def append(self, fragment: str | ToolCall) -> None:
        await self._fragments.append(fragment)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[str | ToolCall]:
        async for fragment in self._fragments.stream():
            yield fragment

    async def finalize(self) -> None:
        await self._fragments.finalize()


class AssistantMessageTextPart(AssistantMessagePart):
    VALID_TYPES = t.Literal["content", "reasoning"]

    def __init__(
            self, part_type: VALID_TYPES, fragments: list[str] | None = None):
        super().__init__(part_type, fragments)

    async def append(self, text: str) -> None:
        await super().append(text)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[str]:
        async for fragment in self._fragments.stream():
            yield fragment

    async def finalize(self) -> None:
        await self._fragments.finalize(compact=lambda list_: ["".join(list_)])


class AssistantMessageToolPart(AssistantMessagePart):
    VALID_TYPES = t.Literal["tool"]

    def __init__(self, fragments: list[ToolCall] | None = None):
        super().__init__("tool", fragments)

    async def append(self, tool_call: ToolCall) -> None:
        await super().append(tool_call)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[ToolCall]:
        async for fragment in super().stream_fragments():
            yield fragment


class AssistantMessageErrorPart(AssistantMessagePart):
    VALID_TYPES = t.Literal["error"]

    def __init__(self, fragments: list[Exception] | None = None):
        super().__init__("error", fragments)

    async def append(self, error: Exception) -> None:
        await super().append(error)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[Exception]:
        async for fragment in super().stream_fragments():
            yield fragment


class AssistantMessage(Message):
    """
    A message returned by the assistant.

    AssistantMessages are more complex than those by the system or the user. In
    addition to content, they also contain reasoning and tool calls (with which
    the assistant requests that we execute something for them). These different
    types of content are represented as AssistantMessageParts.

    Additionally, AssistantMessages are streamed so we can work with them
    before the full output has arrived from the provider. First, parts are
    streamed as they arrive. Then, the fragments of the parts themselves can be
    streamed.

    Since the time property should show the time the messages has fully
    arrived, a task is started on construction that waits for the final part to
    arrive and then sets the time. The property will block until then.
    """
    def __init__(
            self, metadata: MessageMetadata, parts: util.StreamableList,
            time: t.Optional[we.Instant] = None) -> None:
        super().__init__(metadata)
        self._parts = parts
        self._time = time
        self._set_time_task = asyncio.create_task(self._set_time())

    async def _set_time(self):
        await self._parts.wait_finalized()
        self._time = self._time or we.Instant.now()
        self._set_time_task = None

    @property
    async def time(self) -> cl_abc.Awaitable[we.Instant]:
        if self._set_time_task:
            await self._set_time_task
        return self._time

    @property
    def role(self) -> t.Literal["assistant"]:
        return "assistant"

    @property
    async def content(self) -> cl_abc.Awaitable[str]:
        """The final content of the message (the one that "counts")."""
        return await self._concat_part_text("content")

    @property
    async def reasoning(self) -> cl_abc.Awaitable[str]:
        """The reasoning of the assistant in producing the content."""
        return await self._concat_part_text("reasoning")

    async def _concat_part_text(
            self, part_type: AssistantMessageTextPart.VALID_TYPES):
        result = ""
        async for text in self._collect_fragments(part_type):
            result += text
        return result

    async def _collect_fragments(
            self, part_type: AssistantMessagePart.VALID_TYPES):
        await self._parts.wait_finalized()
        for part in self._parts:
            if part.type != part_type:
                continue
            async for fragment in part.stream_fragments():
                yield fragment

    @property
    async def tool_calls(self) -> cl_abc.Awaitable[list[ToolCall]]:
        """
        Tool calls made in this message.

        These are not streamed and will only be available once the entire
        message has arrived.
        """
        tool_calls = []
        async for tool_call in self._collect_fragments("tool"):
            tool_calls.append(tool_call)
        return tool_calls

    @property
    async def errors(self) -> cl_abc.Awaitable[list[Exception]]:
        """
        Errors in this message.

        These are not streamed and will only be available once the entire
        message has arrived.
        """
        exceptions = []
        async for exception in self._collect_fragments("error"):
            exceptions.append(exception)
        return exceptions

    async def stream_parts(
            self) -> cl_abc.AsyncGenerator[AssistantMessagePart]:
        """Asynchronously iterate over parts as they arrive."""
        async for part in self._parts.stream():
            yield part

    @property
    async def model(self) -> cl_abc.Awaitable[model.AssistantMessage]:
        tool_calls = [tool_call.model for tool_call in await self.tool_calls]
        errors = [f"Error: {exc}" for exc in await self.errors]
        return model.AssistantMessage(
            metadata=await self._metadata_model, content=await self.content,
            reasoning=await self.reasoning, tool_calls=tool_calls,
            errors=errors)

    @classmethod
    def from_model(cls, message_model: model.AssistantMessage) -> t.Self:
        metadata = MessageMetadata(message_model.metadata.seq_in_session)
        parts = [AssistantMessageTextPart("content", [message_model.content])]
        if message_model.reasoning:
            parts.append(
                AssistantMessageTextPart(
                    "reasoning", [message_model.reasoning]))
        tool_calls = []
        for tool_call in message_model.tool_calls:
            function = ToolCallFunction(
                tool_call.function.name, tool_call.function.arguments)
            tool_calls.append(ToolCall(tool_call.id, function))
        if tool_calls:
            parts.append(AssistantMessageToolPart(tool_calls))
        if message_model.errors:
            parts.append(
                AssistantMessageErrorPart([
                    Exception(e) for e in message_model.errors]))
        return cls(
            metadata, util.StreamableList(parts), message_model.metadata.time)
