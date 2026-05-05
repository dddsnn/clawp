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

    A session can only handle one request at a time (adding messages or
    requesting a response). Concurrent calls will block until the current one
    is done.
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
        self._lock = asyncio.Lock()
        self._is_shut_down = False
        self._publisher = util.Publisher()

    async def __aenter__(self) -> t.Self:
        self._messages = await self._message_store.read_session_messages()
        await self._publisher.__aenter__()
        return self

    async def __aexit__(self, *args) -> bool:
        async with self._lock:
            # Now that we've acquired the lock, we can be sure all message
            # streaming is done. Prevent any subsequent requests by setting the
            # shutdown flag.
            self._is_shut_down = True
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
        the assistant makes, and also gives the assistant feedback if they
        forgot the channel header or it was malformed.

        Generated messages are not returned directly but can be accessed via
        subscribe().
        """
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't make requests")
            do_request = True
            while do_request:
                assistant_message = await self._request_assistant_message()
                # Wait for the message to completely arrive before handling
                # tool calls etc.
                await assistant_message.wait_finalized()
                do_request = await self._check_channel_header(
                    assistant_message)
                do_request |= await self._handle_tool_calls(assistant_message)

    async def _request_assistant_message(self):
        parts = util.StreamableList()
        await self._provider.stream_assistant_message(
            parts, self._messages, self._mcp_client.tools.values())
        return await self._append_message(
            self._make_message(
                msg.AssistantMessage, parts, set_time_to_now=False))

    async def _check_channel_header(
            self, message: msg.AssistantMessage) -> bool:
        if not await message.content:
            # No content, in this case we don't need a header.
            return False
        channel = await message.metadata.channel.value
        if isinstance(channel, mdl.MissingChannelDescriptor):
            # The channel header is missing so we use the last used user
            # channel.
            for message in reversed(self._messages):
                if isinstance(message, msg.UserMessage):
                    channel.fallback_channel = (
                        await message.metadata.channel.value)
                    self._logger.info(
                        "Assistant omitted channel header, message will be "
                        f"sent to {channel.fallback_channel} instead.")
                    template = await _read_message_file(
                        "missing_channel_header.template")
                    system_message_content = template.format(
                        fallback_channel=channel.fallback_channel
                        .model_dump_json())
                    break
            else:
                self._logger.warning(
                    "Assistant omitted channel header, but no fallback "
                    "channel could be determined, message will not be sent.")
                system_message_content = await _read_message_file(
                    "missing_channel_header_no_fallback.txt")
            await self._append_message(
                self._make_message(
                    msg.SystemMessage, content=system_message_content,
                    channel=mdl.SystemChannelDescriptor()))
            return True
        elif isinstance(channel, mdl.MalformedChannelDescriptor):
            template = await _read_message_file(
                "malformed_channel_header.template")
            system_message_content = template.format(
                error_message=channel.error_message)
            await self._append_message(
                self._make_message(
                    msg.SystemMessage, content=system_message_content,
                    channel=mdl.SystemChannelDescriptor()))
            return True
        return False

    async def _handle_tool_calls(self, message: msg.AssistantMessage) -> bool:
        if not await message.tool_calls:
            return False
        for tool_call in await message.tool_calls:
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
        return True

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """Subscribe to messages in this session."""
        return self._publisher.subscribe()


class Assistant:
    """
    An assistant.

    of multiple sessions that maintain the assistant's personality and
    knowledge. It always has an active session, which represents the model's
    current context window. New sessions may be started (e.g. for compaction),
    but the assistant should maintain their core memories and personality
    throughout.

    Since sessions are essentially append-only, when the history has to be
    changed for a compaction or change in system message, a new session is
    started.

    An assistant is an asynchronous context manager that ensures sessions are
    properly opened and closed.
    """
    def __init__(
            self, assistant_id: uuid.UUID, *,
            message_store: store.AssistantMessageStore,
            provider: "prov.Provider", mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._assistant_id = assistant_id
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
            try:
                async with asyncio.timeout(120):
                    return await self._session.__aexit__(*args)
            except Exception:
                self._logger.exception("Error shutting down session.")
        return False

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
                f"Existing assistant {self._assistant_id} has no sessions. "
                "Starting the first one.")
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
            _read_message_file("session_initialization.txt"),
            mdl.SystemChannelDescriptor())
        await self._session.add_simple_message(
            msg.SystemMessage, await _read_message_file("channel_web_ui.txt"),
            mdl.SystemChannelDescriptor())
        await self._session.add_simple_message(
            msg.SystemMessage, await _read_message_file("channel_system.txt"),
            mdl.SystemChannelDescriptor())

    async def process_user_message(
            self, message_content: str,
            channel: mdl.ChannelDescriptor) -> None:
        """
        Process a user message in the current session.

        Adds the message to the active session and requests an assistant
        response to it. The response is not returned, rather it is available
        via subscribe().
        """
        async with self._lock:
            await self._session.add_simple_message(
                msg.UserMessage, message_content, channel)
            await self._session.request_response()

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """
        Subscribe to the this assistant's messages.

        This includes all kinds of messages, also user/system/developer/tool
        messages.
        """
        return self._session.subscribe()
