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
    generate responses to new user messages using it's provider, and also
    manages which tools the assistant has available via the MCP client.

    The session is an asynchronous context manager that ensures all assistant
    messages have finished streaming when it shuts down.
    """
    def __init__(
            self, provider: "prov.Provider", mcp_client: tool.Client,
            messages: list[msg.Message]) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._provider = provider
        self._mcp_client = mcp_client
        self._messages: list[msg.Message] = messages
        self._is_shut_down = False
        self._num_incomplete_messages = 0
        self._message_wait_condition = asyncio.Condition()
        self._lock = asyncio.Lock()
        self._publisher = util.Publisher()

    async def __aenter__(self) -> "Session":
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
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't process more messages")
            await self._append_message(msg.SystemMessage, message_content)

    async def process_user_message(
            self, message_content: str
    ) -> cl_abc.AsyncGenerator[msg.AssistantMessage]:
        """
        Process and respond to a user message.

        This prepares the context with the new user message and calls the
        provider to generate one or more AssistantMessages in response. Handles
        any tool calls the assistant makes.
        """
        async with self._lock:
            if self._is_shut_down:
                raise RuntimeError("shut down, can't process more messages")
            await self._append_message(msg.UserMessage, message_content)
            async for assistant_message in self._request_assistant_messages():
                yield assistant_message

    async def _append_message(self, message_factory, *args, **kwargs):
        metadata = msg.MessageMetadata(seq_in_session=len(self._messages))
        message = message_factory(metadata, *args, **kwargs)
        self._messages.append(message)
        await self._publisher.append(message)
        return message

    async def _request_assistant_messages(self):
        while True:
            assistant_message = await self._request_assistant_message()
            yield assistant_message
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

    A consiousness consists of the active session as well as a history of past
    sessions.

    A consciousness is an asynchronous context manager that ensures sessions
    are properly opened and closed.
    """
    def __init__(
            self, provider: "prov.Provider", mcp_client: tool.Client) -> None:
        self._session_factory = ft.partial(Session, provider, mcp_client)
        self._lock = asyncio.Lock()
        self._sessions = []

    async def __aenter__(self) -> "Consciousness":
        async with self._lock:
            await self._start_new_session()
            return self

    async def __aexit__(self, *args) -> bool:
        async with self._lock:
            return await self._sessions[-1].__aexit__(*args)

    async def _start_new_session(self):
        try:
            return await self._sessions[-1].__aexit__(None, None, None)
        except IndexError:
            pass
        session = self._session_factory()
        self._sessions.append(session)
        await session.__aenter__()
        await session.add_system_message(
            self._read_message_file("init_system.md"))

    def _read_message_file(self, file_name: str) -> str:
        messages_dir = pathlib.Path(__file__).parent.parent / "messages"
        file_path = messages_dir / file_name
        with file_path.open() as f:
            return f.read()

    async def process_user_message(
            self, message_content: str
    ) -> cl_abc.AsyncGenerator[msg.AssistantMessage]:
        """Process and respond to a user message in the current session."""
        async with self._lock:
            assistant_messages = self._sessions[0].process_user_message(
                message_content)
            async for assistant_message in assistant_messages:
                yield assistant_message

    def subscribe(self) -> cl_abc.AsyncGenerator[msg.Message]:
        """Subscribe to messages in this consciousness."""
        return self._sessions[-1].subscribe()
