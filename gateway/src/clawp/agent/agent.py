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
import functools as ft
import itertools as it
import logging
import pathlib
import typing as t

import whenever as we

from .. import channel as chan
from .. import file, store, tool, util
from .. import message as msg
from .. import model as mdl
from . import session as sess

if t.TYPE_CHECKING:
    from .. import provider as prov


class ChatSwitchError(RuntimeError):
    """Raised when there is an error switching chat."""


class CompactionError(RuntimeError):
    """Raised when there is an error compacting an agent's session."""


class MissingToolError(Exception):
    """Raised when an agent is missing required tools."""

    def __init__(self, message: str, missing_tools: frozenset[str]) -> None:
        super().__init__(message)
        self.missing_tools = missing_tools


class Agent(file.InfoProvider):
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

    An agent is an asynchronous context manager that manages its SessionsStore
    to persist messages in its session, manages its MCP client, and ensures
    sessions are properly opened and closed.
    """

    MAX_REQUEST_COMPACTION_SUMMARY_ATTEMPTS = 5

    def __init__(
        self,
        agent_information: mdl.AgentInformation,
        agent_state: mdl.AgentState,
        *,
        config: mdl.GatewayConfig,
        workspace_dir: pathlib.Path,
        sessions_store: store.SessionsStore,
        memory_store: store.MemoryStore,
        channels: list[chan.Channel],
        provider: prov.Provider,
    ) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        if not workspace_dir.is_dir():
            raise ValueError("workspace doesn't exist")
        self._agent_information = agent_information
        self._agent_state = agent_state
        self._workspace_dir = workspace_dir
        self._sessions_store = sessions_store
        self.memory_store = memory_store
        self._mcp_client = tool.Client(
            config=config,
            agent=self,
            extra_env_getter=self._collect_channel_extra_env,
        )
        self._channel_router = chan.ChannelRouter(self, channels)
        self._session_factory = ft.partial(
            sess.Session,
            model_config=config.openrouter.model,
            message_sender=self._channel_router,
            info_manager=file.InfoManager(self),
            provider=provider,
            mcp_client=self._mcp_client,
        )
        self._process_unread_chats_task = None
        self._sessions: list[sess.Session] = []
        self._active_session_lock = asyncio.Lock()
        self._new_session_condition = asyncio.Condition()

    @property
    def information(self) -> mdl.AgentInformation:
        return self._agent_information

    @property
    def state(self) -> mdl.AgentState:
        return self._agent_state

    def switch_active_chat(
        self, chat: mdl.ChatDescriptor, tx: sess.SessionTransaction
    ) -> None:
        """
        Switch the active chat.

        Sets the agent's active chat, so that all outgoing agent messages are
        sent in that new chat. Raises an error if the given chat is equal to
        the active one.
        """
        if tx.active_chat == chat:
            raise ValueError("new chat is the same as the current one")
        if chat.channel == "system":
            raise ChatSwitchError("cannot switch to the system channel")
        # Switch the active chat in the transaction first, since that may raise
        # an exception if we're not allowed to switch the chat.
        tx.active_chat = chat
        self.state.active_chat = chat

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

    @property
    def enabled_tools(self) -> frozenset[str]:
        return frozenset(self._mcp_client.tools)

    def __str__(self) -> str:
        return f"<Agent {self.information.name} ({self.information.id})>"

    async def __aenter__(self) -> t.Self:
        await self._sessions_store.__aenter__()
        await self._mcp_client.__aenter__()
        await self._channel_router.__aenter__()
        await self._load_sessions()
        self._process_unread_chats_task = asyncio.create_task(
            self._process_unread_chats()
        )
        return self

    async def __aexit__(self, *args) -> bool:
        assert self._process_unread_chats_task is not None
        async with self._active_session_lock:
            self._process_unread_chats_task.cancel()
            try:
                async with asyncio.timeout(120):
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._process_unread_chats_task
            except Exception:
                self._logger.exception("Error waiting for unread chats task.")
            try:
                async with asyncio.timeout(20):
                    await self._sessions[-1].__aexit__(*args)
            except Exception:
                self._logger.exception("Error shutting down session.")
        try:
            async with asyncio.timeout(5):
                await self._channel_router.__aexit__(*args)
        except Exception:
            self._logger.exception("Error closing channel router.")
        try:
            async with asyncio.timeout(10):
                await self._sessions_store.__aexit__(*args)
        except Exception:
            self._logger.exception("Error shutting down message store.")
        try:
            async with asyncio.timeout(10):
                await self._mcp_client.__aexit__(*args)
        except Exception:
            self._logger.exception("Error shutting down MCP client.")
        return False

    async def _load_sessions(self):
        async with self._active_session_lock:
            assert len(self._sessions) == 0
            active_session_seq = self._sessions_store.get_active_session_seq()
            for i in range(active_session_seq + 1):
                self._sessions.append(self._make_session(i))
                await self._sessions[-1].load()
            await self._sessions[-1].__aenter__()
            async with await self._sessions[-1].transaction() as tx:
                if active_session_seq == 0 and not tx.num_messages:
                    self._logger.info(
                        f"Existing agent {self} has no sessions. Starting the "
                        "first one."
                    )
                    await self._append_session_init_messages_locked(tx)

    def _make_session(self, session_seq: int) -> sess.Session:
        sessions_store = self._sessions_store.for_session(session_seq)
        return self._session_factory(
            session_seq,
            sessions_store=sessions_store,
            active_chat=self.state.active_chat,
        )

    async def _append_session_init_messages_locked(
        self,
        tx: sess.SessionTransaction,
        compaction_summary: str | None = None,
    ) -> None:
        # Tell the agent about available channels.
        for channel in self._channel_router.channels.values():
            if channel.type == "system":
                continue
            await self._append_channel_status_message_locked(channel, tx)
        # Tell the agent about their workspace and personality files.
        await tx.append_internal_message(
            msg.SystemMessage, await file.render_workspace_info(self)
        )
        # Tell the agent that this is a new session.
        await self._append_session_start_messages(tx, compaction_summary)

    async def _append_channel_status_message_locked(
        self, channel: chan.Channel, tx: sess.SessionTransaction
    ) -> None:
        await tx.append_internal_message(
            msg.SystemMessage,
            await file.render_channel_status(
                channel, available=channel.type in self.channels
            ),
        )

    async def _append_session_start_messages(
        self, tx: sess.SessionTransaction, compaction_summary: str | None
    ) -> None:
        render_kwargs = {
            "name": self.information.name,
            "information": self.information.model_dump_json(),
            "active_chat": tx.active_chat.model_dump_json(),
        }
        session_seq = len(self._sessions) - 1
        if compaction_summary is None:
            assert session_seq == 0
            template = (
                "system_information/session_initialization_first_ever.md"
            )
        else:
            assert session_seq > 0
            render_kwargs["session_seq"] = str(session_seq)
            template = (
                "system_information/session_initialization_after_compaction.md"
            )
        await tx.append_internal_message(
            msg.SystemMessage,
            await file.render_message_template(template, **render_kwargs),
        )
        if compaction_summary:
            await tx.append_internal_message(
                msg.SystemMessage,
                await file.render_message_template(
                    "compaction_summary.txt", summary=compaction_summary
                ),
            )

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
            locked_tx_cm = acquire_locked_tx_task = None
            try:
                async with asyncio.TaskGroup() as tg:
                    # Use eager_start in here to make sure we get a message
                    # immediately if available.
                    get_chat_task = tg.create_task(
                        anext(unread_chats_iter), eager_start=True
                    )
                    if not unread_chats:
                        # Need at least one chat with unread messages.
                        await get_chat_task
                    locked_tx_cm = self._locked_transaction()
                    acquire_locked_tx_task = tg.create_task(
                        locked_tx_cm.__aenter__()
                    )
                    done, _ = await asyncio.wait(
                        {get_chat_task, acquire_locked_tx_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if get_chat_task in done:
                        unread_chats.append(get_chat_task.result())
                    else:
                        get_chat_task.cancel()
                        assert acquire_locked_tx_task.done()
                    assert len(unread_chats) > 0
                    if acquire_locked_tx_task not in done:
                        # We don't have the lock, the agent must be busy. Keep
                        # reading and adding chats until we get the lock.
                        continue
                    unread_chats = await self._handle_unread_chats_locked(
                        unread_chats, acquire_locked_tx_task.result()
                    )
            except ExceptionGroup as e:
                _, other_exceptions = e.split(StopAsyncIteration)
                if not other_exceptions:
                    # The generator is just closed, we must be shutting down.
                    return
                self._logger.exception(
                    "Error processing unread chats (current unread_chats "
                    f"{unread_chats})."
                )
            finally:
                if acquire_locked_tx_task is not None:
                    assert locked_tx_cm is not None
                    try:
                        await acquire_locked_tx_task
                        # We got the lock and transaction earlier, release.
                        await locked_tx_cm.__aexit__(None, None, None)
                    except asyncio.CancelledError:
                        # We didn't manage to acquire the lock, no need to
                        # release.
                        pass

    def _locked_transaction(
        self,
    ) -> util.DependencyContextManager[sess.SessionTransaction]:
        """
        Lock, and get a transaction.

        Almost always, using a session transaction is enough to ensure the
        necessary mutual exclusion in the session. We only need our internal
        lock to handle situations like session rollover (when we create a new
        session), so that someone waiting on a transaction doesn't accidentally
        get one on the old session.

        This helper function returns a context manager that acquires the lock
        and returns the current session when it is entered.
        """
        manager = util.DependencyContextManager()
        manager.register_dependency(self._active_session_lock)
        manager.register_primary(lambda: self._sessions[-1].transaction())
        return manager

    async def _handle_unread_chats_locked(
        self,
        unread_chats: list[mdl.ChatDescriptor],
        tx: sess.SessionTransaction,
    ) -> list[mdl.ChatDescriptor]:
        """
        Handle messages from the first of a list of unread chats.

        Gets the first chat from the list and handles all messages for that
        chat. Switches chat as needed. Returns a list of still unhandled chats,
        in the same order.
        """
        assert len(unread_chats) > 0
        handle_task = util.create_done_future(None)
        try:
            handle_task = asyncio.create_task(
                self._handle_first_unread_chat_locked(unread_chats, tx)
            )
            await asyncio.shield(handle_task)
            return [c for c in unread_chats if c != unread_chats[0]]
        except asyncio.CancelledError:
            try:
                # Give the shielded task some time to finish cleanly.
                async with asyncio.timeout(60):
                    await handle_task
            except Exception:
                self._logger.exception(
                    "Error waiting to process final chat messages."
                )
            raise

    async def _handle_first_unread_chat_locked(
        self,
        unread_chats: list[mdl.ChatDescriptor],
        tx: sess.SessionTransaction,
    ) -> None:
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
                    unread_chats, tx
                )
                # We've received a message in a chat other than the active
                # chat. Inject messages into the session that make it look
                # like the agent switched to that chat in response to it.
                # This will also append unread messages to the session.
                await self._fake_agent_switch_message_locked(chat, tx)
            else:
                # If we don't switch, we just have to append the messages
                # to the session.
                incoming_messages = (
                    await self._channel_router.get_unread_messages(chat)
                )
                assert len(incoming_messages) > 0
                for incoming_message in incoming_messages:
                    assert incoming_message.chat == chat
                    await tx.append_incoming_message(incoming_message)
            await tx.request_responses()
        except Exception:
            self._logger.exception("Error handling unread chat messages.")

    async def _append_unread_message_overview_locked(
        self,
        unread_chats: list[mdl.ChatDescriptor],
        tx: sess.SessionTransaction,
    ):
        assert unread_chats[0] != self.state.active_chat
        message_counts = {}
        for chat in unread_chats:
            message_counts.setdefault(chat, 0)
            message_counts[chat] += 1
        overview = "\n".join(
            f"{count} unread message(s) in chat {chat.model_dump_json()}"
            for chat, count in message_counts.items()
        )
        await tx.append_internal_message(msg.SystemMessage, content=overview)

    async def _fake_agent_switch_message_locked(
        self, chat: mdl.ChatDescriptor, tx: sess.SessionTransaction
    ) -> None:
        """
        Insert a chat switch tool call on behalf of the agent.

        Append an agent message with empty reasoning and content, containing
        just a tool call to switch to the given chat.
        """
        tool_part = msg.AgentMessageToolPart()
        function = msg.ToolCallFunction(
            name="switch_chat",
            arguments=chat.model_dump_json(include={"channel", "chat_id"}),
        )
        # A random ID will be generated automatically.
        await tool_part.append(msg.ToolCall(function=function))
        await tool_part.finalize()
        await tx.append_agent_message(util.StreamableList([tool_part]))

    async def _collect_channel_extra_env(self) -> dict[str, str]:
        env_by_channel = {
            t: await c.get_extra_shell_env() for t, c in self.channels.items()
        }
        for t1, t2 in it.combinations(env_by_channel.keys(), 2):
            env1 = env_by_channel[t1]
            env2 = env_by_channel[t2]
            if not env1.keys().isdisjoint(env2):
                self._logger.warning(
                    f"Extra environment variables specified by channels {t1} "
                    f"and {t2} are not disjoint ({list(env1)} vs. "
                    f"{list(env2)}). Actual environment will be "
                    "non-deterministic."
                )
        return {
            k: v for env in env_by_channel.values() for k, v in env.items()
        }

    async def request_response(self) -> None:
        """
        Request an agent response.

        This simply requests a chat continuation from the agent without adding
        any message.
        """
        try:
            async with self._locked_transaction() as tx:
                await tx.request_responses()
        except Exception:
            self._logger.exception("Error requesting responses.")

    def messages(self) -> cl_abc.Iterable[sess.MessageInSession]:
        """
        Iterate all of this agent's messages.

        Yields all messages across all sessions that exist at the time of the
        call. To get live updates, use subscribe().

        Yields messages in the context of their session, i.e. with session and
        message sequence numbers.
        """
        return it.chain(*[s.messages() for s in self._sessions])

    async def subscribe(self) -> cl_abc.AsyncGenerator[sess.MessageInSession]:
        """
        Subscribe to all of this agent's new messages.

        These are all of the agent's messages in the context of its session and
        in the same order, starting at the time of the call. This includes all
        message roles, also user/system/developer/tool messages.

        Yields messages in the context of their session, i.e. with session and
        message sequence numbers. When a new session is started, the
        subscription switches to yielding messages from it.
        """
        assert len(self._sessions) > 0
        get_history_first = False
        last_offset = mdl.MessageOffset.prehistoric()
        for session_index in it.count(len(self._sessions) - 1):
            session = self._sessions[session_index]
            session_subscription = session.subscribe()
            # Start a task to get the first element from the subscription. Use
            # eager_start=True to make sure we're not missing anything that
            # gets added just after we fetch the history.
            first_message_task = asyncio.create_task(
                anext(session_subscription), eager_start=True
            )
            if get_history_first:
                for message in session.messages():
                    yield message
                    last_offset = message.message_offset
            # In the following, only yield if we haven't seen the message (by
            # offset) in the history already.
            try:
                message = await first_message_task
                if message.message_offset > last_offset:
                    yield message
                    last_offset = message.message_offset
                async for message in session_subscription:
                    if message.message_offset > last_offset:
                        yield message
                        last_offset = message.message_offset
            except StopAsyncIteration:
                # Subscription already ended (in first_message_task).
                pass
            except util.PublisherNotRunningError:
                # The session was already shut down. We got all messages with
                # the history.
                pass
            # The current session's subscription is done, which means
            # it must have been closed. Wait for the next session to
            # get started, then continue subscribing there.
            self._logger.debug(
                f"Session subscription ended at {last_offset}, waiting for "
                "next session to start."
            )
            # For subsequent sessions, first yield any messages that were added
            # after the session started but before we subscribed.
            get_history_first = True
            async with self._new_session_condition:
                # Wait for the next session to be added so we can continue the
                # loop.
                await self._new_session_condition.wait_for(
                    lambda wanted_num_sessions=session_index + 2: (
                        len(self._sessions) >= wanted_num_sessions
                    )
                )
                self._logger.debug(
                    "Next session started, continuing subscription."
                )

    async def add_channel(self, channel: chan.Channel) -> None:
        """
        Add a channel to this agent.

        Raises a ValueError if a channel of this type already exists for the
        agent.
        """
        if channel.id is None:
            raise ValueError("can't add channels without ID")
        missing_tools = channel.required_tools - self.enabled_tools
        if missing_tools:
            raise MissingToolError(
                f"agent is missing required tools {missing_tools}",
                missing_tools,
            )
        async with self._locked_transaction() as tx:
            if channel.type in self.state.claimed_channels:
                raise ValueError(
                    f"agent already has a channel of type {channel.type}"
                )
            await self._channel_router.add_channel(channel)
            self.state.claimed_channels[channel.type] = channel.id
            await self._append_channel_status_message_locked(channel, tx)

    async def remove_channel(self, channel_type: mdl.ChannelType) -> None:
        """
        Remove a channel from this agent.

        Raises a ValueError if the agent currently has no channel of this type.
        """
        async with self._locked_transaction() as tx:
            try:
                channel = self.channels[channel_type]
                assert channel_type in self.state.claimed_channels
            except KeyError, AssertionError:
                raise ValueError(
                    f"agent has no channel of type {channel_type}"
                )
            await self._channel_router.remove_channel(channel_type)
            del self.state.claimed_channels[channel_type]
            await self._append_channel_status_message_locked(channel, tx)

    async def compact_session(self) -> None:
        """
        Compact the agent's session.

        The agent is switched to the system channel, where they are prompted to
        summarize the current session. A new session is started with all the
        system onboarding messages followed by the agent's own summary of the
        old session.
        """
        async with self._active_session_lock:
            old_num_sessions = len(self._sessions)
            active_session = self._sessions[-1]
            async with await active_session.system_channel_transaction() as tx:
                try:
                    compaction_summary = (
                        await self._request_compaction_summary_locked(tx)
                    )
                except CompactionError:
                    raise
                except Exception as e:
                    raise CompactionError(
                        "error getting compaction summary from agent"
                    ) from e
            await self._start_new_session_locked(compaction_summary)
        assert len(self._sessions) == old_num_sessions + 1

    async def _request_compaction_summary_locked(
        self, tx: sess.SystemChannelSessionTransaction
    ) -> str:
        time_str = we.Instant.now().format_iso(unit="millisecond")
        await tx.append_internal_message(
            msg.SystemMessage,
            await file.render_message_template(
                "request_compaction_summary.txt", time_str=time_str
            ),
        )
        for i in range(self.MAX_REQUEST_COMPACTION_SUMMARY_ATTEMPTS):
            await tx.request_responses()
            responses = (
                self._channel_router.system_channel.pop_received_messages()
            )
            try:
                summary_response = responses[-1]
                summary = await summary_response.content
                assert len(summary) > 0
                return summary
            except IndexError, AssertionError:
                self._logger.debug(
                    "Received no compaction summary from agent at attempt "
                    f"{i + 1}, reprompting."
                )
            await tx.append_internal_message(
                msg.SystemMessage,
                "Your last message contained no content. Please provide a "
                "compaction summary.",
            )
        raise CompactionError(
            "agent provided no compaction summary after "
            f"{self.MAX_REQUEST_COMPACTION_SUMMARY_ATTEMPTS} attempts"
        )

    async def _start_new_session_locked(self, compaction_summary: str):
        assert len(self._sessions) > 0
        assert len(compaction_summary) > 0
        self._logger.debug("Closing active session.")
        async with self._new_session_condition:
            await self._sessions[-1].__aexit__(None, None, None)
            self._sessions.append(
                self._make_session(
                    self._sessions_store.get_active_session_seq() + 1
                )
            )
            self._logger.debug("Starting new session.")
            await self._sessions[-1].__aenter__()
            async with await self._sessions[-1].transaction() as tx:
                await self._append_session_init_messages_locked(
                    tx, compaction_summary
                )
            self._logger.debug("Setting new session notification.")
            self._new_session_condition.notify_all()

    @property
    def info_message_specs(self) -> frozenset[mdl.InfoMessageSpec[t.Any]]:
        tutorial_topics = [
            "tutorials",
            "sessions",
            "system_messages",
            "channels",
            "workspace",
        ]
        onboarding_specs = frozenset(
            [mdl.InfoMessageSpecInit()]
            + [
                mdl.InfoMessageSpecTutorial(topic=topic)
                for topic in tutorial_topics
            ]
        )
        sub_specs = [
            provider.info_message_specs
            for provider in (
                self.information.personality,
                self._channel_router,
                self._mcp_client,
            )
        ]
        return onboarding_specs | frozenset(it.chain(*sub_specs))
