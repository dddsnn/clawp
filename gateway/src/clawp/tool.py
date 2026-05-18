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

import logging
import pathlib

import fastmcp
import fastmcp.client
import fastmcp.client.transports
import fastmcp.tools
import mcp.types

RUST_MCP_SERVER_COMMAND = "rust-mcp-filesystem"


class Client:
    """A client providing tools via MCP servers."""
    def __init__(self, agent_workspace: pathlib.Path):
        self._logger = logging.getLogger(type(self).__name__)
        transport = fastmcp.client.transports.StdioTransport(
            command=RUST_MCP_SERVER_COMMAND,
            args=["--enable-roots", "--allow-write"])
        roots = [f"file://{agent_workspace.resolve()}"]
        self._client = fastmcp.Client(transport, roots=roots)
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
