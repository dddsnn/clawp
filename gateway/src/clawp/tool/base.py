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

import collections.abc as cl_abc
import dataclasses as dc
import logging
import typing as t

import fastmcp
import fastmcp.client.client
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


@dc.dataclass
class ToolResult:
    """The result of a tool call."""
    raw_result: fastmcp.client.client.CallToolResult
    """The result as returned by fastmcp."""
    content_string: str
    """The result content that can be presented to the agent."""


@dc.dataclass
class SessionOperationToolResult(ToolResult):
    """A tool result instructing an operation on the session."""
    operation: cl_abc.Callable[["agt.SessionTransaction"],
                               cl_abc.Awaitable[None]]
    """An operation that should be performed on the session transaction."""


class ClientSessionTransactionContext:
    def __init__(self, client: "Client", tx: "agt.SessionTransaction") -> None:
        self._client = client
        self._tx = tx

    def __enter__(self) -> t.Self:
        self._client.set_session_transaction(self._tx)
        return self

    def __exit__(self, *args) -> bool:
        self._client.set_session_transaction(None)
        return False

    async def call_tool(self, name: str, *args, **kwargs) -> ToolResult:
        return await self._client.call_tool(name, *args, **kwargs)


class Client:
    """A client providing tools via MCP servers."""
    def __init__(self, config: mdl.GatewayConfig, agent: "agt.Agent"):
        self._logger = logging.getLogger(type(self).__name__)
        self._complex_metadata = {}
        server = fastmcp.FastMCP(name="Clawp MCP server")
        self._shell_server = shell.SandboxShellMcpServer(config, agent)
        self._clawp_server = builtin.ClawpMcpServer(
            agent, self._complex_metadata)
        server.mount(builtin.make_filesystem_proxy(agent.workspace_dir))
        server.mount(self._clawp_server, namespace="clawp")
        server.mount(self._shell_server)
        self._client = fastmcp.Client(
            server, timeout=config.tools.client_timeout.total("seconds"))
        self._tools = None
        self._session_transaction = None

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

    def set_session_transaction(self, tx: "agt.SessionTransaction") -> None:
        if self._session_transaction and tx:
            raise RuntimeError("session transaction is already set")
        self._clawp_server.session_transaction = tx
        self._session_transaction = tx

    def with_session_transaction(
            self,
            tx: "agt.SessionTransaction") -> ClientSessionTransactionContext:
        return ClientSessionTransactionContext(self, tx)

    @property
    def tools(self) -> dict[str, fastmcp.tools.Tool]:
        if self._tools is None:
            raise ValueError("client not initialized")
        return self._tools

    async def call_tool(self, name: str, *args, **kwargs) -> ToolResult:
        if name not in self._tools:
            raise ValueError(f"unknown tool {name}")
        result = await self._client.call_tool(name, *args, **kwargs)
        result_class, result_kwargs = self._determine_result_type(result)
        return result_class(**result_kwargs)

    def _determine_result_type(self, result):
        result_kwargs = {"raw_result": result, "content_string": ""}
        for block in result.content:
            if not isinstance(block, mcp.types.TextContent):
                self._logger.warning(
                    f"Ignoring non-text content block {block}.")
                continue
            result_kwargs["content_string"] += block.text
        try:
            complex_metadata_id = result.meta["complex_metadata_id"]
        except (TypeError, KeyError):
            # No complex metadata, regular result.
            return ToolResult, result_kwargs
        try:
            complex_metadata = self._complex_metadata.pop(complex_metadata_id)
        except KeyError:
            self._logger.exception(
                f"Result {result} specified a complex metadata ID, but it "
                f"wasn't present in the dict.")
            return ToolResult, result_kwargs
        if "session_operation" in complex_metadata:
            result_kwargs["operation"] = complex_metadata["session_operation"]
            return SessionOperationToolResult, result_kwargs
        return ToolResult, result_kwargs
