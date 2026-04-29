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

import asyncio
import collections.abc as cl_abc
import functools as ft
import json
import logging
import pathlib
import typing as t
import uuid

import message as msg
import store
import tool
import util

if t.TYPE_CHECKING:
    import provider as prov


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
            self, assistant_id: uuid.UUID, consciousness_id: uuid.UUID,
            session_seq: int, *, message_store: store.MessageStore,
            provider: "prov.Provider", mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._assistant_id = assistant_id
        self._consciousness_id = consciousness_id
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
        self._messages = (
            await self._message_store.read_session_messages(
                self._assistant_id, self._consciousness_id, self._session_seq))
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

    async def add_system_message(self, message_content: str) -> None:
        """
        Add a system message to the session.

        This only adds the message, it doesn't make any API calls or return
        anything.
        """
        await self._add_message(msg.SystemMessage, message_content)

    async def add_user_message(self, message_content: str) -> None:
        """Add a user message to the session, like add_system_message()."""
        await self._add_message(msg.UserMessage, message_content)

    async def _add_message(self, message_class, message_content):
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't process more messages")
            await self._append_message(message_class, message_content)

    async def _append_message(self, message_factory, *args, **kwargs):
        metadata = msg.MessageMetadata(seq_in_session=len(self._messages))
        message = message_factory(metadata, *args, **kwargs)
        self._messages.append(message)
        await self._message_store.append_message(
            self._assistant_id, self._consciousness_id, self._session_seq,
            message)
        await self._publisher.append(message)
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
                        msg.ToolMessage, content=str(result.data),
                        tool_call_id=tool_call.id)
                except Exception as e:
                    await self._append_message(
                        msg.ToolMessage,
                        content="Error in tool call: " + str(e),
                        tool_call_id=tool_call.id)
                    self._logger.exception("Error in tool call.")

    async def _request_assistant_message(self):
        assistant_message_parts = util.StreamableList()
        message_stream_task = (
            await self._provider.stream_assistant_message(
                assistant_message_parts, self._messages,
                self._mcp_client.tools.values()))
        assistant_message = await self._append_message(
            msg.AssistantMessage, assistant_message_parts)
        self._num_incomplete_messages += 1
        asyncio.create_task(
            self._wait_for_message_completion(
                message_stream_task, assistant_message))
        return assistant_message

    async def _wait_for_message_completion(self, message_stream_task, message):
        try:
            await message_stream_task
            # There is a separate task setting the message's time which we also
            # have to wait for.
            await message.time
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

    A consiousness manages the active session.

    A consciousness is an asynchronous context manager that ensures sessions
    are properly opened and closed.
    """
    def __init__(
            self, assistant_id: uuid.UUID, consciousness_id: uuid.UUID, *,
            message_store: store.MessageStore, provider: "prov.Provider",
            mcp_client: tool.Client) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._assistant_id = assistant_id
        self._consciousness_id = consciousness_id
        self._message_store = message_store
        self._session_factory = ft.partial(
            Session, self._assistant_id, self._consciousness_id,
            message_store=message_store, provider=provider,
            mcp_client=mcp_client)
        self._session = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> t.Self:
        async with self._lock:
            await self._ensure_active_session()
            return self

    async def __aexit__(self, *args) -> bool:
        async with self._lock:
            return await self._session.__aexit__(*args)

    async def _ensure_active_session(self):
        session_seqs = self._message_store.list_sessions(
            self._assistant_id, self._consciousness_id)
        if session_seqs:
            active_session_seq = session_seqs[-1]
            self._session = self._session_factory(active_session_seq)
            await self._session.__aenter__()
        else:
            self._logger.warning(
                f"Existing consciousness {self._consciousness_id} has no "
                "sessions. Starting the first one.")
            await self._message_store.create_session(
                self._assistant_id, self._consciousness_id, 0)
            self._session = self._session_factory(0)
            await self._start_new_session()

    async def _start_new_session(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
        self._session = self._session_factory(0)
        await self._session.__aenter__()
        await self._session.add_system_message(
            self._read_message_file("init_system.md"))

    def _read_message_file(self, file_name: str) -> str:
        messages_dir = pathlib.Path(__file__).parent.parent / "messages"
        file_path = messages_dir / file_name
        with file_path.open() as f:
            return f.read()

    async def process_user_message(self, message_content: str):
        """
        Process a user message in the current session.

        Adds the message to the active session and requests an assistant
        response to it which.
        """
        async with self._lock:
            await self._session.add_user_message(message_content)
            await self._session.request_response()

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """Subscribe to messages in this consciousness."""
        return self._session.subscribe()
