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
import typing as t
import uuid

import whenever as we

from . import channel as chan
from . import message as msg
from . import model as mdl
from . import store, tool, util

if t.TYPE_CHECKING:
    from . import provider as prov


class Session:
    """
    Session with an agent.

    The session essentially encapsulates the agent's context window. It can add
    messages to the context, generate agent responses using its provider, and
    also manages which tools the agent has available via the MCP client.

    The session is an asynchronous context manager that loads existing messages
    from the store on aenter and also ensures all agent messages have finished
    streaming when it shuts down.

    A session can only handle one request at a time (incoming message or
    requesting a response). Concurrent calls will block until the current one
    is done.
    """
    def __init__(
            self, session_seq: int, *,
            message_store: store.SessionMessageStore,
            message_sender: chan.MessageSender, provider: "prov.Provider",
            mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._session_seq = session_seq
        self._message_store = message_store
        self._message_sender = message_sender
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

    async def add_incoming_message(
            self, incoming_message: chan.IncomingMessage) -> None:
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't process more messages")
            if incoming_message.role == "developer":
                message_class = msg.DeveloperMessage
            elif incoming_message.role == "system":
                message_class = msg.SystemMessage
            elif incoming_message.role == "user":
                message_class = msg.UserMessage
                await self._add_metadata_for_user_message(incoming_message)
            else:
                raise ValueError(
                    "unable to handle message role "
                    f"{incoming_message.role}")
            metadata = self._make_metadata(
                incoming_message.metadata.time,
                incoming_message.metadata.channel)
            message = message_class(metadata, content=incoming_message.content)
            await self._append_message(message)

    async def _add_metadata_for_user_message(
            self, user_message: chan.IncomingMessage):
        time = await user_message.metadata.time.value
        formatted_time = time.format_iso(unit="millisecond")
        channel = await user_message.metadata.channel.value
        # The user message will be the one right after the system message with
        # the metadata, so seq_in_session should be one more.
        header_dict = {
            "seq_in_session": len(self._messages) + 1,
            "time": formatted_time,
            "channel": channel.model_dump(),}
        message_content = await util.render_message_template(
            "message_metadata.md",
            metadata_json=json.dumps(header_dict, separators=(',', ':')))
        await self._append_message_now(
            msg.SystemMessage, content=message_content)

    async def _append_message_now(self, message_class, **kwargs):
        metadata = self._make_metadata(
            we.Instant.now(), mdl.SystemChannelDescriptor())
        message = message_class(metadata, **kwargs)
        await self._append_message(message)

    def _make_metadata(
        self,
        time: we.Instant | util.Value[we.Instant],
        channel: mdl.IncomingChannelDescriptor
        | util.Value[mdl.OutgoingChannelDescriptor],
    ) -> msg.MessageMetadata:
        if not isinstance(time, util.Value):
            time = util.ImmediateValue(time)
        if not isinstance(channel, util.Value):
            channel = util.ImmediateValue(channel)
        return msg.MessageMetadata(
            seq_in_session=len(self._messages), time=time, channel=channel)

    async def _append_message(self, message):
        self._messages.append(message)
        # First, publish the message, so clients streaming it can get it before
        # it has fully arrived. Only then append it to the message store, which
        # requires the message to have finished streaming.
        await self._publisher.append(message)
        await self._message_store.append_message(message)

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
        metadata = self._make_metadata(util.FutureValue(), util.FutureValue())
        message = msg.AgentMessage(metadata, parts)
        await self._append_message(message)
        await self._message_sender.send(message)
        return message

    async def _check_channel_header(
            self, agent_message: msg.AgentMessage) -> bool:
        if not await agent_message.content:
            # No content, in this case we don't need a header.
            return False
        channel = await agent_message.metadata.channel.value
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
                    system_message_content = (
                        await util.render_message_template(
                            "system_information", "missing_channel_header.md",
                            fallback_channel=channel.fallback_channel
                            .model_dump_json()))
                    break
            else:
                self._logger.warning(
                    "Agent omitted channel header, but no fallback channel "
                    "could be determined, message will not be sent.")
                system_message_content = await util.render_message_template(
                    "system_information",
                    "missing_channel_header_no_fallback.md")
            await self._append_message_now(
                msg.SystemMessage, content=system_message_content)
            return True
        elif isinstance(channel, mdl.MalformedChannelDescriptor):
            system_message_content = await util.render_message_template(
                "system_information", "malformed_channel_header.md",
                error_message=channel.error_message)
            await self._append_message_now(
                msg.SystemMessage, content=system_message_content)
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
                await self._append_message_now(
                    msg.ToolMessage, content=str(result.data),
                    tool_call_id=tool_call.id)
            except Exception as e:
                await self._append_message_now(
                    msg.ToolMessage, content="Error in tool call: " + str(e),
                    tool_call_id=tool_call.id)
                self._logger.exception("Error in tool call.")
        return True

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """Subscribe to messages in this session."""
        return self._publisher.subscribe()


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
            Session, message_sender=channel_repo, provider=provider,
            mcp_client=mcp_client)
        self._session = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> t.Self:
        async with self._lock:
            self._read_incoming_messages_task = asyncio.create_task(
                self._read_incoming_messages())
            await self._ensure_active_session()
            return self

    async def __aexit__(self, *args) -> bool:
        async with self._lock:
            self._read_incoming_messages_task.cancel()
            try:
                async with asyncio.timeout(120):
                    return await self._session.__aexit__(*args)
            except Exception:
                self._logger.exception("Error shutting down session.")
            try:
                async with asyncio.timeout(20):
                    await self._read_incoming_messages_task
            except Exception:
                self._logger.exception(
                    "Error waiting for incoming message task.")
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

    async def _start_new_session(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
        self._session = self._make_session(0)
        await self._session.__aenter__()
        await self._channel_repo.system_channel.add_incoming_message(
            "developer", await util.render_message_template("init_system.md"))
        # Tell the agent that this is a new session.
        await self._channel_repo.system_channel.add_incoming_message(
            "system", await util.render_message_template(
                "system_information", "session_initialization.md"))
        # Tell the agent about available channels.
        for channel in self._channel_repo.channels.values():
            await self._channel_repo.system_channel.add_incoming_message(
                "system", await channel.channel_available_message)

    async def _read_incoming_messages(self) -> None:
        handle_task = None
        try:
            async for message in self._channel_repo.incoming_messages():
                async with self._lock:
                    handle_task = asyncio.create_task(
                        self._handle_incoming_message(message))
                    await asyncio.shield(handle_task)
        except asyncio.CancelledError:
            if not handle_task:
                return
            try:
                async with asyncio.timeout(60):
                    await handle_task
            except Exception:
                self._logger.exception(
                    "Error waiting for final incoming message.")

    async def _handle_incoming_message(
            self, message: chan.IncomingMessage) -> None:
        try:
            await self._session.add_incoming_message(message)
            if message.request_response:
                await self._session.request_response()
        except Exception:
            self._logger.exception("Error handling incoming message.")

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """
        Subscribe to the this agent's messages.

        These are all of the agent's messages in the context of its session and
        in the same order. This includes all message roles, also
        user/system/developer/tool messages.
        """
        return self._session.subscribe()
