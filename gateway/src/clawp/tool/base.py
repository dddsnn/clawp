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
import typing as t

import fastmcp
import fastmcp.tools
import mcp.types
import pydantic as pyd
import whenever as we

from .. import model as mdl
from . import builtin, shell

if t.TYPE_CHECKING:
    from .. import agent as agt

Iso8601Instant = t.Annotated[we.Instant,
                             pyd.Field(
                                 description="An ISO 8601 timestamp",
                                 examples=["2026-06-14T17:53:00Z"])]


class Client:
    """A client providing tools via MCP servers."""
    def __init__(self, config: mdl.GatewayConfig, agent: "agt.Agent"):
        self._logger = logging.getLogger(type(self).__name__)
        server = fastmcp.FastMCP(name="Clawp MCP server")
        self._shell_server = shell.SandboxShellMcpServer(config, agent)
        server.mount(builtin.make_filesystem_proxy(agent.workspace_dir))
        server.mount(builtin.ClawpMcpServer(agent), namespace="clawp")
        server.mount(self._shell_server)
        self._client = fastmcp.Client(
            server, timeout=config.tools.client_timeout.total("seconds"))
        self._tools = None

    async def __aenter__(self):
        await self._shell_server.__aenter__()
        await self._client.__aenter__()
        self._tools = {t.name: t for t in await self._client.list_tools()}
        return self

    async def __aexit__(self, *args):
        await self._client.__aexit__(*args)
        await self._shell_server.__aexit__(*args)
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
