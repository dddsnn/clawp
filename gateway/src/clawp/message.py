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
import logging
import typing as t

import whenever as we

from . import model as mdl
from . import util

MessageRole = t.Literal["agent", "developer", "system", "tool", "user"]


@dc.dataclass
class IncomingMessageMetadata:
    """
    Message metadata known immediately.

    This is the metadata that is known as soon as the message is fully
    received.
    """
    time: util.Value[we.Instant]
    """The time the message was fully received."""
    channel: mdl.ChannelDescriptor
    """The channel the message is on."""


@dc.dataclass
class MessageMetadata(IncomingMessageMetadata):
    """
    Full message metadata.

    This is the metadata that is known once a message has been integrated and
    committed into the running system.
    """
    seq_in_session: int
    """The message's sequence number in its session."""


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
            seq_in_session=self.metadata.seq_in_session,
            time=await self.metadata.time.value,
            channel=self.metadata.channel,
        )

    @classmethod
    def from_model(cls, message_model: mdl.Message) -> t.Self:
        if isinstance(message_model, mdl.AgentMessage):
            return AgentMessage.from_model(message_model)
        elif isinstance(message_model, mdl.DeveloperMessage):
            return DeveloperMessage.from_model(message_model)
        elif isinstance(message_model, mdl.SystemMessage):
            return SystemMessage.from_model(message_model)
        elif isinstance(message_model, mdl.ToolMessage):
            return ToolMessage.from_model(message_model)
        else:
            assert isinstance(message_model, mdl.UserMessage)
            return UserMessage.from_model(message_model)

    @classmethod
    def _metadata_from_model(cls, model: mdl.Message) -> MessageMetadata:
        return MessageMetadata(
            seq_in_session=model.metadata.seq_in_session,
            time=util.ImmediateValue(model.metadata.time),
            channel=model.metadata.channel,
        )


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
        return cls(cls._metadata_from_model(model), model.content)


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
        return cls(
            cls._metadata_from_model(model), model.content, model.tool_call_id)


@dc.dataclass
class ToolCallFunction:
    """A named function used in the agent's tool call."""
    name: str = ""
    arguments: str = ""


@dc.dataclass
class ToolCall:
    """A tool call requested by the agent."""
    id: str
    function: ToolCallFunction = dc.field(default_factory=ToolCallFunction)

    @property
    def model(self) -> mdl.ToolCall:
        return mdl.ToolCall(
            id=self.id, function=mdl.ToolCallFunction(
                name=self.function.name, arguments=self.function.arguments))


class AgentMessagePart:
    """
    One part of an AgentMessage.

    AgentMessageParts consist of fragments that can be streamed. The type of
    these fragments depends on the type of part.

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


class AgentMessageTextPart(AgentMessagePart):
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


class AgentMessageReasoningPart(AgentMessageTextPart):
    def __init__(self, fragments: list[str] | None = None):
        super().__init__("reasoning", fragments)


class AgentMessageContentPart(AgentMessageTextPart):
    """
    Content part of an agent message.

    In contrast to the raw text part, overrides stream_fragments() to strip off
    the channel header.
    """
    def __init__(self, fragments: list[str] | None = None):
        super().__init__("content", fragments)


class AgentMessageToolPart(AgentMessagePart):
    VALID_TYPES = t.Literal["tool"]

    def __init__(self, fragments: list[ToolCall] | None = None):
        super().__init__("tool", fragments)

    async def append(self, tool_call: ToolCall) -> None:
        await super().append(tool_call)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[ToolCall]:
        async for fragment in super().stream_fragments():
            yield fragment


class AgentMessageErrorPart(AgentMessagePart):
    VALID_TYPES = t.Literal["error"]

    def __init__(self, fragments: list[Exception] | None = None):
        super().__init__("error", fragments)

    async def append(self, error: Exception) -> None:
        await super().append(error)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[Exception]:
        async for fragment in super().stream_fragments():
            yield fragment


class AgentMessage(Message):
    """
    A message returned by the agent.

    AgentMessages are more complex than those by the system or the user. In
    addition to content, they also contain reasoning and tool calls (with which
    the agent requests that we execute something for them). These different
    types of content are represented as AgentMessageParts.

    Additionally, AgentMessages are streamed so we can work with them before
    the full output has arrived from the provider. First, parts are streamed as
    they arrive. Then, the fragments of the parts themselves can be streamed.

    Some of the message's metadata isn't immediately available, since part or
    all of it needs to be parsed first. For this reason, the constructor starts
    tasks to set this data once it is available. The properties on the metadata
    will block until then. The tasks are awaited as part of wait_finalized().
    """
    _logger = logging.getLogger("AgentMessage")

    def __init__(
            self, metadata: MessageMetadata,
            parts: util.StreamableList) -> None:
        super().__init__(metadata)
        self._parts = parts
        self._deferred_set_tasks = {
            asyncio.create_task(self._deferred_set(self._set_time()))}

    async def _deferred_set(self, setter):
        try:
            await setter
        except Exception:
            self._logger.exception(f"Error running {setter}.")
        self._deferred_set_tasks.discard(asyncio.current_task())
        if not self._deferred_set_tasks:
            self._deferred_set_tasks = None

    async def _set_time(self):
        if isinstance(self.metadata.time, util.ImmediateValue):
            # The time is already available (probably loaded from storage), no
            # need to do anything.
            return
        assert isinstance(self.metadata.time, util.FutureValue)
        await self._parts.wait_finalized()
        self.metadata.time.value = we.Instant.now()

    async def wait_finalized(self) -> None:
        """Wait until the message has finished streaming."""
        await self._parts.wait_finalized()
        for task in self._deferred_set_tasks or []:
            await task

    @property
    def role(self) -> t.Literal["agent"]:
        return "agent"

    @property
    async def content(self) -> str:
        """The final content of the message (the one that "counts")."""
        return await self._concat_part_text("content")

    @property
    async def reasoning(self) -> str:
        """The reasoning of the agent in producing the content."""
        return await self._concat_part_text("reasoning")

    async def _concat_part_text(
            self, part_type: AgentMessageTextPart.VALID_TYPES):
        result = ""
        async for text in self._collect_fragments(part_type):
            result += text
        return result

    async def _collect_fragments(
            self, part_type: AgentMessagePart.VALID_TYPES):
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

    async def stream_parts(self) -> cl_abc.AsyncGenerator[AgentMessagePart]:
        """Asynchronously iterate over parts as they arrive."""
        async for part in self._parts.stream():
            yield part

    @property
    async def model(self) -> mdl.AgentMessage:
        tool_calls = [tool_call.model for tool_call in await self.tool_calls]
        errors = [f"Error: {exc}" for exc in await self.errors]
        return mdl.AgentMessage(
            metadata=await self._metadata_model, content=await self.content,
            reasoning=await self.reasoning, tool_calls=tool_calls,
            errors=errors)

    @classmethod
    def from_model(cls, model: mdl.AgentMessage) -> t.Self:
        parts: list[AgentMessagePart] = [
            AgentMessageContentPart([model.content])]
        if model.reasoning:
            parts.append(AgentMessageReasoningPart([model.reasoning]))
        tool_calls = []
        for tool_call in model.tool_calls:
            function = ToolCallFunction(
                tool_call.function.name, tool_call.function.arguments)
            tool_calls.append(ToolCall(tool_call.id, function))
        if tool_calls:
            parts.append(AgentMessageToolPart(tool_calls))
        if model.errors:
            parts.append(
                AgentMessageErrorPart([Exception(e) for e in model.errors]))
        return cls(cls._metadata_from_model(model), util.StreamableList(parts))
