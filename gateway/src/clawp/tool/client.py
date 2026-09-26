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
import contextlib
import logging
import pathlib
import typing as t

import fastmcp
import fastmcp.client.client
import mcp.types

from .. import file
from .. import model as mdl
from . import base, builtin, shell

if t.TYPE_CHECKING:
    from .. import agent as agt


class Client(file.InfoProvider):
    """
    A client providing tools via MCP servers.

    The client provides access to a number of underlying MCP servers. It is a
    context manager that starts those servers on __aenter__(). Additionally, it
    ensures that any config files required by the servers exist. If not, the
    default config file is copied to the agent's workspace from the file
    module.
    """

    def __init__(
        self,
        config: mdl.GatewayConfig,
        agent: agt.Agent,
        extra_env_getter: cl_abc.Callable[
            [], cl_abc.Awaitable[dict[str, str]]
        ],
    ):
        """
        :param extra_env_getter: A coroutine function returning a dictionary of
            additional environment variables for the shell tool. It will be
            called on every execution of the shell tool.
        """
        self._logger = logging.getLogger(type(self).__name__)
        self._agent = agent
        self._complex_metadata_registry = (
            base.ComplexToolResultMetadataRegistry()
        )
        server = fastmcp.FastMCP(name="Clawp MCP server")
        self._clawp_server = builtin.ClawpMcpServer(
            self._agent, self._complex_metadata_registry
        )
        self._shell_server = shell.SandboxShellMcpServer(
            self._agent, config, extra_env_getter
        )
        self._filesystem_server = builtin.FileSystemMcpServer(
            self._agent, self._shell_server.shell
        )
        server.mount(self._clawp_server, namespace="clawp")
        server.mount(self._shell_server)
        server.mount(self._filesystem_server)
        self._client = fastmcp.Client(
            server, timeout=config.tools.client_timeout.total("seconds")
        )
        self._exit_stack = contextlib.AsyncExitStack()
        self._tools = None
        self._session_transaction = None

    @property
    def _servers(self) -> frozenset[base.McpServer]:
        return frozenset(
            [
                self._clawp_server,
                self._filesystem_server,
                self._shell_server,
            ]
        )

    @property
    def _config_file_paths(self) -> frozenset[pathlib.Path]:
        return frozenset(
            [
                path
                for server in self._servers
                for path in server.config_file_paths
            ]
        )

    async def __aenter__(self):
        await self._exit_stack.__aenter__()
        await self._exit_stack.enter_async_context(self._shell_server)
        await self._exit_stack.enter_async_context(self._filesystem_server)
        await self._exit_stack.enter_async_context(self._client)
        available_tools = await self._client.list_tools()
        self._warn_if_unavailable_tools_required(available_tools)
        self._tools = {
            t.name: t for t in available_tools if self._tool_is_allowed(t)
        }
        self._warn_if_options_for_unavailable_tools()
        await self._ensure_config_files_exist()
        return self

    async def __aexit__(self, *args):
        await self._exit_stack.__aexit__(*args)
        self._tools = None
        return False

    def _warn_if_unavailable_tools_required(
        self, available_tools: list[mcp.Tool]
    ) -> None:
        available_tool_names = {t.name for t in available_tools}
        configured_tools = set()
        if self._agent.state.tools.exclude != "*":
            configured_tools |= set(self._agent.state.tools.exclude)
        if self._agent.state.tools.include != "*":
            configured_tools |= set(self._agent.state.tools.include)
        unknown_tools = configured_tools - available_tool_names
        if unknown_tools:
            self._logger.warning(
                f"Found unknown tools configured: {unknown_tools}."
            )

    def _warn_if_options_for_unavailable_tools(self):
        for tool_name, options in self._agent.state.tools.options.items():
            if tool_name not in self.tools:
                self._logger.warning(
                    "Agent state specifies options for unavailable tool "
                    f"{tool_name}: {options}."
                )

    def _tool_is_allowed(self, tool: mcp.Tool) -> bool:
        if (
            self._agent.state.tools.exclude == "*"
            or tool.name in self._agent.state.tools.exclude
        ):
            return False
        return (
            self._agent.state.tools.include == "*"
            or tool.name in self._agent.state.tools.include
        )

    def set_session_transaction(
        self, tx: agt.SessionTransaction | None
    ) -> None:
        if self._session_transaction and tx:
            raise RuntimeError("session transaction is already set")
        self._clawp_server.set_session_transaction(tx)
        self._session_transaction = tx

    def with_session_transaction(
        self, tx: agt.SessionTransaction
    ) -> base.ClientSessionTransactionContext:
        """
        Set a session transaction.

        Returns a context manager that sets the session transaction on the
        client. The context manager also acts as a proxy to the client.
        """
        return base.ClientSessionTransactionContext(self, tx)

    @property
    def tools(self) -> dict[str, mcp.types.Tool]:
        if self._tools is None:
            raise ValueError("client not initialized")
        return self._tools

    @property
    def info_message_specs(self) -> frozenset[mdl.InfoMessageSpec[t.Any]]:
        file_specs = [
            mdl.InfoMessageSpecFileContent(file_path=path)
            for path in self._config_file_paths
        ]
        other_specs = [
            spec
            for server in self._servers
            for spec in server.info_message_specs
        ]
        return frozenset(file_specs + other_specs)

    async def _ensure_config_files_exist(self):
        for file_path in self._config_file_paths:
            file_in_workspace = self._agent.workspace_dir / file_path
            if file_in_workspace.exists():
                continue
            self._logger.info(
                f"{self._agent} is missing config file {file_path}, "
                "installing the default."
            )
            try:
                file_content = await file.read_file("config_files", file_path)
                file_in_workspace.write_text(file_content)
            except Exception as e:
                raise RuntimeError(
                    f"Error installing default config file {file_path}, tool "
                    "will misbehave."
                ) from e

    async def call_tool(self, name: str, *args, **kwargs) -> base.ToolResult:
        assert self._tools is not None
        if name not in self._tools:
            raise ValueError(f"unknown tool {name}")
        result = await self._client.call_tool(name, *args, **kwargs)
        return self._wrap_result(result)

    def _wrap_result(
        self, result: fastmcp.client.client.CallToolResult
    ) -> base.ToolResult:
        content_string = ""
        for block in result.content:
            if not isinstance(block, mcp.types.TextContent):
                self._logger.warning(
                    f"Ignoring non-text content block {block}."
                )
                continue
            content_string += block.text
        complex_metadata = self._complex_metadata_registry.pop_for_result(
            result
        )
        try:
            session_operation = complex_metadata["session_operation"]
            return base.SessionOperationToolResult(
                raw_result=result,
                content_string=content_string,
                operation=session_operation,
            )
        except KeyError:
            return base.ToolResult(
                raw_result=result, content_string=content_string
            )
