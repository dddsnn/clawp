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
import whenever as we

if t.TYPE_CHECKING:
    import provider as prov

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
    async def time(self) -> t.Awaitable[we.Instant]:
        """The time the message was fully received."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def role(self) -> MessageRole:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    async def content(self) -> t.Awaitable[str]:
        """The full content of the message."""
        raise NotImplementedError


class SimpleMessage(Message):
    def __init__(
            self, metadata: MessageMetadata, role: MessageRole,
            content: str) -> None:
        super().__init__(metadata)
        if role not in t.get_args(MessageRole):
            raise ValueError(f"invalid role {role}")
        self._time = we.Instant.now()
        self._role = role
        self._content = content

    @property
    async def time(self) -> t.Awaitable[we.Instant]:
        return self._time

    @property
    def role(self) -> MessageRole:
        return self._role

    @property
    async def content(self) -> t.Awaitable[str]:
        return self._content


class SystemMessage(SimpleMessage):
    def __init__(self, metadata: MessageMetadata, content: str) -> None:
        super().__init__(metadata, "system", content)


class DeveloperMessage(SimpleMessage):
    def __init__(self, metadata: MessageMetadata, content: str) -> None:
        super().__init__(metadata, "developer", content)


class UserMessage(SimpleMessage):
    """Message sent by the user."""
    def __init__(self, metadata: MessageMetadata, content: str) -> None:
        super().__init__(metadata, "user", content)


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


class StreamableList:
    """
    A list that can be streamed asynchronously.

    __bool__, __getitem__, and __iter__ work on the underlying list.

    The stream() generator can be asynchronously iterated over, yielding
    elements as they are added via append(). The generator keeps waiting for
    new elements until finalize() is called.

    After finalize() is called, no more elements can be added. finalize() must
    be called eventually so that the task waiting for it can finish.
    """
    def __init__(self):
        self._list = []
        self._new_element_condition = asyncio.Condition()
        self._num_readers = 0
        self._num_readers_condition = asyncio.Condition()
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
        """
        Append an element.

        The list must not be finalized, or a ValueError is raised.
        """
        if self._finalized_event.is_set():
            raise ValueError("StreamableList has already been finalized")
        self._list.append(item)
        async with self._new_element_condition:
            self._new_element_condition.notify_all()

    async def finalize(self, compact=None) -> None:
        """
        Finalize the list.

        This puts the stream into a read-only state (any appends() will now
        raise exceptions), and stops the iteration of any asynchronous streams
        (via stream()).

        :param compact: An optional function to make the list more compact
            (e.g. by concatenating strings). This will be given the underlying
            list and must return the compacted list.
        """
        self._finalized_event.set()
        if compact:
            async with self._num_readers_condition:
                await self._num_readers_condition.wait_for(
                    lambda: self._num_readers == 0)
                self._list = compact(self._list)

    async def wait_finalized(self) -> None:
        """
        Wait until the list has been finalized.

        When the list is finalized, no new elements can be added.
        """
        await self._finalized_wait_task

    async def stream(self) -> cl_abc.AsyncGenerator:
        """
        Asynchronously stream list elements.

        Existing elements are yielded, as well as new ones added via append().
        Once the list is finalized and no more elements can be added, the
        generator exits.
        """
        try:
            self._num_readers += 1
            i = 0
            while True:
                if i < len(self._list):
                    yield self._list[i]
                    i += 1
                    continue
                elif self._finalized_event.is_set():
                    return
                new_element_wait_task = asyncio.create_task(
                    self._wait_for_new_element())
                await asyncio.wait(
                    {new_element_wait_task, self._finalized_wait_task},
                    return_when=asyncio.FIRST_COMPLETED)
                new_element_wait_task.cancel()
        finally:
            self._num_readers -= 1
            assert self._num_readers >= 0
            async with self._num_readers_condition:
                self._num_readers_condition.notify_all()

    async def _wait_for_new_element(self):
        async with self._new_element_condition:
            await self._new_element_condition.wait()


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


class AssistantMessagePart:
    """
    One part of an AssistantMessage.

    AssistantMessageParts consist of fragments that can be streamed. The type
    of these fragments depends on the type of part.
    """
    VALID_TYPES = t.Literal["content", "error", "reasoning", "tool"]

    def __init__(self, part_type: VALID_TYPES):
        if part_type not in t.get_args(self.VALID_TYPES):
            raise ValueError(f"invalid type {part_type}")
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

    async def finalize(self) -> None:
        await self._fragments.finalize()


class AssistantMessageTextPart(AssistantMessagePart):
    VALID_TYPES = t.Literal["content", "reasoning"]

    def __init__(self, part_type: VALID_TYPES):
        super().__init__(part_type)

    async def append(self, text: str) -> None:
        await super().append(text)

    async def stream_fragments(self) -> cl_abc.AsyncGenerator[str]:
        async for fragment in self._fragments.stream():
            yield fragment

    async def finalize(self) -> None:
        await self._fragments.finalize(compact=lambda list_: ["".join(list_)])


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
            self, metadata: MessageMetadata, parts: StreamableList) -> None:
        super().__init__(metadata)
        self._parts = parts
        self._time = None
        self._set_time_task = asyncio.create_task(self._set_time())

    async def _set_time(self):
        await self._parts.wait_finalized()
        self._time = we.Instant.now()
        self._set_time_task = None

    @property
    async def time(self) -> t.Awaitable[we.Instant]:
        if self._set_time_task:
            await self._set_time_task
        return self._time

    @property
    def role(self) -> t.Literal["assistant"]:
        return "assistant"

    @property
    async def content(self) -> t.Awaitable[str]:
        """The final content of the message (the one that "counts")."""
        return await self._concat_part_text("content")

    @property
    async def reasoning(self) -> t.Awaitable[str]:
        """The reasoning of the assistant in producing the content."""
        return await self._concat_part_text("reasoning")

    async def _concat_part_text(
            self, part_type: AssistantMessageTextPart.VALID_TYPES):
        await self._parts.wait_finalized()
        text = ""
        for part in self._parts:
            if part.type != part_type:
                continue
            async for fragment in part.stream_fragments():
                text += fragment
        return text

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
        """Asynchronously iterate over parts as they arrive."""
        async for part in self._parts.stream():
            yield part


class Session:
    """
    Session with an assistant.

    The session essentially encapsulates the assistant's context window. It can
    generate responses to new user messages using it's provider, and also
    manages which tools the assistant has available via the MCP client.

    The session is an asynchronous context manager that ensures all assistant
    messages have finished streaming when it shuts down.
    """
    def __init__(
            self, provider: "prov.Provider", mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._provider = provider
        self._mcp_client = mcp_client
        self._messages: list[Message] = []
        self._is_shut_down = False
        self._num_incomplete_messages = 0
        self._message_wait_condition = asyncio.Condition()
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *_) -> bool:
        async with self._lock:
            self._is_shut_down = True
            if self._num_incomplete_messages:
                self._logger.info(
                    f"Waiting for {self._num_incomplete_messages} messages to "
                    "finish streaming before shutdown.")
            try:
                async with asyncio.timeout(120), self._message_wait_condition:
                    await self._message_wait_condition.wait_for(
                        lambda: self._num_incomplete_messages == 0)
            except asyncio.TimeoutError:
                self._logger.warning(
                    "Timeout waiting for incomplete messages.")
        return False

    async def process_user_message(
            self, user_message_content: str
    ) -> cl_abc.AsyncGenerator[AssistantMessage]:
        """
        Process and respond to a user message.

        This prepares the context with the new user message and calls the
        provider to generate one or more AssistantMessages in response. Handles
        any tool calls the assistant makes.
        """
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't process more messages")
            self._messages.append(
                UserMessage(
                    MessageMetadata(len(self._messages)),
                    user_message_content))
            async for assistant_message in self._request_assistant_messages():
                yield assistant_message

    async def _request_assistant_messages(self):
        while True:
            assistant_message = await self._provider.request_assistant_message(
                MessageMetadata(len(self._messages)), self._messages,
                self._mcp_client.tools.values())
            self._num_incomplete_messages += 1
            asyncio.create_task(self._wait_for_message_time(assistant_message))
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
                            MessageMetadata(len(self._messages)),
                            content=str(result.data),
                            tool_call_id=tool_call.id))
                except Exception as e:
                    self._messages.append(
                        ToolMessage(
                            MessageMetadata(len(self._messages)),
                            content="Error in tool call: " + str(e),
                            tool_call_id=tool_call.id))
                    self._logger.exception("Error in tool call.")

    async def _wait_for_message_time(self, message):
        # Wait for the message to finish streaming, at which point it get its
        # time property set.
        await message.time
        self._num_incomplete_messages -= 1
        async with self._message_wait_condition:
            self._message_wait_condition.notify()
