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

import functools as ft
import logging
import pathlib

import fastmcp
import fastmcp.client
import fastmcp.client.transports
import fastmcp.exceptions
import fastmcp.server
import fastmcp.server.providers.proxy
import fastmcp.tools

from .. import agent as agt
from .. import file
from .. import model as mdl
from . import base


class ClawpMcpServer(fastmcp.FastMCP):
    """MCP server providing tools to interact with Clawp itself."""

    def __init__(
        self,
        agent: agt.Agent,
        complex_metadata_registry: base.ComplexToolResultMetadataRegistry,
    ):
        super().__init__("Clawp system MCP server")
        self._logger = logging.getLogger(type(self).__name__)
        self._agent = agent
        self._complex_metadata_registry = complex_metadata_registry
        self._session_transaction = None
        self.add_tool(self.list_tutorial_topics)
        self.add_tool(self.read_tutorial)
        self.add_tool(self.switch_chat)
        self.add_tool(self.log_memory)
        self.add_tool(self.search_memory)

    @property
    def session_transaction(self) -> agt.SessionTransaction:
        if self._session_transaction is None:
            raise RuntimeError("no session transaction has been set")
        return self._session_transaction

    def set_session_transaction(
        self, value: agt.SessionTransaction | None
    ) -> None:
        self._session_transaction = value

    async def list_tutorial_topics(self) -> list[str]:
        """List all tutorial topics."""
        return await file.list_tutorial_topics()

    async def read_tutorial(self, topic: str) -> str:
        """List all tutorial topics."""
        try:
            return await file.render_tutorial(topic)
        except FileNotFoundError as e:
            raise ValueError(f"topic {topic} doesn't exist") from e

    async def switch_chat(
        self, channel: str, chat_id: str
    ) -> fastmcp.tools.ToolResult:
        """
        Switch the active chat.

        Any unread messages in the new chat will be shown immediately and
        marked as read.
        """
        try:
            channel_object = self._agent.channels[channel]
        except KeyError:
            raise ValueError(f"no such channel {channel}")
        chat = await channel_object.get_chat_descriptor(chat_id)
        try:
            self._agent.switch_active_chat(chat, self.session_transaction)
        except agt.ChatSwitchError as e:
            raise fastmcp.exceptions.ToolError(e)
        content = f"You are now talking in chat {chat.model_dump_json()}."
        try:
            unread_messages = await channel_object.get_unread_messages(chat_id)
        except Exception as e:
            self._logger.exception(
                "Error fetching unread messages after switching chat to "
                "{chat}."
            )
            raise fastmcp.exceptions.ToolError(
                f"{content}\n\n But there was an error fetching unread "
                f"messages: {e}"
            )
        if not unread_messages:
            content += " No unread messages."
        else:
            content += f" Showing {len(unread_messages)} unread message(s)."

        # Specify an operation on the session that appends all the unread
        # messages after the tool result.
        async def add_unread_messages_to_session(
            tx: agt.SessionTransaction,
        ) -> None:
            for message in unread_messages:
                assert message.chat == chat
                await tx.append_incoming_message(message)

        return self._complex_metadata_registry.make_result(
            content, session_operation=add_unread_messages_to_session
        )

    async def log_memory(self, content: str) -> None:
        """
        Log a memory.

        The memory is persisted with the current time and can later be found
        via clawp_search_memory.
        """
        await self._agent.memory_store.log_memory(content)

    async def search_memory(
        self,
        start_time: base.Iso8601Instant | None = None,
        end_time: base.Iso8601Instant | None = None,
        search_term: str | None = None,
    ) -> list[mdl.Memory]:
        """
        Search memories.

        Lists all memories matching the search criteria, in ascending order of
        time.

        start_time and end_time filter for memories in the
        time range they bound. If one or both are omitted, memories are not
        filtered by the respective bound.

        If search_term is given, a simple case-insensitive substring match is
        made to filter results.

        If no filters are specified, all memories are returned.
        """
        memory_iter = self._agent.memory_store.search_memory(
            start_time=start_time, end_time=end_time, search_term=search_term
        )
        return [memory async for memory in memory_iter]


class FileSystemMcpServer(fastmcp.FastMCP):
    def __init__(self, agent_workspace: pathlib.Path) -> None:
        """
        MCP server providing access to the file system.

        This server proxies the rust-mcp-filesystem MCP server, which must be
        installed on the system (stdio mode). The server uses the MCP roots
        feature to only allow access to the agent's workspace. HOME is set to
        the agent's workspace so ~ expands intuitively.
        """
        super().__init__("File system MCP server")
        self._agent_workspace = agent_workspace
        self._filesystem_proxy = self._make_filesystem_proxy()
        self.mount(self._filesystem_proxy)

    def _make_filesystem_proxy(
        self,
    ) -> fastmcp.server.providers.proxy.FastMCPProxy:
        """
        Create proxy to filesystem server.

        Create a proxy to the stdio MCP server with filesystem tools that's
        installed as a binary.
        """
        # Set HOME to the agent's workspace, so paths containing ~ resolve
        # intuitively correct for the agent.
        transport = fastmcp.client.transports.StdioTransport(
            command="rust-mcp-filesystem",
            args=["--enable-roots", "--allow-write"],
            env={"HOME": str(self._agent_workspace.resolve())},
        )
        client_factory = ft.partial(
            fastmcp.Client,
            transport,
            roots=[f"file://{self._agent_workspace.resolve()}"],
        )
        return fastmcp.server.providers.proxy.FastMCPProxy(
            client_factory=client_factory
        )
