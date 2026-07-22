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
import collections.abc as cl_abc
import pathlib
import shlex
import typing as t

import fabric
import fastmcp
import pydantic as pyd

from .. import model as mdl

if t.TYPE_CHECKING:
    from .. import agent as agt


class SandboxShellMcpServer(fastmcp.FastMCP):
    """
    MCP server providing a shell tool running in a sandbox.

    Shell commands are executed in a sandbox, which the server connects to via
    SSH. On the host, there must be a script named command_wrapper.bash in PATH
    that acts as a wrapper around the command to set up permission boundaries.
    This script is called with these arguments:

    - clawp_base_dir: The base directory where the system stores its files.
      This must be accessible in the sandbox at the same path as in the
      gateway.
    - agent_id: The agent's ID.
    - envs: A comma-separated list of environment variable names that should be
      passed on the the wrapped command.
    - cwd: The directory to change to before executing the command.
    - cmd: The command as a single string, escaped for the shell.

    The environment variables HOME, SHELL, USER, and LOGNAME must be set
    correctly by the wrapper script. The PATH must always be passed on.
    """
    def __init__(
        self, config: mdl.GatewayConfig, agent: "agt.Agent",
        extra_env_getter: cl_abc.Callable[[], cl_abc.Awaitable[dict[str,
                                                                    str]]]):
        """
        :param extra_env_getter: A coroutine function returning a dictionary of
            additional environment variables to set. It will be called on every
            execution.
        """
        super().__init__("Shell MCP server")
        self._config = config
        self._agent = agent
        self._conn = fabric.Connection(
            host=config.tools.shell.ssh.host,
            port=config.tools.shell.ssh.port,
            user=config.tools.shell.ssh.username,
            connect_kwargs={
                "key_filename": str(
                    config.tools.shell.ssh.key_filename.absolute())},
        )
        self._extra_env_getter = extra_env_getter
        self.add_tool(self.shell)

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

        HOME is set to your workspace directory (the same one you can access
        with your filesystem tools), so you can use ~ for paths relative to it
        (e.g. ~/file_in_my_workspace).
        """
        env = env or {}
        env |= await self._extra_env_getter()
        if "PATH" in env or "HOME" in env:
            raise ValueError("PATH and HOME can't be changed")
        env["PATH"] = self._config.tools.shell.path
        if cwd:
            cwd_path = pathlib.Path(cwd)
        else:
            cwd_path = self._agent.workspace_dir.absolute()
        if not cwd_path.is_absolute():
            raise ValueError("cwd must be an absolute path")
        return await asyncio.to_thread(
            self._run_wrapped_command_sync, command, cwd_path, env)

    def _run_wrapped_command_sync(
            self, command: str, cwd: pathlib.Path, env: dict[str, str]):
        # Escape the command so we can pass it to the wrapper script as a
        # single argument even with special characters (e.g. quotes,
        # redirection).
        escaped_command = shlex.quote(command)
        wrapped_command = "command_wrapper.bash {} {} {} {} {}".format(
            self._config.files_base_dir.absolute(), self._agent.information.id,
            ",".join(env.keys()), cwd, escaped_command)
        result = self._conn.run(
            wrapped_command, shell="/bin/bash", env=env, replace_env=True,
            warn=True)
        return mdl.ShellResult(
            stdout=result.stdout, stderr=result.stderr,
            exit_code=result.exited, shell=result.shell)
