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
import logging
import pathlib
import typing as t
import uuid

from .. import channel as chan
from .. import file, store, util
from .. import model as mdl
from . import agent as agt

if t.TYPE_CHECKING:
    from .. import provider as prov


class AgentRepository:
    """
    A repository of agents.

    The repository contains all agents in the system. It manages the directory
    containing the individual agent directories, and acts as an asynchronous
    context manager discovering/starting/stopping agents on
    __aenter__/__aexit__. It can also hatch new agents.
    """

    def __init__(
        self,
        *,
        base_dir: pathlib.Path,
        channel_pool: chan.ChannelPool,
        provider: prov.Provider,
        config: mdl.GatewayConfig,
    ) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._base_dir = base_dir
        self._channel_pool = channel_pool
        self._provider = provider
        self._config = config
        self._agents = {}
        self._running = False

    def iter_agents(self) -> cl_abc.Generator[agt.Agent]:
        yield from self._agents.values()

    def get_agent(self, agent_id: uuid.UUID) -> agt.Agent:
        """
        Get agent by ID.

        Raises a KeyError if no agent with the given ID exists.
        """
        return self._agents[agent_id]

    async def __aenter__(self) -> t.Self:
        if not self._base_dir.is_dir():
            self._logger.info(f"Creating agent repository {self._base_dir}.")
            self._base_dir.mkdir(parents=True, exist_ok=True)
        for agent in self._discover_agents():
            self._logger.info(f"Starting {agent}.")
            try:
                self._agents[agent.information.id] = await agent.__aenter__()
            except Exception:
                self._logger.exception(f"Error starting {agent}.")
                raise
        self._running = True
        return self

    async def __aexit__(self, *args) -> bool:
        self._running = False
        await self._stop_agents()
        for agent in self._agents.values():
            agent_base_dir = self._agent_base_dir(agent.information.id)
            self._agent_state_file(agent_base_dir).write_text(
                agent.state.model_dump_json()
            )
        self._agents.clear()
        return False

    async def _stop_agents(self):
        stop_tasks: set[asyncio.Task[None] | asyncio.Future[None]] = {
            asyncio.create_task(a.__aexit__(None, None, None))
            for a in self._agents.values()
        }
        if not stop_tasks:
            stop_tasks.add(util.create_done_future(None))
        done, pending = await asyncio.wait(stop_tasks, timeout=120)
        for task in pending:
            self._logger.warning("Agent shutdown timed out.")
            task.cancel()
        for task in done:
            if task.exception():
                self._logger.error(
                    "Error shutting down agent.", exc_info=task.exception()
                )

    def _agent_base_dir(self, agent_id: uuid.UUID) -> pathlib.Path:
        return self._base_dir / str(agent_id)

    def _discover_agents(self) -> cl_abc.Generator[agt.Agent]:
        for d in self._base_dir.iterdir():
            if not d.is_dir():
                self._logger.warning(f"Ignoring unexpected non-directory {d}.")
                continue
            try:
                yield self._instantiate_agent(d)
            except ValueError:
                self._logger.exception(
                    f"Ignoring invalid agent directory {d}."
                )

    def _instantiate_agent(self, dir: pathlib.Path) -> agt.Agent:
        agent_information, agent_state = self._load_agent_files(dir)
        workspace_dir = self._workspace_dir(dir)
        if not self._workspace_dir(dir).is_dir():
            raise ValueError(f"missing workspace directory {workspace_dir}")
        sessions_store_dir = self._sessions_store_dir(dir)
        if not sessions_store_dir.is_dir():
            raise ValueError(f"missing message store {sessions_store_dir}")
        sessions_store = store.SessionsStore(sessions_store_dir)
        memory_store_dir = self._memory_store_dir(dir)
        if not memory_store_dir.is_dir():
            raise ValueError(f"missing memory store {memory_store_dir}")
        memory_store = store.JsonlMemoryStore(memory_store_dir)
        channels = []
        for ch_type, ch_id in agent_state.claimed_channels.items():
            try:
                channel_status = self._channel_pool.acquire(ch_type, ch_id)
                channels.append(channel_status.channel)
            except chan.ChannelError as e:
                self._logger.warning(
                    f"Agent {agent_information.id} claims channel "
                    f"{ch_type}:{ch_id}, but it's not available: {e}."
                )
        # Add the builtin system, web_ui, and agent channels.
        channels.append(chan.SystemChannel())
        channels.append(
            chan.WebUiChannel(
                self._web_ui_channel_state_dir(dir), agent_state.web_ui_channel
            )
        )
        channels.append(
            chan.AgentChannel(
                self,
                self._agent_channel_state_dir(dir),
                agent_state.agent_channel,
            )
        )
        return agt.Agent(
            agent_information,
            agent_state,
            config=self._config,
            workspace_dir=workspace_dir,
            sessions_store=sessions_store,
            memory_store=memory_store,
            channels=channels,
            provider=self._provider,
        )

    def _load_agent_files(
        self, agent_base_dir: pathlib.Path
    ) -> tuple[mdl.AgentInformation, mdl.AgentState]:
        try:
            agent_id = uuid.UUID(agent_base_dir.name)
        except ValueError as e:
            raise ValueError("invalid agent ID in directory name") from e
        agent_information_file = self._agent_information_file(agent_base_dir)
        try:
            agent_information = mdl.AgentInformation.model_validate_json(
                agent_information_file.read_bytes()
            )
        except Exception as e:
            raise ValueError("invalid agent information file") from e
        agent_state_file = self._agent_state_file(agent_base_dir)
        try:
            agent_state = mdl.AgentState.model_validate_json(
                agent_state_file.read_bytes()
            )
        except Exception as e:
            raise ValueError("invalid agent state file") from e
        if agent_information.id != agent_id:
            raise ValueError(
                f"agent ID in information file ({agent_information.id}) "
                f"doesn't match the one in the directory name ({agent_id})"
            )
        return agent_information, agent_state

    def _agent_information_file(
        self, agent_base_dir: pathlib.Path
    ) -> pathlib.Path:
        return agent_base_dir / "agent_information.json"

    def _agent_state_file(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "agent_state.json"

    def _workspace_dir(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "workspace"

    def _sessions_store_dir(
        self, agent_base_dir: pathlib.Path
    ) -> pathlib.Path:
        return agent_base_dir / "sessions"

    def _memory_store_dir(self, agent_base_dir: pathlib.Path) -> pathlib.Path:
        return agent_base_dir / "memory_store"

    def _web_ui_channel_state_dir(
        self, agent_base_dir: pathlib.Path
    ) -> pathlib.Path:
        return agent_base_dir / "web_ui_channel"

    def _agent_channel_state_dir(
        self, agent_base_dir: pathlib.Path
    ) -> pathlib.Path:
        return agent_base_dir / "agent_channel"

    async def hatch_agent(
        self, agent_name: str, personality_name: str
    ) -> agt.Agent:
        """Hatch a new agent."""
        if not self._running:
            raise RuntimeError("not running, can't hatch a new agent")
        agent_id = uuid.uuid4()
        agent_base_dir = await self._initialize_agent_files(
            agent_id, agent_name, personality_name
        )
        agent = self._instantiate_agent(agent_base_dir)
        self._logger.info(f"Starting new {agent}.")
        try:
            self._agents[agent.information.id] = await agent.__aenter__()
        except Exception:
            self._logger.exception(f"Error starting new {agent}.")
            raise
        return self._agents[agent.information.id]

    async def _initialize_agent_files(
        self, agent_id: uuid.UUID, agent_name: str, personality_name: str
    ) -> pathlib.Path:
        try:
            personality_with_contents = (
                await file.read_personality_with_file_contents(
                    personality_name
                )
            )
        except file.PersonalityNotFoundError:
            raise
        except Exception as e:
            raise ValueError(
                f"can't use personality {personality_name}"
            ) from e
        return await asyncio.to_thread(
            self._initialize_agent_files_sync,
            agent_id,
            agent_name,
            personality_with_contents,
        )

    def _initialize_agent_files_sync(
        self,
        agent_id: uuid.UUID,
        agent_name: str,
        personality: mdl.AgentPersonalityWithFileContents,
    ) -> pathlib.Path:
        self._logger.info(f"Setting up files for new agent {agent_id}.")
        agent_base_dir = self._base_dir / str(agent_id)
        if agent_base_dir.exists():
            raise ValueError(
                f"agent base directory {agent_base_dir} already exists"
            )
        agent_base_dir.mkdir(parents=True, exist_ok=True)
        agent_information = mdl.AgentInformation(
            id=agent_id,
            name=agent_name,
            personality=personality.get_personality(),
        )
        self._agent_information_file(agent_base_dir).write_text(
            agent_information.model_dump_json()
        )
        agent_state = mdl.AgentState(
            active_chat=mdl.WebUiChatDescriptor(chat_id=""),
            web_ui_channel=mdl.WebUiChannelState(),
            agent_channel=mdl.AgentChannelState(),
            tools=personality.tools,
        )
        self._agent_state_file(agent_base_dir).write_text(
            agent_state.model_dump_json()
        )
        self._logger.info(f"Created new agent state {agent_state}.")
        self._sessions_store_dir(agent_base_dir).mkdir(
            parents=True, exist_ok=True
        )
        self._memory_store_dir(agent_base_dir).mkdir(
            parents=True, exist_ok=True
        )
        workspace_dir = self._workspace_dir(agent_base_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        for pf in personality.personality_files:
            file_content = personality.personality_file_contents[pf.path]
            if file_content is None:
                # File shouldn't exist.
                continue
            (workspace_dir / pf.path).write_text(file_content)
        return agent_base_dir
