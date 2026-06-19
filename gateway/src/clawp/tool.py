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

import asyncio
import functools as ft
import logging
import pathlib
import typing as t

import fabric
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


class ShellMcpServer(fastmcp.FastMCP):
    """MCP server providing a shell tool."""
    def __init__(self, config: mdl.ShellConfig, home: pathlib.Path):
        super().__init__("Shell MCP server")
        self._config = config
        self._home = home.absolute()
        self.add_tool(self.shell)
        self._conn = fabric.Connection(
            host=config.ssh.host, port=config.ssh.port,
            user=config.ssh.username, connect_kwargs={
                "key_filename": str(config.ssh.key_filename.absolute())})

    async def __aenter__(self) -> t.Self:
        await asyncio.to_thread(self._conn.open)
        return self

    async def __aexit__(self, *_) -> bool:
        await asyncio.to_thread(self._conn.close)
        return False

    async def shell(
        self,
        command: str,
        cwd: t.Optional[t.Annotated[
            str,
            pyd.Field(
                description=
                "Change working directory before running the command. Must be "
                "an absolute path. Default: own workspace directory."
            )]] = None,
        env: t.Optional[dict[str, str]] = None,
    ) -> mdl.ShellResult:
        """
        Execute a command in a shell.

        Executes the given command in a shell within a sandbox. Each call to
        this tool spawns a new shell, so working directory and environment
        don't persist across calls. You may specify environment variables to
        set first. PATH and HOME are set automatically and can't be changed.
        HOME is set to your workspace directory.
        """
        env = env or {}
        if "PATH" in env or "HOME" in env:
            raise ValueError("PATH and HOME can't be changed")
        env = env | {"PATH": self._config.path, "HOME": str(self._home)}
        if cwd:
            cwd_path = pathlib.Path(cwd)
        else:
            cwd_path = self._home
        if not cwd_path.is_absolute():
            raise ValueError("cwd must be an absolute path")
        return await asyncio.to_thread(self._run_sync, command, cwd_path, env)

    def _run_sync(self, command, cwd, env):
        with self._conn.cd(str(cwd)):
            result = self._conn.run(
                command, shell=self._config.shell_binary, env=env,
                replace_env=True)
        return mdl.ShellResult(
            stdout=result.stdout, stderr=result.stderr,
            exit_code=result.exited, shell=result.shell)


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
    def __init__(self, config: mdl.ToolConfig, agent: "agt.Agent"):
        self._logger = logging.getLogger(type(self).__name__)
        server = fastmcp.FastMCP(name="Clawp MCP server")
        self._shell_server = ShellMcpServer(config.shell, agent.workspace_dir)
        server.mount(_make_filesystem_proxy(agent.workspace_dir))
        server.mount(ClawpMcpServer(agent), namespace="clawp")
        server.mount(self._shell_server)
        self._client = fastmcp.Client(server)
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
