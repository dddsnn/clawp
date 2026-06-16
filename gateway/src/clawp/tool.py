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
import typing as t

import fastmcp
import fastmcp.client
import fastmcp.client.transports
import fastmcp.server
import fastmcp.server.providers.proxy
import fastmcp.tools
import mcp.types
import pydantic as pyd
import whenever as we

from . import file
from . import model as mdl

if t.TYPE_CHECKING:
    from . import agent as agt

Iso8601Instant = t.Annotated[we.Instant,
                             pyd.Field(
                                 description="An ISO 8601 timestamp",
                                 examples=["2026-06-14T17:53:00Z"])]


class ClawpMcpServer(fastmcp.FastMCP):
    """MCP server providing tools to interact with Clawp itself."""
    def __init__(self, agent: "agt.Agent"):
        super().__init__("Clawp system MCP server")
        self._agent = agent
        self.add_tool(self.list_tutorial_topics)
        self.add_tool(self.read_tutorial)
        self.add_tool(self.send_message)
        self.add_tool(self.log_memory)
        self.add_tool(self.search_memory)

    async def list_tutorial_topics(self) -> list[str]:
        """List all tutorial topics."""
        return await file.list_tutorial_topics()

    async def read_tutorial(self, topic: str) -> str:
        """List all tutorial topics."""
        try:
            return await file.render_tutorial(topic)
        except FileNotFoundError as e:
            raise ValueError(f"topic {topic} doesn't exist") from e

    async def send_message(
            self, channel: mdl.OutgoingChannelDescriptor, content: str) -> str:
        """
        Send a message to a specific channel.

        Normally, you don't need this since your normal response gets routed to
        the same channel you were just contacted on. If you want to respond on
        a different channel instead, or send messages on multiple channels, you
        can use this tool.
        """
        await self._agent.add_and_send_agent_message(channel, content)
        return (
            "Message delivered. Respond with empty content to acknowledge "
            "(otherwise that content will be delivered on the previous "
            "channel).")

    async def log_memory(self, content: str) -> None:
        """
        Log a memory.

        The memory is persisted with the current time and can later be found
        via clawp_search_memory.
        """
        await self._agent.memory_store.log_memory(content)

    async def search_memory(
            self, start_time: t.Optional[Iso8601Instant] = None,
            end_time: t.Optional[Iso8601Instant] = None,
            search_term: t.Optional[str] = None) -> list[mdl.Memory]:
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
            start_time=start_time, end_time=end_time, search_term=search_term)
        return [memory async for memory in memory_iter]


def _make_filesystem_proxy(
    agent_workspace: pathlib.Path
) -> fastmcp.server.providers.proxy.FastMCPProxy:
    """
    Create proxy to filesystem server.

    Create a proxy to the stdio MCP server with filesystem tools that's
    installed as a binary.
    """
    transport = fastmcp.client.transports.StdioTransport(
        command="rust-mcp-filesystem",
        args=["--enable-roots", "--allow-write"])
    client_factory = ft.partial(
        fastmcp.Client, transport,
        roots=[f"file://{agent_workspace.resolve()}"])
    return fastmcp.server.providers.proxy.FastMCPProxy(
        client_factory=client_factory)


class Client:
    """A client providing tools via MCP servers."""
    def __init__(self, agent: "agt.Agent"):
        self._logger = logging.getLogger(type(self).__name__)
        server = fastmcp.FastMCP(name="Clawp MCP server")
        server.mount(_make_filesystem_proxy(agent.workspace_dir))
        server.mount(ClawpMcpServer(agent), namespace="clawp")
        self._client = fastmcp.Client(server)
        self._tools = None

    async def __aenter__(self):
        await self._client.__aenter__()
        self._tools = {t.name: t for t in await self._client.list_tools()}
        return self

    async def __aexit__(self, *args):
        await self._client.__aexit__(*args)
        self._tools = None
        return False

    @property
    def tools(self) -> dict[str, fastmcp.tools.Tool]:
        if self._tools is None:
            raise ValueError("client not initialized")
        return self._tools

    async def call_tool(self, name: str, *args, **kwargs) -> str:
        if name not in self._tools:
            raise ValueError(f"unknown tool {name}")
        result = await self._client.call_tool(name, *args, **kwargs)
        result_string = ""
        for block in result.content:
            if not isinstance(block, mcp.types.TextContent):
                self._logger.warning(
                    f"Ignoring non-text content block {block}.")
                continue
            result_string += block.text
        return result_string
