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
import fastmcp.server
import fastmcp.server.providers.proxy
import fastmcp.tools
import mcp.types


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
    def __init__(self, agent_workspace: pathlib.Path):
        self._logger = logging.getLogger(type(self).__name__)
        server = fastmcp.FastMCP(name="Clawp MCP server")
        server.mount(_make_filesystem_proxy(agent_workspace))
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
