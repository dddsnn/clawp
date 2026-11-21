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
import dataclasses as dc
import json
import logging
import typing as t

import whenever as we

from .. import channel as chan
from .. import file, store, tool, util
from .. import message as msg
from .. import model as mdl
from . import error as err

if t.TYPE_CHECKING:
    from .. import provider as prov


@dc.dataclass
class MessageInSession:
    message: msg.Message[msg.MessageMetadata]
    message_offset: mdl.MessageOffset

    def __post_init__(self) -> None:
        if not isinstance(self.message, msg.Message):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("invalid message")  # noqa: TRY004
        if not isinstance(self.message_offset, mdl.MessageOffset):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("invalid message offset")  # noqa: TRY004


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

    def __init__(self, session: Session) -> None:
        self._session = session
        self._is_active = False
        self._completed_event = asyncio.Event()

    async def __aenter__(self) -> t.Self:
        if self._completed_event.is_set():
            raise RuntimeError("transaction has already completed")
        self._is_active = True
        return self

    async def __aexit__(self, *args) -> t.Literal[False]:
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
        return self._session._active_chat  # pyright: ignore[reportPrivateUsage]

    @active_chat.setter
    def active_chat(self, value: mdl.ChatDescriptor) -> None:
        """
        Set the active chat of the session.

        Any new agent messages in the session will be sent in the new chat.
        """
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        self._session._active_chat = value  # pyright: ignore[reportPrivateUsage]

    @property
    def num_messages(self) -> int:
        """The number of messages in this session."""
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        return self._session.num_messages

    async def append_incoming_message(
        self, incoming_message: mdl.IncomingMessage
    ) -> None:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        await self._session._append_incoming_message(incoming_message)  # pyright: ignore[reportPrivateUsage]

    async def request_responses(self) -> None:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        await self._session._request_responses()  # pyright: ignore[reportPrivateUsage]

    async def append_internal_message(
        self,
        message_class: type[msg.InternalMessage],
        content: str,
        **kwargs,
    ) -> None:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        await self._session._append_internal_message(  # pyright: ignore[reportPrivateUsage]
            message_class, content, **kwargs
        )

    async def append_agent_message(
        self, message_parts: util.StreamableList[msg.AgentMessagePart]
    ) -> None:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        await self._session._append_agent_message(message_parts)  # pyright: ignore[reportPrivateUsage]

    def messages(self) -> cl_abc.Generator[MessageInSession]:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        return self._session.messages()

    def subscribe(self) -> cl_abc.AsyncGenerator[MessageInSession]:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        return self._session.subscribe()


class SystemChannelSessionTransaction(SessionTransaction):
    """
    Session transaction only allowing the system channel.

    This class is like SessionTransaction, except that everything happens in
    the special system channel. On __aenter__, the active chat is switched to
    the system channel and a system message added informing the agent of this
    (and that they can't switch away). Any attempts to switch channel (via the
    active_chat setter) raise ChatSwitchError. On __aexit__, the previously
    active chat is restored and a system message added informing the agent of
    this.
    """

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self._previous_active_chat = mdl.WebUiChatDescriptor(chat_id="")

    async def __aenter__(self) -> t.Self:
        await super().__aenter__()
        self._previous_active_chat = self._session._active_chat  # pyright: ignore[reportPrivateUsage]
        self._session._active_chat = mdl.SystemChatDescriptor(chat_id="")  # pyright: ignore[reportPrivateUsage]
        await self.append_internal_message(
            msg.SystemMessage,
            "You were switched to the system channel. You cannot switch away "
            "(but you can make other tool calls).",
        )
        return self

    async def __aexit__(self, *args) -> t.Literal[False]:
        self._session._active_chat = self._previous_active_chat  # pyright: ignore[reportPrivateUsage]
        await self.append_internal_message(
            msg.SystemMessage,
            "You were switched back from the system channel to your previous "
            f"chat {self._session._active_chat.model_dump_json()}.",  # pyright: ignore[reportPrivateUsage]
        )
        return await super().__aexit__(*args)

    @property
    def active_chat(self) -> mdl.ChatDescriptor:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        return mdl.SystemChatDescriptor(chat_id="")

    @active_chat.setter
    def active_chat(self, value: mdl.ChatDescriptor) -> None:
        if not self._is_active:
            raise RuntimeError("transaction is not active")
        raise err.ChatSwitchError("cannot switch away from the system channel")


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
        self,
        session_seq: int,
        *,
        model_config: mdl.ModelConfig,
        sessions_store: store.SessionStore,
        message_sender: chan.MessageSender,
        info_manager: file.InfoManager,
        provider: prov.Provider,
        mcp_client: tool.Client,
        active_chat: mdl.ChatDescriptor,
    ) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._session_seq = session_seq
        self._model_config = model_config
        self._sessions_store = sessions_store
        self._message_sender = message_sender
        self._info_manager = info_manager
        self._provider = provider
        self._mcp_client = mcp_client
        self._active_chat = active_chat
        self._is_loaded = False
        self._state: mdl.SessionState = None  # pyright: ignore[reportAttributeAccessIssue]
        self._messages = []
        self._publisher = util.Publisher()
        self._active_transaction = None

    async def __aenter__(self) -> t.Self:
        if not self._is_loaded:
            await self.load()
        await self._publisher.__aenter__()
        return self

    async def __aexit__(self, *args) -> bool:
        if (
            self._active_transaction
            and not self._active_transaction.is_complete()
        ):
            self._logger.debug(
                "Waiting for ongoing transaction to complete before exiting."
            )
            await self._active_transaction.wait()
        await self._publisher.__aexit__(*args)
        return False

    async def load(self) -> None:
        """Load messages from the store."""
        if self._is_loaded:
            self._logger.warning(
                "Ignoring call to load(), since we're already loaded."
            )
            return
        (
            self._state,
            self._messages,
        ) = await self._sessions_store.load_or_create()
        self._is_loaded = True

    async def transaction(self) -> SessionTransaction:
        """
        Create a transaction.

        A transaction isolates a set of operations by only allowing one of them
        at a time. If there is still an active transaction, blocks until it
        completes.
        """
        return await self._transaction(SessionTransaction)

    async def system_channel_transaction(
        self,
    ) -> SystemChannelSessionTransaction:
        """
        Create a transaction in the special system channel.
        """
        return await self._transaction(SystemChannelSessionTransaction)

    async def _transaction[T: SessionTransaction](self, tx_type: type[T]) -> T:
        if self._active_transaction:
            if not self._active_transaction.is_complete():
                self._logger.debug(
                    "Waiting for ongoing transaction to complete before "
                    "starting the next one."
                )
            await self._active_transaction.wait()
        self._active_transaction = tx_type(self)
        return self._active_transaction

    @property
    def num_messages(self) -> int:
        """The number of messages in this session."""
        return len(self._messages)

    async def _append_incoming_message(
        self, incoming_message: mdl.IncomingMessage
    ) -> None:
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
        if isinstance(message, mdl.UserChatMessage):
            await self._add_metadata_for_user_message(message)
            message = msg.UserMessage(
                msg.ChatMessageMetadata.from_model(message.metadata),
                content=message.content,
            )
        else:
            message = msg.SystemMessage(
                msg.InternalMessageMetadata(time=message.metadata.time),
                content=message.content,
            )
        await self._append_message(message)

    async def _add_metadata_for_user_message(
        self, user_message: mdl.UserChatMessage
    ):
        message_content = await file.render_message_template(
            "message_metadata.txt",
            metadata_json=user_message.metadata.model_dump_json(),
        )
        if user_message.metadata.chat.channel == "agent":
            # This message will be sent to at least one other agent, remind the
            # agent on how to end the conversation.
            message_content += await file.render_message_template(
                "fragments/agent_to_agent_comm_reminder.txt"
            )
        await self._append_internal_message(
            msg.SystemMessage, content=message_content
        )

    async def _append_message(
        self,
        message: msg.Message[msg.MessageMetadata],
        add_info_messages: bool = True,
    ):
        """
        Append a message.

        Appends the message to the transient storage, then publishes it
        immediately, possibly before it has arrived completely. This allows
        agent messages to be streamed. Only then append the complete message to
        persistent storage. The message is guaranteed to be finalized when this
        returns.

        If add_info_messages is True, ensures that any info messages the agent
        should know about are part of the session. Any that are missing are
        appended before handling the current message.

        Cancelling this coroutine may lead to a state in which the message
        exists in transient storage and has started visibly streaming, but
        hasn't been persisted.
        """
        if add_info_messages:
            await self._append_missing_info_messages()
        self._messages.append(message)
        # First, publish the message, so clients streaming it can get it before
        # it has fully arrived. Only then append it to the message store, which
        # requires the message to have finished streaming.
        await self._publisher.append(message)
        await message.wait_finalized()
        try:
            await self._sessions_store.append_message(message)
        except Exception:
            self._logger.exception(
                "Error storing message in persistent store. The message was "
                "added and is being processed in memory, but will likely not "
                "be present when reloading from the persistent store."
            )

    async def _append_missing_info_messages(self):
        """
        Append any missing info messages.

        Queries the info manager with the current state to see if there are any
        info messages the agent should have seen that aren't yet present in the
        current session and appends them.
        """
        missing = await self._info_manager.missing_messages(self._state)
        if missing:
            self._logger.info(
                f"Appending {len(missing)} missing info messages to the "
                "agent's session."
            )
        for info_message in missing:
            await self._append_internal_message(
                info_message.message_type,
                info_message.content,
                add_info_messages=False,
            )
            self._state.info_messages_shown.add(info_message.spec)

    async def _request_responses(self) -> None:
        """
        Prompt the agent for a continuation.

        Requests an agent message from the API to continue the current state of
        the session. Any tool calls the agent makes are handled. Makes
        successive requests as long as necessary to give the agent tool results
        or report errors in sending a message.

        All agent messages are sent in the session's active chat (though the
        active chat may change at any point through a tool call by the agent).

        After this coroutine finishes, there is guaranteed to be at least one
        more message in the session.
        """
        old_num_messages = len(self._messages)
        num_requests = 0
        while num_requests < self._model_config.doom_loop_max_requests:
            try:
                async with asyncio.timeout(
                    self._model_config.request_timeout.total("seconds")
                ):
                    need_another_request = await self._request_response()
                    if not need_another_request:
                        break
            except TimeoutError:
                self._logger.error("Request timed out, giving up.")
                return
            num_requests += 1
        else:
            self._logger.warning(
                f"Breaking out of request loop after {num_requests} requests."
            )
        assert len(self._messages) > old_num_messages

    async def _request_response(self) -> bool:
        message, stream_coro = await self._request_agent_message()
        # Wait for the message to completely arrive before handling tool calls
        # or sending.
        try:
            await stream_coro
        except Exception, asyncio.CancelledError:
            self._logger.exception(
                f"Error streaming {message}. Not attempting any further "
                "processing."
            )
            raise
        assert message.finalized()
        need_another_request = await self._process_finalized_agent_message(
            message
        )
        return need_another_request

    async def _request_agent_message(self):
        start_metadata, full_metadata_class = (
            self._message_sender.make_outgoing_start_metadata(
                self._active_chat
            )
        )
        metadata = msg.ChatMessageMetadata.from_start_metadata(
            start_metadata, full_metadata_class
        )
        parts = util.StreamableList()
        message = msg.AgentMessage(metadata, parts)
        stream_coro = await self._provider.stream_agent_message(
            parts, self._messages, self._mcp_client.tools.values()
        )
        combined_stream_coro = self._stream_and_append_agent_message(
            message, stream_coro
        )
        return message, combined_stream_coro

    async def _stream_and_append_agent_message(self, message, stream_coro):
        async with asyncio.TaskGroup() as tg:
            tg.create_task(stream_coro)
            tg.create_task(self._append_message(message))

    async def _process_finalized_agent_message(
        self, message: msg.AgentMessage
    ) -> bool:
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
                self._model_config.message_send_timeout.total("seconds")
            ):
                await send_task
        except Exception as e:
            self._logger.exception(
                "Error sending message. Informing the agent to allow a retry."
            )
            await self._append_internal_message(
                msg.SystemMessage,
                content=await file.render_message_send_error(message, e),
            )
            need_another_request = True
        return need_another_request

    async def _handle_tool_calls(self, message: msg.AgentMessage) -> bool:
        if self._active_transaction is None:
            raise RuntimeError("no transaction to handle tool calls")
        if not await message.tool_calls:
            return False
        for tool_call in await message.tool_calls:
            self._logger.debug(f"Handling tool call {tool_call}.")
            try:
                with self._mcp_client.with_session_transaction(
                    self._active_transaction
                ) as tx_client:
                    await self._handle_tool_call(tool_call, tx_client)
            except Exception as e:
                await self._append_internal_message(
                    msg.ToolMessage,
                    content="Error in tool call: " + str(e),
                    tool_call_id=tool_call.id,
                )
                self._logger.exception("Error in tool call.")
        return True

    async def _handle_tool_call(
        self,
        tool_call: msg.ToolCall,
        tx_client: tool.ClientSessionTransactionContext,
    ) -> None:
        if self._active_transaction is None:
            raise RuntimeError("no transaction to handle tool call")
        arguments = json.loads(tool_call.function.arguments)
        result = await tx_client.call_tool(tool_call.function.name, arguments)
        await self._append_internal_message(
            msg.ToolMessage,
            content=result.content_string,
            tool_call_id=tool_call.id,
        )
        if isinstance(result, tool.SessionOperationToolResult):
            # The tool result wants us to call a function on the
            # session.
            await result.operation(self._active_transaction)

    async def _append_internal_message(
        self,
        message_class: type[msg.InternalMessage],
        content: str,
        add_info_messages: bool = True,
        **kwargs,
    ) -> None:
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
                time=util.ImmediateValue(we.Instant.now())
            ),
            content=content,
            **kwargs,
        )
        await self._append_message(
            message, add_info_messages=add_info_messages
        )

    async def _append_agent_message(
        self, message_parts: util.StreamableList[msg.AgentMessagePart]
    ) -> None:
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
                self._active_chat
            )
        )
        metadata = msg.ChatMessageMetadata.from_start_metadata(
            start_metadata, full_metadata_class
        )
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
                session_seq=self._session_seq, message_seq=message_seq
            )
            yield MessageInSession(
                message=message, message_offset=message_offset
            )

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
                session_seq=self._session_seq, message_seq=message_seq
            )
            yield MessageInSession(
                message=message, message_offset=message_offset
            )
