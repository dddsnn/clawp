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

import fastmcp
import fastmcp.tools

mcp_server = fastmcp.FastMCP(name="Clawp MCP server")


@mcp_server.tool
def add(a: int, b: int) -> int:
    """Adds two integer numbers together."""
    return a + b


class Client:
    """A client providing tools via MCP servers."""
    def __init__(self):
        self._client = fastmcp.Client(mcp_server)
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

    async def call_tool(
            self, name: str, *args, **kwargs) -> fastmcp.tools.CallToolResult:
        if name not in self._tools:
            raise ValueError(f"unknown tool {name}")
        return await self._client.call_tool(name, *args, **kwargs)
