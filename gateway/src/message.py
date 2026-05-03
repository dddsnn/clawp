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
# You should have received a copy of the GNU Affero General Public License
# along with clawp. If not, see <https://www.gnu.org/licenses/>.

import abc
import asyncio
import collections.abc as cl_abc
import dataclasses as dc
import typing as t

import whenever as we

import model as mdl
import util

MessageRole = t.Literal["assistant", "developer", "system", "tool", "user"]



@dc.dataclass
class MessageMetadata:
    seq_in_session: t.Optional[int]
    """
    The message's sequence number in its session.

    A None value means the message is transient and will disappear again.
    """
    time: util.Value[we.Instant]
    """The time the message was fully received."""


class Message(abc.ABC):
    def __init__(self, metadata: MessageMetadata) -> None:
        self._metadata = metadata

    @property
    def metadata(self) -> MessageMetadata:
        return self._metadata

    @property
    @abc.abstractmethod
    def role(self) -> MessageRole:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    async def content(self) -> str:
        """The full content of the message."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    async def model(self) -> mdl.Message:
        """Model representation of this message."""
        raise NotImplementedError

    @property
    async def _metadata_model(self) -> mdl.MessageMetadata:
        return mdl.MessageMetadata(
            seq_in_session=self.metadata.seq_in_session, time=await
            self.metadata.time.value)

    @classmethod
    def from_model(cls, message_model: mdl.Message) -> t.Self:
        if isinstance(message_model, mdl.AssistantMessage):
            return AssistantMessage.from_model(message_model)
        elif isinstance(message_model, mdl.DeveloperMessage):
            return DeveloperMessage.from_model(message_model)
        elif isinstance(message_model, mdl.SystemMessage):
            return SystemMessage.from_model(message_model)
        elif isinstance(message_model, mdl.ToolMessage):
            return ToolMessage.from_model(message_model)
        else:
            assert isinstance(message_model, mdl.UserMessage)
            return UserMessage.from_model(message_model)


class SimpleMessage(Message):
    def __init__(
            self, metadata: MessageMetadata, role: MessageRole,
            content: str) -> None:
        super().__init__(metadata)
        if role not in t.get_args(MessageRole):
            raise ValueError(f"invalid role {role}")
        self._role = role
        self._content = content

    @property
    def role(self) -> MessageRole:
        return self._role

    @property
    async def content(self) -> str:
        return self._content

    @classmethod
    def from_model(cls, model: mdl.Message) -> t.Self:
        metadata = MessageMetadata(
            seq_in_session=model.metadata.seq_in_session,
            time=util.ImmediateValue(model.metadata.time))
        return cls(metadata, model.content)


class SystemMessage(SimpleMessage):
    def __init__(self, metadata: MessageMetadata, content: str) -> None:
        super().__init__(metadata, "system", content)

    @property
    async def model(self) -> mdl.SystemMessage:
        return mdl.SystemMessage(
            metadata=await self._metadata_model, content=await self.content)


class DeveloperMessage(SimpleMessage):
    def __init__(self, metadata: MessageMetadata, content: str) -> None:
        super().__init__(metadata, "developer", content)

    @property
    async def model(self) -> mdl.DeveloperMessage:
        return mdl.DeveloperMessage(
            metadata=await self._metadata_model, content=await self.content)


class UserMessage(SimpleMessage):
    """Message sent by the user."""
    def __init__(self, metadata: MessageMetadata, content: str) -> None:
        super().__init__(metadata, "user", content)

    @property
    async def model(self) -> mdl.UserMessage:
        return mdl.UserMessage(
            metadata=await self._metadata_model, content=await self.content)


class ToolMessage(SimpleMessage):
    """Message sent by the system in response to a tool call."""
    def __init__(
            self, metadata: MessageMetadata, content: str,
            tool_call_id: str) -> None:
        super().__init__(metadata, "tool", content)
        self._tool_call_id = tool_call_id

    @property
    def tool_call_id(self) -> str:
        return self._tool_call_id

    @property
    async def model(self) -> mdl.ToolMessage:
        return mdl.ToolMessage(
            metadata=await self._metadata_model, content=await self.content,
            tool_call_id=self.tool_call_id)

    @classmethod
    def from_model(cls, model: mdl.ToolMessage) -> t.Self:
        metadata = MessageMetadata(
            seq_in_session=model.metadata.seq_in_session,
            time=util.ImmediateValue(model.metadata.time))
        return cls(metadata, model.content, model.tool_call_id)


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
    def model(self) -> mdl.ToolCall:
        return mdl.ToolCall(
            id=self.id, function=mdl.ToolCallFunction(
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

    async def append(self, fragment: str | ToolCall | Exception) -> None:
        await self._fragments.append(fragment)

    async def stream_fragments(
            self) -> cl_abc.AsyncGenerator[str | ToolCall | Exception]:
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

    Since the time property of the message metadata should show the time the
    message has fully arrived, a task is started on construction that waits for
    the final part to arrive and then sets the time. The property on the
    metadata will block until then. The task can be awaited as part of
    wait_finalized().
    """
    def __init__(
            self, metadata: MessageMetadata,
            parts: util.StreamableList) -> None:
        super().__init__(metadata)
        self._parts = parts
        self._set_time_task = asyncio.create_task(self._set_time())

    async def _set_time(self):
        if isinstance(self.metadata.time, util.FutureValue):
            # This message is streaming (and not loaded from storage), so we
            # have to set the time in the metadata when the message has
            # arrived.
            await self._parts.wait_finalized()
            self.metadata.time.value = we.Instant.now()
        self._set_time_task = None

    async def wait_finalized(self) -> None:
        """Wait until the message has finished streaming."""
        await self._parts.wait_finalized()
        if self._set_time_task:
            await self._set_time_task

    @property
    def role(self) -> t.Literal["assistant"]:
        return "assistant"

    @property
    async def content(self) -> str:
        """The final content of the message (the one that "counts")."""
        return await self._concat_part_text("content")

    @property
    async def reasoning(self) -> str:
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
    async def tool_calls(self) -> list[ToolCall]:
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
    async def errors(self) -> list[Exception]:
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
    async def model(self) -> mdl.AssistantMessage:
        tool_calls = [tool_call.model for tool_call in await self.tool_calls]
        errors = [f"Error: {exc}" for exc in await self.errors]
        return mdl.AssistantMessage(
            metadata=await self._metadata_model, content=await self.content,
            reasoning=await self.reasoning, tool_calls=tool_calls,
            errors=errors)

    @classmethod
    def from_model(cls, model: mdl.AssistantMessage) -> t.Self:
        metadata = MessageMetadata(
            seq_in_session=model.metadata.seq_in_session,
            time=util.ImmediateValue(model.metadata.time))
        parts: list[AssistantMessagePart] = [
            AssistantMessageTextPart("content", [model.content])]
        if model.reasoning:
            parts.append(
                AssistantMessageTextPart("reasoning", [model.reasoning]))
        tool_calls = []
        for tool_call in model.tool_calls:
            function = ToolCallFunction(
                tool_call.function.name, tool_call.function.arguments)
            tool_calls.append(ToolCall(tool_call.id, function))
        if tool_calls:
            parts.append(AssistantMessageToolPart(tool_calls))
        if model.errors:
            parts.append(
                AssistantMessageErrorPart([Exception(e)
                                           for e in model.errors]))
        return cls(metadata, util.StreamableList(parts))
