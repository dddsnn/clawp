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

import asyncio
import collections.abc as cl_abc
import functools as ft
import json
import logging
import pathlib
import typing as t
import uuid

import whenever as we

import message as msg
import model as mdl
import store
import tool
import util

if t.TYPE_CHECKING:
    import provider as prov


async def _read_message_file(file_name: str) -> str:
    messages_dir = pathlib.Path(__file__).parent.parent / "messages"
    return await asyncio.to_thread(_read_file, messages_dir / file_name)


def _read_file(path: pathlib.Path) -> str:
    with path.open() as f:
        return f.read()


class Session:
    """
    Session with an assistant.

    The session essentially encapsulates the assistant's context window. It can
    add messages to the context, generate assistant responses using its
    provider, and also manages which tools the assistant has available via the
    MCP client.

    The session is an asynchronous context manager that loads existing messages
    from the store on aenter and also ensures all assistant messages have
    finished streaming when it shuts down.
    """
    def __init__(
            self, session_seq: int, *,
            message_store: store.SessionMessageStore,
            provider: "prov.Provider", mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._session_seq = session_seq
        self._message_store = message_store
        self._provider = provider
        self._mcp_client = mcp_client
        self._messages = None
        self._is_shut_down = False
        self._num_incomplete_messages = 0
        self._message_wait_condition = asyncio.Condition()
        self._lock = asyncio.Lock()
        self._publisher = util.Publisher()

    async def __aenter__(self) -> t.Self:
        self._messages = await self._message_store.read_session_messages()
        await self._publisher.__aenter__()
        return self

    async def __aexit__(self, *args) -> bool:
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
        await self._publisher.__aexit__(*args)
        return False

    async def add_simple_message(
            self, message_class: t.Literal[msg.DeveloperMessage,
                                           msg.SystemMessage, msg.UserMessage],
            message_content: str, channel: mdl.ChannelDescriptor) -> None:
        """
        Add a message to the session.

        Adds the message to the session and sets the relevant metadata. For
        user messages, also prepends the system message containing metadata.

        This only adds the message, it doesn't make any API calls or return
        anything.
        """
        assert message_class in (
            msg.DeveloperMessage, msg.SystemMessage, msg.UserMessage)
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't process more messages")
            if message_class is msg.UserMessage:
                # Offset the seq_in_session by one to make space for the
                # metadata system message we need to add before it.
                message = self._make_message(
                    message_class, message_content, seq_in_session_offset=1,
                    channel=channel)
                await self._add_metadata_for_user_message(message)
            else:
                message = self._make_message(
                    message_class, message_content, channel=channel)
            await self._append_message(message)

    def _make_message(
            self, message_class, *args, seq_in_session_offset=0,
            set_time_to_now=True, channel=None, **kwargs):
        if set_time_to_now:
            time = util.ImmediateValue(we.Instant.now())
        else:
            time = util.FutureValue()
        if channel:
            channel = util.ImmediateValue(channel)
        else:
            channel = util.FutureValue()
        metadata = msg.MessageMetadata(
            seq_in_session=len(self._messages) + seq_in_session_offset,
            time=time, channel=channel)
        return message_class(metadata, *args, **kwargs)

    async def _add_metadata_for_user_message(self, user_message):
        time = await user_message.metadata.time.value
        formatted_time = time.format_iso(unit="millisecond")
        channel = await user_message.metadata.channel.value
        header_dict = {
            "seq_in_session": user_message.metadata.seq_in_session,
            "time": formatted_time,
            "channel": channel.model_dump(),}
        message_template = await _read_message_file(
            "message_metadata.template")
        message_content = message_template.format(
            metadata_json=json.dumps(header_dict, separators=(',', ':')))
        message = self._make_message(
            msg.SystemMessage, message_content,
            channel=mdl.SystemChannelDescriptor())
        await self._append_message(message)

    async def _append_message(self, message):
        self._messages.append(message)
        # First, publish the message, so clients streaming it can get it before
        # it has fully arrived. Only then append it to the message store, which
        # requires the message to have finished streaming.
        await self._publisher.append(message)
        await self._message_store.append_message(message)
        return message

    async def request_response(self) -> None:
        """
        Request an assistant response.

        Calls the provider to generate one or more AssistantMessages in
        response to the current state of the session. Handles any tool calls
        the assistant makes.

        Generated messages are not returned directly but can be accessed via
        subscribe().
        """
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't make requests")
            await self._request_assistant_messages()

    async def _request_assistant_messages(self):
        while True:
            assistant_message = await self._request_assistant_message()
            if not await assistant_message.tool_calls:
                return
            for tool_call in await assistant_message.tool_calls:
                self._logger.debug(f"Handling tool call {tool_call}.")
                try:
                    arguments_dict = json.loads(tool_call.function.arguments)
                    result = await self._mcp_client.call_tool(
                        tool_call.function.name, arguments_dict)
                    await self._append_message(
                        self._make_message(
                            msg.ToolMessage, content=str(result.data),
                            tool_call_id=tool_call.id,
                            channel=mdl.SystemChannelDescriptor()))
                except Exception as e:
                    await self._append_message(
                        self._make_message(
                            msg.ToolMessage,
                            content="Error in tool call: " + str(e),
                            tool_call_id=tool_call.id,
                            channel=mdl.SystemChannelDescriptor()))
                    self._logger.exception("Error in tool call.")

    async def _request_assistant_message(self):
        assistant_message_parts = util.StreamableList()
        message_stream_task = (
            await self._provider.stream_assistant_message(
                assistant_message_parts, self._messages,
                self._mcp_client.tools.values()))
        assistant_message = await self._append_message(
            self._make_message(
                msg.AssistantMessage, assistant_message_parts,
                set_time_to_now=False))
        self._num_incomplete_messages += 1
        asyncio.create_task(
            self._wait_for_message_completion(
                message_stream_task, assistant_message))
        return assistant_message

    async def _wait_for_message_completion(self, message_stream_task, message):
        try:
            await message_stream_task
            # The message itself also has tasks that need to finish.
            await message.wait_finalized()
        except (Exception, asyncio.CancelledError):
            self._logger.exception(
                "Error streaming assistant message, the message may be empty "
                "or incomplete.")
        finally:
            self._num_incomplete_messages -= 1
            async with self._message_wait_condition:
                self._message_wait_condition.notify()

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """Subscribe to messages in this session."""
        return self._publisher.subscribe()


class Consciousness:
    """
    A consciousness of an assistant.

    A consiousness manages the active session. It represents the continuation
    of multiple sessions that maintain the assistant's personality and
    knowledge. New sessions may be started (e.g. for compaction), but the
    assistant should maintain their core memories and personality throughout.

    Since sessions are essentially append-only, when the history has to be
    changed for a compaction or change in system message, a new session is
    started.

    A consciousness is an asynchronous context manager that ensures sessions
    are properly opened and closed.
    """
    def __init__(
            self, consciousness_id: uuid.UUID, *,
            message_store: store.ConsciousnessMessageStore,
            provider: "prov.Provider", mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._consciousness_id = consciousness_id
        self._message_store = message_store
        self._session_factory = ft.partial(
            Session, provider=provider, mcp_client=mcp_client)
        self._session = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> t.Self:
        async with self._lock:
            await self._ensure_active_session()
            return self

    async def __aexit__(self, *args) -> bool:
        async with self._lock:
            return await self._session.__aexit__(*args)

    def _make_session(self, session_seq: int) -> Session:
        message_store = self._message_store.get_session_message_store(
            session_seq)
        return self._session_factory(session_seq, message_store=message_store)

    async def _ensure_active_session(self):
        active_session_seq = self._message_store.get_active_session_seq()
        if active_session_seq is not None:
            self._session = self._make_session(active_session_seq)
            await self._session.__aenter__()
        else:
            self._logger.info(
                f"Existing consciousness {self._consciousness_id} has no "
                "sessions. Starting the first one.")
            self._session = self._make_session(0)
            await self._start_new_session()

    async def _start_new_session(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
        self._session = self._make_session(0)
        await self._session.__aenter__()
        await self._session.add_simple_message(
            msg.DeveloperMessage, await _read_message_file("init_system.md"),
            mdl.SystemChannelDescriptor())
        # Tell the assistant that this is a new session.
        await self._session.add_simple_message(
            msg.SystemMessage, await
            _read_message_file("session_initialization.template"),
            mdl.SystemChannelDescriptor())
        await self._session.add_simple_message(
            msg.SystemMessage, await
            _read_message_file("channel_web_ui.template"),
            mdl.SystemChannelDescriptor())
        await self._session.add_simple_message(
            msg.SystemMessage, await
            _read_message_file("channel_system.template"),
            mdl.SystemChannelDescriptor())

    async def process_user_message(
            self, message_content: str, channel: mdl.ChannelDescriptor):
        """
        Process a user message in the current session.

        Adds the message to the active session and requests an assistant
        response to it which.
        """
        async with self._lock:
            await self._session.add_simple_message(
                msg.UserMessage, message_content, channel)
            await self._session.request_response()

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """Subscribe to messages in this consciousness."""
        return self._session.subscribe()


class Assistant:
    """
    An assistant.

    An assistant represents a kind of personality for the user to interact
    with, defined through the initial files that the assistant is shown in the
    beginning. An assistant can have multiple consciousnesses, essentially
    copies of the assistant that are independent.
    """
    def __init__(
            self, assistant_id: uuid.UUID, *,
            message_store: store.AssistantMessageStore,
            provider: "prov.Provider", mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._assistant_id = assistant_id
        self._message_store = message_store
        self._consciousness_factory = ft.partial(
            Consciousness, provider=provider, mcp_client=mcp_client)
        self._consciousnesses = {}

    async def __aenter__(self) -> t.Self:
        self._consciousnesses.clear()
        await self._init_consciousnesses()
        return self

    async def __aexit__(self, *args) -> bool:
        exit_tasks = set()
        for consciousness in self._consciousnesses.values():
            exit_tasks.add(asyncio.create_task(consciousness.__aexit__(*args)))
            try:
                await asyncio.wait(exit_tasks, timeout=120)
            except Exception:
                self._logger.exception("Error shutting down consciousnesses.")
        return False

    def _make_consciousness(
            self, consciousness_id: uuid.UUID) -> Consciousness:
        message_store = self._message_store.get_consciousness_message_store(
            consciousness_id)
        return self._consciousness_factory(
            consciousness_id, message_store=message_store)

    async def _init_consciousnesses(self):
        for consciousness_id in self._message_store.list_consciousnesses():
            await self._add_consciousness(consciousness_id)
        if not self._consciousnesses:
            consciousness_id = uuid.uuid4()
            self._logger.info(
                "No existing consciousnesses, adding new one with ID "
                f"{consciousness_id}.")
            await self._add_consciousness(consciousness_id)

    async def _add_consciousness(self, consciousness_id):
        assert consciousness_id not in self._consciousnesses
        consciousness = self._make_consciousness(consciousness_id)
        self._consciousnesses[consciousness_id] = (
            await consciousness.__aenter__())

    async def process_user_message(
            self, consciousness_id: uuid.UUID, message_content: str,
            channel: mdl.ChannelDescriptor):
        """Process and respond to a user message in the consciousness."""
        consciousness = self._consciousnesses[consciousness_id]
        await consciousness.process_user_message(message_content, channel)

    def subscribe(
            self,
            consciousness_id: uuid.UUID) -> cl_abc.AsyncGenerator[msg.Message]:
        """
        Subscribe to messages in the consciousness.

        Raises a KeyError if the consciousness doesn't exist.
        """
        return self._consciousnesses[consciousness_id].subscribe()
