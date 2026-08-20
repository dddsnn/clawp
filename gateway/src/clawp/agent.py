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
import contextlib
import dataclasses as dc
import functools as ft
import itertools as it
import json
import logging
import pathlib
import typing as t
import uuid

import whenever as we

from . import channel as chan
from . import file, store, tool, util
from . import message as msg
from . import model as mdl

if t.TYPE_CHECKING:
    from . import provider as prov


@dc.dataclass
class MessageInSession:
    message: msg.Message
    message_offset: mdl.MessageOffset

    def __post_init__(self) -> None:
        if not isinstance(self.message, msg.Message):
            raise ValueError("invalid message")
        if not isinstance(self.message_offset, mdl.MessageOffset):
            raise ValueError("invalid message offset")


class SessionTransaction:
    """
    Transaction proxying operations on a Session.

    The transaction is an asynchronous context manager that mutexes access to
    the Session's write operations. It's a thin proxy for the Session's public
    interface. For documentation on the methods see the Session.

    Calling methods is only valid in the entered state. The context manager
    must be used exactly once (or it won't show as completed), and it can't be
    reused.
    """
    def __init__(self, session: "Session") -> None:
        self._session = session
        self._is_active = False
        self._completed_event = asyncio.Event()

    async def __aenter__(self) -> t.Self:
        if self._completed_event.is_set():
            raise RuntimeError("transaction has already completed")
        self._is_active = True
        return self

    async def __aexit__(self, *args) -> bool:
        self._is_active = False
        self._completed_event.set()
        return False

    def is_complete(self) -> bool:
        """Check if the transaction is completed."""
        return self._completed_event.is_set()

    async def wait(self) -> None:
        """Wait until the transaction is completed."""
        await self._completed_event.wait()

    @property
    def active_chat(self) -> mdl.ChatDescriptor:
        """
        Get the active chat of the session.

        The active chat is the one outgoing agent messages are sent on.
        """
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        return self._session._active_chat

    @active_chat.setter
    def active_chat(self, value: mdl.ChatDescriptor) -> None:
        """
        Set the active chat of the session.

        Any new agent messages in the session will be sent in the new chat.
        """
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        self._session._active_chat = value

    @property
    def num_messages(self) -> int:
        """The number of messages in this session."""
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        return self._session.num_messages

    async def append_incoming_message(
            self, incoming_message: mdl.IncomingMessage) -> None:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        await self._session._append_incoming_message(incoming_message)

    async def request_responses(self) -> None:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        await self._session._request_responses()

    async def append_internal_message(
            self, message_class: type[msg.InternalMessage], content: str,
            **kwargs) -> None:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        await self._session._append_internal_message(
            message_class, content, **kwargs)

    async def append_agent_message(
            self, message_parts: util.StreamableList) -> None:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        await self._session._append_agent_message(message_parts)

    def messages(self) -> cl_abc.Generator[MessageInSession]:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        return self._session.messages()

    def subscribe(self) -> cl_abc.AsyncGenerator[MessageInSession]:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        return self._session.subscribe()


class Session:
    """
    Session with an agent.

    The session essentially encapsulates the agent's context window. It can add
    messages to the context, generate agent responses using its provider, and
    also manages which tools the agent has available via the MCP client.

    The session is an asynchronous context manager that loads existing messages
    from the store on aenter and also ensures all agent messages have finished
    streaming when it shuts down.

    For modifying operations, the session is meant to be used through the
    SessionTransaction context manager returned by transaction(). Only one of
    those is returned at a time, the previous one has to complete for the next
    one to be returned. This ensures that operations that add more than one
    message don't write concurrently, which might lead to their messages being
    interleaved in a nonsensical way.

    Pure read operations (e.g. messages()) can be done on the session itself.
    """
    def __init__(
            self, session_seq: int, *, model_config: mdl.ModelConfig,
            message_store: store.SessionMessageStore,
            message_sender: chan.MessageSender, provider: "prov.Provider",
            mcp_client: tool.Client, active_chat: mdl.ChatDescriptor) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._session_seq = session_seq
        self._model_config = model_config
        self._message_store = message_store
        self._message_sender = message_sender
        self._provider = provider
        self._mcp_client = mcp_client
        self._active_chat = active_chat
        self._messages = None
        self._publisher = util.Publisher()
        self._active_transaction = None

    async def __aenter__(self) -> t.Self:
        self._messages = await self._message_store.read_session_messages()
        await self._publisher.__aenter__()
        return self

    async def __aexit__(self, *args) -> bool:
        if (self._active_transaction
                and not self._active_transaction.is_complete()):
            self._logger.debug(
                "Waiting for ongoing transaction to complete before exiting.")
            await self._active_transaction.wait()
        await self._publisher.__aexit__(*args)
        return False

    async def transaction(self) -> SessionTransaction:
        """
        Create a transaction.

        A transaction isolates a set of operations by only allowing one of them
        at a time. If there is still an active transaction, blocks until it
        completes.
        """
        if self._active_transaction:
            if not self._active_transaction.is_complete():
                self._logger.debug(
                    "Waiting for ongoing transaction to complete before "
                    "starting the next one.")
            await self._active_transaction.wait()
        self._active_transaction = SessionTransaction(self)
        return self._active_transaction

    @property
    def num_messages(self) -> int:
        """The number of messages in this session."""
        return len(self._messages or [])

    async def _append_incoming_message(
            self, incoming_message: mdl.IncomingMessage) -> None:
        """
        Append an incoming message to this session.

        The message must be a user message or a system message. For user
        messages, a system message with metadata will be generated and added to
        the session first. System messages are appended as-is.

        If the message is on the agent channel, a small reminder is added below
        the metadata to not get into an endless loop with the other agent.

        This method only adds messages, it doesn't request a response from the
        agent.
        """
        message = incoming_message.message
        if message.role == "user":
            assert isinstance(message, mdl.ChatMessage)
            await self._add_metadata_for_user_message(message)
            message = msg.UserMessage(
                msg.ChatMessageMetadata.from_model(message.metadata),
                content=message.content)
        else:
            assert isinstance(message, mdl.SystemMessage)
            message = msg.SystemMessage(
                msg.InternalMessageMetadata(time=message.metadata.time),
                content=message.content)
        await self._append_message(message)

    async def _add_metadata_for_user_message(
            self, user_message: mdl.UserMessage):
        message_content = await file.render_message_template(
            "message_metadata.txt",
            metadata_json=user_message.metadata.model_dump_json())
        if user_message.metadata.chat.channel == "agent":
            # This message will be sent to at least one other agent, remind the
            # agent on how to end the conversation.
            message_content += await file.render_message_template(
                "fragments/agent_to_agent_comm_reminder.txt")
        await self._append_internal_message(
            msg.SystemMessage, content=message_content)

    async def _append_message(self, message: msg.Message):
        """
        Append a message.

        Appends the message to the transient storage, then publishes it
        immediately, possibly before it has arrived completely. This allows
        agent messages to be streamed. Only then append the complete message to
        persistent storage. The message is guaranteed to be finalized when this
        returns.

        Cancelling this coroutine may lead to a state in which the message
        exists in transient storage and has started visibly streaming, but
        hasn't been persisted.
        """
        self._messages.append(message)
        # First, publish the message, so clients streaming it can get it before
        # it has fully arrived. Only then append it to the message store, which
        # requires the message to have finished streaming.
        await self._publisher.append(message)
        await message.wait_finalized()
        try:
            await self._message_store.append_message(message)
        except Exception:
            self._logger.exception(
                "Error storing message in persistent store. The message was "
                "added and is being processed in memory, but will likely not "
                "be present when reloading from the persistent store.")

    async def _request_responses(self) -> None:
        """
        Prompt the agent for a continuation.

        Requests an agent message from the API to continue the current state of
        the session. Any tool calls the agent makes are handled. Makes
        successive requests as long as necessary to give the agent tool results
        or report errors in sending a message.

        All agent messages are sent in the session's active chat (though the
        active chat may change at any point through a tool call by the agent).
        """
        num_requests = 0
        while num_requests < self._model_config.doom_loop_max_requests:
            try:
                async with asyncio.timeout(
                        self._model_config.request_timeout.total("seconds")):
                    need_another_request = await self._request_response()
                    if not need_another_request:
                        break
            except TimeoutError:
                self._logger.error("Request timed out, giving up.")
                return
            num_requests += 1
        else:
            self._logger.warning(
                f"Breaking out of request loop after {num_requests} requests.")

    async def _request_response(self) -> bool:
        message, stream_coro = await self._request_agent_message()
        # Wait for the message to completely arrive before handling tool calls
        # or sending.
        try:
            await stream_coro
        except (Exception, asyncio.CancelledError):
            self._logger.exception(
                f"Error streaming {message}. Not attempting any further "
                "processing.")
            raise
        assert message.finalized()
        need_another_request = await self._process_finalized_agent_message(
            message)
        return need_another_request

    async def _request_agent_message(self):
        start_metadata, full_metadata_class = (
            self._message_sender.make_outgoing_start_metadata(
                self._active_chat))
        metadata = msg.ChatMessageMetadata.from_start_metadata(
            start_metadata, full_metadata_class)
        parts = util.StreamableList()
        message = msg.AgentMessage(metadata, parts)
        stream_coro = await self._provider.stream_agent_message(
            parts, self._messages, self._mcp_client.tools.values())
        combined_stream_coro = self._stream_and_append_agent_message(
            message, stream_coro)
        return message, combined_stream_coro

    async def _stream_and_append_agent_message(self, message, stream_coro):
        async with asyncio.TaskGroup() as tg:
            tg.create_task(stream_coro)
            tg.create_task(self._append_message(message))

    async def _process_finalized_agent_message(
            self, message: msg.AgentMessage) -> bool:
        """
        Do processing on an agent message once it's fully received.

        Send the message in its chat and handle any tool calls the agent made.
        Return whether another request is necessary (to inform the agent of
        tool results or of an error during sending).
        """
        assert message.finalized()
        for error in await message.errors:
            self._logger.error("Message had error.", exc_info=error)
        if await message.content:
            send_task = asyncio.create_task(self._message_sender.send(message))
        else:
            self._logger.debug("Not sending message without content.")
            send_task = util.create_done_future(None)
        need_another_request = await self._handle_tool_calls(message)
        try:
            async with asyncio.timeout(
                    self._model_config.message_send_timeout.total("seconds")):
                await send_task
        except Exception as e:
            self._logger.exception(
                "Error sending message. Informing the agent to allow a retry.")
            await self._append_internal_message(
                msg.SystemMessage, content=await
                file.render_message_send_error(message, e))
            need_another_request = True
        return need_another_request

    async def _handle_tool_calls(self, message: msg.AgentMessage) -> bool:
        if not await message.tool_calls:
            return False
        for tool_call in await message.tool_calls:
            self._logger.debug(f"Handling tool call {tool_call}.")
            try:
                with self._mcp_client.with_session_transaction(
                        self._active_transaction) as tx_client:
                    await self._handle_tool_call(tool_call, tx_client)
            except Exception as e:
                await self._append_internal_message(
                    msg.ToolMessage, content="Error in tool call: " + str(e),
                    tool_call_id=tool_call.id)
                self._logger.exception("Error in tool call.")
        return True

    async def _handle_tool_call(
            self, tool_call: msg.ToolCall,
            tx_client: tool.ClientSessionTransactionContext) -> None:
        arguments = json.loads(tool_call.function.arguments)
        result = await tx_client.call_tool(tool_call.function.name, arguments)
        await self._append_internal_message(
            msg.ToolMessage, content=result.content_string,
            tool_call_id=tool_call.id)
        if isinstance(result, tool.SessionOperationToolResult):
            # The tool result wants us to call a function on the
            # session.
            await result.operation(self._active_transaction)

    async def _append_internal_message(
            self, message_class: type[msg.InternalMessage], content: str,
            **kwargs) -> None:
        """
        Append an internal message to this session.

        Internal messages are ones that don't leave the system via a chat (i.e.
        system/developer and tool messages). Constructs the message from the
        message class, mandatory content string and any additional kwargs that
        are given.

        This method only adds a message, it doesn't request a response from the
        agent.
        """
        assert issubclass(message_class, msg.InternalMessage)
        message = message_class(
            msg.InternalMessageMetadata(
                time=util.ImmediateValue(we.Instant.now())), content=content,
            **kwargs)
        await self._append_message(message)

    async def _append_agent_message(
            self, message_parts: util.StreamableList) -> None:
        """
        Append an agent message to this session.

        This makes it possible to "impersonate" the agent, i.e. add a message
        that the next API call can't distinguish from a message sent by the
        agent.

        The message is sent in the session's active chat. Any tool calls are
        handled like for an agent message that's actually produced by the API
        and their results appended to the session. Messages about send errors
        are also appended as usual. But this method only adds messages, it
        doesn't request a further response, even if there are tool results.
        """
        start_metadata, full_metadata_class = (
            self._message_sender.make_outgoing_start_metadata(
                self._active_chat))
        metadata = msg.ChatMessageMetadata.from_start_metadata(
            start_metadata, full_metadata_class)
        message = msg.AgentMessage(metadata, message_parts)
        await self._append_message(message)
        assert message.finalized()
        await self._process_finalized_agent_message(message)

    def messages(self) -> cl_abc.Generator[MessageInSession]:
        """
        Iterate all messages.

        This iterates over all messages that exist at the time of the call. For
        a live stream, see subscribe().

        Yields messages in the context of the session, i.e. with session and
        message sequence numbers.
        """
        for message_seq, message in enumerate(self._messages):
            message_offset = mdl.MessageOffset(
                session_seq=self._session_seq, message_seq=message_seq)
            yield MessageInSession(
                message=message, message_offset=message_offset)

    async def subscribe(self) -> cl_abc.AsyncGenerator[MessageInSession]:
        """
        Subscribe to messages in this session.

        Yields messages in the context of the session, i.e. with session and
        message sequence numbers.
        """
        async for message in self._publisher.subscribe():
            # We append before publishing, so message sequence number is one
            # less than the number of messages.
            message_seq = len(self._messages) - 1
            assert message_seq >= 0
            message_offset = mdl.MessageOffset(
                session_seq=self._session_seq, message_seq=message_seq)
            yield MessageInSession(
                message=message, message_offset=message_offset)


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

    An agent is an asynchronous context manager that manages its MessageStore
    to persist messages in its session, manages its MCP client, and ensures
    sessions are properly opened and closed.
    """
    def __init__(
            self, agent_information: mdl.AgentInformation,
            agent_state: mdl.AgentState, *, config: mdl.GatewayConfig,
            workspace_dir: pathlib.Path, message_store: store.MessageStore,
            memory_store: store.MemoryStore, channels: list[chan.Channel],
            provider: "prov.Provider") -> None:
        self._logger = logging.getLogger(type(self).__name__)
        if not workspace_dir.is_dir():
            raise ValueError("workspace doesn't exist")
        self._agent_information = agent_information
        self._agent_state = agent_state
        self._workspace_dir = workspace_dir
        self._message_store = message_store
        self.memory_store = memory_store
        self._mcp_client = tool.Client(
            config=config, agent=self,
            extra_env_getter=self._collect_channel_extra_env)
        self._channel_router = chan.ChannelRouter(self, channels)
        self._session_factory = ft.partial(
            Session, model_config=config.openrouter.model,
            message_sender=self._channel_router, provider=provider,
            mcp_client=self._mcp_client)
        self._session = None
        self._lock = asyncio.Lock()

    @property
    def information(self) -> mdl.AgentInformation:
        return self._agent_information

    @property
    def state(self) -> mdl.AgentState:
        return self._agent_state

    def switch_active_chat(
            self, chat: mdl.ChatDescriptor, tx: SessionTransaction) -> None:
        """
        Switch the active chat.

        Sets the agent's active chat, so that all outgoing agent messages are
        sent in that new chat. Raises an error if the given chat is equal to
        the active one.
        """
        if self._agent_state.active_chat == chat:
            raise ValueError("new chat is the same as the current one")
        self._agent_state.active_chat = chat
        tx.active_chat = chat

    @property
    def workspace_dir(self) -> pathlib.Path:
        """
        Directory inside the base_dir to which the agent has direct access.
        """
        return self._workspace_dir

    @property
    def channels(self) -> dict[str, chan.Channel]:
        """
        Return this agent's channels.

        The dictionary maps channel type to the channel.
        """
        return self._channel_router.channels

    @property
    def web_ui_channel(self) -> chan.WebUiChannel:
        """The agent's web UI channel."""
        return self._channel_router.web_ui_channel

    def __str__(self) -> str:
        return f"{type(self).__name__} {self.information.id}"

    async def __aenter__(self) -> t.Self:
        await self._message_store.__aenter__()
        await self._mcp_client.__aenter__()
        await self._channel_router.__aenter__()
        async with self._lock:
            self._process_unread_chats_task = asyncio.create_task(
                self._process_unread_chats())
            await self._ensure_active_session_locked()
            return self

    async def __aexit__(self, *args) -> bool:
        async with self._lock:
            self._process_unread_chats_task.cancel()
            try:
                async with asyncio.timeout(120):
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._process_unread_chats_task
            except Exception:
                self._logger.exception("Error waiting for unread chats task.")
            try:
                async with asyncio.timeout(20):
                    await self._session.__aexit__(*args)
            except Exception:
                self._logger.exception("Error shutting down session.")
        try:
            async with asyncio.timeout(5):
                await self._channel_router.__aexit__(*args)
        except Exception:
            self._logger.exception("Error closing channel router.")
        try:
            async with asyncio.timeout(10):
                await self._message_store.__aexit__(*args)
        except Exception:
            self._logger.exception("Error shutting down message store.")
        try:
            async with asyncio.timeout(10):
                await self._mcp_client.__aexit__(*args)
        except Exception:
            self._logger.exception("Error shutting down MCP client.")
        return False

    def _make_session(self, session_seq: int) -> Session:
        message_store = self._message_store.get_session_message_store(
            session_seq)
        return self._session_factory(
            session_seq, message_store=message_store,
            active_chat=self.state.active_chat)

    async def _ensure_active_session_locked(self):
        active_session_seq = self._message_store.get_active_session_seq()
        self._session = self._make_session(active_session_seq)
        await self._session.__aenter__()
        async with await self._session.transaction() as tx:
            if active_session_seq == 0 and not tx.num_messages:
                self._logger.info(
                    f"Existing agent {self} has no sessions. Starting the "
                    "first one.")
                await self._send_session_init_messages_locked(tx)

    async def _start_new_session_locked(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
        self._session = self._make_session(
            self._message_store.get_active_session_seq() + 1)
        await self._session.__aenter__()
        async with await self._session.transaction() as tx:
            await self._send_session_init_messages_locked(tx)

    async def _send_session_init_messages_locked(
            self, tx: SessionTransaction) -> None:
        async for message in self._onboarding_messages():
            await tx.append_internal_message(msg.DeveloperMessage, message)
        # Tell the agent about available channels.
        for channel in self._channel_router.channels.values():
            await self._add_channel_status_message_locked(channel, tx)
        # Tell the agent about their workspace and personality files.
        await tx.append_internal_message(
            msg.SystemMessage, await file.render_workspace_info(self))
        for pf in self.information.personality.personality_files:
            await tx.append_internal_message(
                msg.SystemMessage, await
                file.render_file_content(self.workspace_dir, pf.path))
        # Tell the agent that this is a new session.
        await tx.append_internal_message(
            msg.SystemMessage, await file.render_message_template(
                "system_information/session_initialization.md",
                active_chat=tx.active_chat.model_dump_json()))

    async def _onboarding_messages(self) -> cl_abc.AsyncGenerator[str]:
        """
        Read all tutorials.

        Go through all tutorial messages in a sensible order for agent
        onboarding.
        """
        yield await file.render_message_template("init_system.txt")
        tutorial_topics = [
            "tutorials",
            "system_sessions",
            "system_system_messages",
            "system_channels_chats",
            "channel_web_ui",
            "channel_agent",
            "channel_github",
            "channel_matrix",
            "system_workspace_memory",]
        for topic in tutorial_topics:
            yield await file.render_tutorial(topic)

    async def _add_channel_status_message_locked(
            self, channel: chan.Channel, tx: SessionTransaction) -> None:
        await tx.append_internal_message(
            msg.SystemMessage, await file.render_channel_status(
                channel, available=channel.type in self.channels))

    async def _process_unread_chats(self) -> None:
        """
        Infinitely loop over unread chat messages.

        Loops over the channel routers infinite generator of chats with unread
        messages and processes them appropriately. Waits until there is at
        least one chat with unread messages, then attempts to acquire the lock
        and process the messages immediately. If the lock can't be acquired
        (because the agent is still busy with earlier messages), keeps
        collecting chats with unread messages until the lock is available. Then
        processes everything at once.

        Messages are processed in batches on a per-chat basis, in order of
        arrival of the chat. I.e. all messages of the chat with the earliest
        unread message are processed, then the next chat with the earliest
        message. Releases the lock in between processing chats, so new messages
        may arrive in between.

        If the message to be processed is from the currently active chat it is
        simply appended to the session. Otherwise, an overview of unread
        messages per chat is presented to the agent, followed by an artifical
        tool call to switch chat to read the new message.
        """
        unread_chats_iter = self._channel_router.unread_message_chats()
        unread_chats: list[mdl.ChatDescriptor] = []
        while True:
            acquire_lock_task = None
            try:
                async with asyncio.TaskGroup() as tg:
                    # Use eager_start in here to make sure we get a message or
                    # the lock immediately if available.
                    get_chat_task = tg.create_task(
                        anext(unread_chats_iter), eager_start=True)
                    if not unread_chats:
                        # Need at least one chat with unread messages.
                        await get_chat_task
                    acquire_lock_task = tg.create_task(
                        self._lock.acquire(), eager_start=True)
                    done, _ = await asyncio.wait(
                        {get_chat_task, acquire_lock_task},
                        return_when=asyncio.FIRST_COMPLETED)
                    if get_chat_task in done:
                        unread_chats.append(get_chat_task.result())
                    else:
                        get_chat_task.cancel()
                        assert acquire_lock_task.done()
                    assert len(unread_chats) > 0
                    if acquire_lock_task not in done:
                        # We don't have the lock, the agent must be busy. Keep
                        # reading and adding chats until we get the lock.
                        continue
                    unread_chats = await self._handle_unread_chats_locked(
                        unread_chats)
            except ExceptionGroup as e:
                _, other_exceptions = e.split(StopAsyncIteration)
                if not other_exceptions:
                    # The generator is just closed, we must be shutting down.
                    return
                self._logger.exception(
                    "Error processing unread chats (current unread_chats "
                    f"{unread_chats}).")
            finally:
                if acquire_lock_task is not None:
                    try:
                        await acquire_lock_task
                        # The lock was acquired by us.
                        self._lock.release()
                    except asyncio.CancelledError:
                        # We didn't manage to acquire the lock, no need to
                        # release.
                        pass

    async def _handle_unread_chats_locked(
            self, unread_chats: list[mdl.ChatDescriptor]
    ) -> list[mdl.ChatDescriptor]:
        """
        Handle messages from the first of a list of unread chats.

        Gets the first chat from the list and handles all messages for that
        chat. Switches chat as needed. Returns a list of still unhandled chats,
        in the same order.
        """
        assert len(unread_chats) > 0
        try:
            async with await self._session.transaction() as tx:
                handle_task = asyncio.create_task(
                    self._handle_first_unread_chat_locked(unread_chats, tx))
                await asyncio.shield(handle_task)
            return [c for c in unread_chats if c != unread_chats[0]]
        except asyncio.CancelledError:
            try:
                # Give the shielded task some time to finish cleanly.
                async with asyncio.timeout(60):
                    await handle_task
            except Exception:
                self._logger.exception(
                    "Error waiting to process final chat messages.")
            raise

    async def _handle_first_unread_chat_locked(
            self, unread_chats: list[mdl.ChatDescriptor],
            tx: SessionTransaction) -> None:
        chat = unread_chats[0]
        try:
            if await self._channel_router.num_unread_messages(chat) == 0:
                # This can happen if a chat gets multiple messages at once:
                # We'll get the first unread chat from the channel router, then
                # process all of its unread messages here. But the channel
                # router still yields the chat again for the other messages,
                # calling this again, but without unread messages.
                return
            if chat != self.state.active_chat:
                await self._append_unread_message_overview_locked(
                    unread_chats, tx)
                # We've received a message in a chat other than the active
                # chat. Inject messages into the session that make it look
                # like the agent switched to that chat in response to it.
                # This will also append unread messages to the session.
                await self._fake_agent_switch_message_locked(chat, tx)
            else:
                # If we don't switch, we just have to append the messages
                # to the session.
                incoming_messages = (
                    await self._channel_router.get_unread_messages(chat))
                assert len(incoming_messages) > 0
                for incoming_message in incoming_messages:
                    assert incoming_message.chat == chat
                    await tx.append_incoming_message(incoming_message)
            await tx.request_responses()
        except Exception:
            self._logger.exception("Error handling unread chat messages.")

    async def _append_unread_message_overview_locked(
            self, unread_chats: list[mdl.ChatDescriptor],
            tx: SessionTransaction):
        assert unread_chats[0] != self.state.active_chat
        message_counts = {}
        for chat in unread_chats:
            message_counts.setdefault(chat, 0)
            message_counts[chat] += 1
        overview = "\n".join(
            f"{count} unread message(s) in chat {chat.model_dump_json()}"
            for chat, count in message_counts.items())
        await tx.append_internal_message(msg.SystemMessage, content=overview)

    async def _fake_agent_switch_message_locked(
            self, chat: mdl.ChatDescriptor, tx: SessionTransaction) -> None:
        """
        Insert a chat switch tool call on behalf of the agent.

        Append an agent message with empty reasoning and content, containing
        just a tool call to switch to the given chat.
        """
        tool_part = msg.AgentMessageToolPart()
        function = msg.ToolCallFunction(
            name="clawp_switch_chat",
            arguments=chat.model_dump_json(include=("channel", "chat_id")))
        # A random ID will be generated automatically.
        await tool_part.append(msg.ToolCall(function=function))
        await tool_part.finalize()
        await tx.append_agent_message(util.StreamableList([tool_part]))

    async def _collect_channel_extra_env(self) -> dict[str, str]:
        env_by_channel = {
            t: await c.get_extra_shell_env()
            for t, c in self.channels.items()}
        for t1, t2 in it.combinations(env_by_channel.keys(), 2):
            env1 = env_by_channel[t1]
            env2 = env_by_channel[t2]
            if not env1.keys().isdisjoint(env2):
                self._logger.warning(
                    f"Extra environment variables specified by channels {t1} "
                    f"and {t2} are not disjoint ({list(env1)} vs. "
                    f"{list(env2)}). Actual environment will be "
                    "non-deterministic.")
        return {
            k: v
            for env in env_by_channel.values()
            for k, v in env.items()}

    async def request_response(self) -> None:
        """
        Request an agent response.

        This simply requests a chat continuation from the agent without adding
        any message.
        """
        try:
            async with await self._session.transaction() as tx:
                await tx.request_responses()
        except Exception:
            self._logger.exception("Error requesting responses.")

    def messages(self) -> cl_abc.Generator[MessageInSession]:
        """
        Iterate all of this agent's messages.

        Yields all messages across all sessions that exist at the time of the
        call. To get live updates, use subscribe().

        Yields messages in the context of their session, i.e. with session and
        message sequence numbers.
        """
        return self._session.messages()

    def subscribe(self) -> cl_abc.AsyncGenerator[MessageInSession]:
        """
        Subscribe to the this agent's messages.

        These are all of the agent's messages in the context of its session and
        in the same order. This includes all message roles, also
        user/system/developer/tool messages.

        Yields messages in the context of their session, i.e. with session and
        message sequence numbers.
        """
        return self._session.subscribe()

    async def add_channel(self, channel: chan.Channel) -> None:
        """
        Add a channel to this agent.

        Raises a ValueError if a channel of this type already exists for the
        agent.
        """
        async with await self._session.transaction() as tx, self._lock:
            if channel.type in self.state.claimed_channels:
                raise ValueError(
                    f"agent already has a channel of type {channel.type}")
            await self._channel_router.add_channel(channel)
            self.state.claimed_channels[channel.type] = channel.id
            await self._add_channel_status_message_locked(channel, tx)

    async def remove_channel(self, channel_type: mdl.ChannelType) -> None:
        """
        Remove a channel from this agent.

        Raises a ValueError if the agent currently has no channel of this type.
        """
        async with await self._session.transaction() as tx, self._lock:
            try:
                channel = self.channels[channel_type]
                assert channel_type in self.state.claimed_channels
            except (KeyError, AssertionError):
                raise ValueError(
                    f"agent has no channel of type {channel_type}")
            await self._channel_router.remove_channel(channel_type)
            del self.state.claimed_channels[channel_type]
            await self._add_channel_status_message_locked(channel, tx)


class AgentRepository:
    """
    A repository of agents.

    The repository contains all agents in the system. It manages the directory
    containing the individual agent directories, and acts as an asynchronous
    context manager discovering/starting/stopping agents on
    __aenter__/__aexit__. It can also hatch new agents.
    """
    def __init__(
            self, *, base_dir: pathlib.Path, channel_pool: chan.ChannelPool,
            provider: "prov.Provider", config: mdl.GatewayConfig) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._base_dir = base_dir
        self._channel_pool = channel_pool
        self._provider = provider
        self._config = config
        self._agents = {}
        self._running = False

    def iter_agents(self) -> cl_abc.Generator[Agent]:
        yield from self._agents.values()

    def get_agent(self, agent_id: uuid.UUID) -> Agent:
        """
        Get agent by ID.

        Raises a KeyError if no agent with the given ID exists.
        """
        return self._agents[agent_id]

    async def __aenter__(self) -> t.Self:
        if not self._base_dir.is_dir():
            self._logger.info(f"Creating agent repository {self._base_dir}.")
            self._base_dir.mkdir(parents=True, exist_ok=True)
        for agent in self._discover_agents():
            self._logger.info(f"Starting {agent}.")
            try:
                self._agents[agent.information.id] = await agent.__aenter__()
            except Exception:
                self._logger.exception(f"Error starting {agent}.")
                raise
        self._running = True
        return self

    async def __aexit__(self, *args) -> bool:
        self._running = False
        await self._stop_agents()
        for agent in self._agents.values():
            agent_base_dir = self._agent_base_dir(agent.information.id)
            self._agent_state_file(agent_base_dir).write_text(
                agent.state.model_dump_json())
        self._agents.clear()
        return False

    async def _stop_agents(self):
        stop_tasks = {
            asyncio.create_task(a.__aexit__(None, None, None))
            for a in self._agents.values()}
        if not stop_tasks:
            stop_tasks.add(util.create_done_future(None))
        done, pending = await asyncio.wait(stop_tasks, timeout=120)
        for task in pending:
            self._logger.warning("Agent shutdown timed out.")
            task.cancel()
        for task in done:
            if task.exception():
                self._logger.error(
                    "Error shutting down agent.", exc_info=task.exception())

    def _agent_base_dir(self, agent_id: uuid.UUID) -> pathlib.Path:
        return self._base_dir / str(agent_id)

    def _discover_agents(self) -> cl_abc.Generator[Agent]:
        for d in self._base_dir.iterdir():
            if not d.is_dir():
                self._logger.warning(f"Ignoring unexpected non-directory {d}.")
                continue
            try:
                yield self._instantiate_agent(d)
            except ValueError:
                self._logger.exception(
                    f"Ignoring invalid agent directory {d}.")

    def _instantiate_agent(self, dir: pathlib.Path) -> Agent:
        agent_information, agent_state = self._load_agent_files(dir)
        workspace_dir = self._workspace_dir(dir)
        if not self._workspace_dir(dir).is_dir():
            raise ValueError(f"missing workspace directory {workspace_dir}")
        message_store_dir = self._message_store_dir(dir)
        if not message_store_dir.is_dir():
            raise ValueError(f"missing message store {message_store_dir}")
        message_store = store.MessageStore(message_store_dir)
        memory_store_dir = self._memory_store_dir(dir)
        if not memory_store_dir.is_dir():
            raise ValueError(f"missing memory store {memory_store_dir}")
        memory_store = store.JsonlMemoryStore(memory_store_dir)
        channels = []
        for ch_type, ch_id in agent_state.claimed_channels.items():
            try:
                channel_status = self._channel_pool.acquire(ch_type, ch_id)
                channels.append(channel_status.channel)
            except chan.ChannelError as e:
                self._logger.warning(
                    f"Agent {agent_information.id} claims channel "
                    f"{ch_type}:{ch_id}, but it's not available: {e}.")
        # Add the builtin web_ui and agent channels.
        channels.append(
            chan.WebUiChannel(
                self._web_ui_channel_state_dir(dir),
                agent_state.web_ui_channel))
        channels.append(
            chan.AgentChannel(
                agent_information.id, self, self._agent_channel_state_dir(dir),
                agent_state.agent_channel))
        return Agent(
            agent_information, agent_state, config=self._config,
            workspace_dir=workspace_dir, message_store=message_store,
            memory_store=memory_store, channels=channels,
            provider=self._provider)

    def _load_agent_files(
        self, agent_base_dir: pathlib.Path
    ) -> tuple[mdl.AgentInformation, mdl.AgentState]:
        try:
            agent_id = uuid.UUID(agent_base_dir.name)
        except ValueError as e:
            raise ValueError("invalid agent ID in directory name") from e
        agent_information_file = self._agent_information_file(agent_base_dir)
        try:
            agent_information = mdl.AgentInformation.model_validate_json(
                agent_information_file.read_bytes())
        except Exception as e:
            raise ValueError("invalid agent information file") from e
        agent_state_file = self._agent_state_file(agent_base_dir)
        try:
            agent_state = mdl.AgentState.model_validate_json(
                agent_state_file.read_bytes())
        except Exception as e:
            raise ValueError("invalid agent state file") from e
        if agent_information.id != agent_id:
            raise ValueError(
                f"agent ID in information file ({agent_information.id}) "
                f"doesn't match the one in the directory name ({agent_id})")
        return agent_information, agent_state

    def _agent_information_file(
            self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "agent_information.json"

    def _agent_state_file(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "agent_state.json"

    def _workspace_dir(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "workspace"

    def _message_store_dir(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "message_store"

    def _memory_store_dir(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "memory_store"

    def _web_ui_channel_state_dir(
            self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "web_ui_channel"

    def _agent_channel_state_dir(
            self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "agent_channel"

    async def hatch_agent(
            self, agent_name: str, personality_name: str) -> Agent:
        """Hatch a new agent."""
        if not self._running:
            raise RuntimeError("not running, can't hatch a new agent")
        agent_id = uuid.uuid4()
        agent_base_dir = await self._initialize_agent_files(
            agent_id, agent_name, personality_name)
        agent = self._instantiate_agent(agent_base_dir)
        self._logger.info(f"Starting new {agent}.")
        try:
            self._agents[agent.information.id] = await agent.__aenter__()
        except Exception:
            self._logger.exception(f"Error starting new {agent}.")
            raise
        return self._agents[agent.information.id]

    async def _initialize_agent_files(
            self, agent_id, agent_name, personality_name):
        try:
            personality_with_contents = (
                await
                file.read_personality_with_file_contents(personality_name))
        except file.PersonalityNotFoundError:
            raise
        except Exception as e:
            raise ValueError(
                f"can't use personality {personality_name}") from e
        self._logger.info(f"Setting up files for new agent {agent_id}.")
        agent_base_dir = self._base_dir / str(agent_id)
        if agent_base_dir.exists():
            raise ValueError(
                f"agent base directory {agent_base_dir} already exists")
        agent_base_dir.mkdir(parents=True, exist_ok=True)
        agent_information = mdl.AgentInformation(
            id=agent_id,
            name=agent_name,
            personality=personality_with_contents.get_personality(),
        )
        self._agent_information_file(agent_base_dir).write_text(
            agent_information.model_dump_json())
        agent_state = mdl.AgentState(
            active_chat=mdl.BasicChatDescriptor(channel="web_ui", chat_id=""),
            web_ui_channel=mdl.WebUiChannelState(),
            agent_channel=mdl.AgentChannelState(),
        )
        self._agent_state_file(agent_base_dir).write_text(
            agent_state.model_dump_json())
        self._logger.info(f"Created new agent state {agent_state}.")
        self._message_store_dir(agent_base_dir).mkdir(
            parents=True, exist_ok=True)
        self._memory_store_dir(agent_base_dir).mkdir(
            parents=True, exist_ok=True)
        workspace_dir = self._workspace_dir(agent_base_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        for pf in personality_with_contents.personality_files:
            file_content = (
                personality_with_contents.personality_file_contents[pf.path])
            if file_content is None:
                # File shouldn't exist.
                continue
            (workspace_dir / pf.path).write_text(file_content)
        return agent_base_dir
