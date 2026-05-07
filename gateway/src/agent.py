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

import channel as chan
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


# TODO give each session a reason, e.g. "root session", "compaction", "change in md files" etc.?+++++++
class Session:
    """
    Session with an agent.

    The session essentially encapsulates the agent's context window. It can add
    messages to the context, generate agent responses using its provider, and
    also manages which tools the agent has available via the MCP client.

    The session is an asynchronous context manager that loads existing messages
    from the store on aenter and also ensures all agent messages have finished
    streaming when it shuts down.

    A session can only handle one request at a time (adding messages or
    requesting a response). Concurrent calls will block until the current one
    is done.
    """

    # REFACTOR narrower interface for channel repo?++++++++
    def __init__(
            self, session_seq: int, *,
            message_store: store.SessionMessageStore,
            channel_repo: chan.ChannelRepository, provider: "prov.Provider",
            mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._session_seq = session_seq
        self._message_store = message_store
        self._channel_repo = channel_repo
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

    # TODO deprecate++++++++
    # REFACTOR use channels in here as well?++++++
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

    # REFACTOR the set_time_to_now and channel handling, and actually the whole
    # message construction business in here++++++++++++++
    # REFACTOR take a whole ReceivedMessageMetadata in here?+++++++
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
        Request an agent response.

        Calls the provider to generate one or more AgentMessages in response to
        the current state of the session. Handles any tool calls the agent
        makes, and also gives the agent feedback if they forgot the channel
        header or it was malformed.

        Generated messages are not returned directly but can be accessed via
        subscribe().
        """
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't make requests")
            do_request = True
            while do_request:
                # TODO the asst sometimes needs to make multiple calls in succession,
                # but sometimes gets stuck in a loop. we need some looping prevention+++++
                # TODO we may also want a timeout for a single message request, since
                # this will block shutdown for as long as it's going on. but then how do
                # we handle a partially complete message? it won't go into storage at all, right?+++++++++++
                message = await self._request_agent_message()
                # Wait for the message to completely arrive before handling
                # tool calls etc.
                await message.wait_finalized()
                do_request = await self._check_channel_header(message)
                do_request |= await self._handle_tool_calls(message)

    async def _request_agent_message(self):
        parts = util.StreamableList()
        await self._provider.stream_agent_message(
            parts, self._messages, self._mcp_client.tools.values())
        return await self._append_message(
            self._make_message(msg.AgentMessage, parts, set_time_to_now=False))

    # TODO maybe it would be better to always refuse to send, and follow up with
    # a question which channel the message was meant for? i.e. asst sends with
    # missing/invalid channel header, system asks "you forgot the header, where
    # should this go? answer on the system channel"+++++++++++++
    async def _check_channel_header(self, message: msg.AgentMessage) -> bool:
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
                        "Agent omitted channel header, message will be sent "
                        f"to {channel.fallback_channel} instead.")
                    template = await _read_message_file(
                        "missing_channel_header.template")
                    system_message_content = template.format(
                        fallback_channel=channel.fallback_channel
                        .model_dump_json())
                    break
            else:
                self._logger.warning(
                    "Agent omitted channel header, but no fallback channel "
                    "could be determined, message will not be sent.")
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

    async def _handle_tool_calls(self, message: msg.AgentMessage) -> bool:
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


# TODO arch:
# - within a session, can communicate via different channels with different people,
#   also group chats etc. using either a send message tool or message header
# - without message header, agent messages go the last used channel/recipient
# - agent or user can start new sessions
# - only one session active at a time
# TODO we need to check that the set of tools available to the assistant remains
# unchanged, or else notify the assistant, else they may get confused++++++++
# TODO the llm can get incredibly confused if we take tools away that were present
# previously (getting stuck in a loop hallucinating). we probably have to send a
# system message informing it that a tool is no longer available
# PERF to maintain cache, we may want to temporarily add system messages saying
# "this has changed" instead of rewriting the whole context and causing
# cache invalidations++++++++++
class Agent:
    """
    An agent.

    An agent manages a sequence of sessions that maintain the agent's
    personality and knowledge. It always has an active session, which
    represents the model's current context window. New sessions may be started
    (e.g. for compaction), but the agent should maintain their core memories
    and personality throughout.

    Since sessions are essentially append-only, when the history has to be
    changed for a compaction or change in system message, a new session is
    started.

    An agent is an asynchronous context manager that ensures sessions are
    properly opened and closed.
    """
    def __init__(
            self, agent_id: uuid.UUID, *,
            message_store: store.AgentMessageStore,
            channel_repo: chan.ChannelRepository, provider: "prov.Provider",
            mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._agent_id = agent_id
        self._message_store = message_store
        self._channel_repo = channel_repo
        self._session_factory = ft.partial(
            Session, channel_repo=channel_repo, provider=provider,
            mcp_client=mcp_client)
        self._session = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> t.Self:
        async with self._lock:
            self._read_channel_messages_task = asyncio.create_task(
                self._read_channel_messages())
            await self._ensure_active_session()
            return self

    async def __aexit__(self, *args) -> bool:
        async with self._lock:
            self._read_channel_messages_task.cancel()
            try:
                async with asyncio.timeout(120):
                    return await self._session.__aexit__(*args)
            except Exception:
                self._logger.exception("Error shutting down session.")
            # TODO exc handling, timeout
            await self._read_channel_messages_task
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
                f"Existing agent {self._agent_id} has no sessions. Starting "
                "the first one.")
            self._session = self._make_session(0)
            await self._start_new_session()

    # TODO this only ever starts a session with seq 0. when we implement session restarts
    # for compactions etc. this seq needs to increase. also, we need to give
    # the actual reason for the session start in the system info message+++++++++++++
    async def _start_new_session(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
        self._session = self._make_session(0)
        await self._session.__aenter__()
        await self._channel_repo.add_incoming_developer_message(
            await _read_message_file("init_system.md"))
        # Tell the agent that this is a new session.
        await self._channel_repo.add_incoming_system_message(
            await _read_message_file("session_initialization.txt"))
        await self._channel_repo.add_incoming_system_message(
            await _read_message_file("channel_web_ui.txt"))
        await self._channel_repo.add_incoming_system_message(
            await _read_message_file("channel_system.txt"))

    # TODO handle cancel gracefully+++++++++++++
    async def _read_channel_messages(self) -> None:
        async for channel_message in self._channel_repo.incoming_messages():
            async with self._lock:
                if channel_message.role == "developer":
                    message_class = msg.DeveloperMessage
                elif channel_message.role == "system":
                    message_class = msg.SystemMessage
                elif channel_message.role == "user":
                    message_class = msg.UserMessage
                else:
                    # TODO handle agent messages? tool?++++++++++
                    raise ValueError(
                        "unable to handle message role "
                        f"{channel_message.role}")
                # TODO actually use the time from the channel message++++++++++
                await self._session.add_simple_message(
                    message_class, channel_message.content, await
                    channel_message.metadata.channel.value)
                if channel_message.request_response:
                    await self._session.request_response()

    # TODO deprecate+++++++++
    async def process_user_message(
            self, message_content: str,
            channel: mdl.ChannelDescriptor) -> None:
        """
        Process a user message in the current session.

        Adds the message to the active session and requests an agent response
        to it. The response is not returned, rather it is available via
        subscribe().
        """
        async with self._lock:
            await self._session.add_simple_message(
                msg.UserMessage, message_content, channel)
            await self._session.request_response()

    # TODO deprecate+++++++++
    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """
        Subscribe to the this agent's messages.

        This includes all kinds of messages, also user/system/developer/tool
        messages.
        """
        # TODO handle session rollover++++++
        return self._session.subscribe()
