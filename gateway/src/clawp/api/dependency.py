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

import typing as t
import uuid

import fastapi
import fastapi.requests

from .. import agent as agt


def get_agent_repo(
        conn: fastapi.requests.HTTPConnection) -> agt.AgentRepository:
    try:
        agent_repo = conn.app.state.agent_repo
        assert isinstance(agent_repo, agt.AgentRepository)
    except (AttributeError, AssertionError) as e:
        raise fastapi.HTTPException(
            status_code=500, detail="Agent repo is not available") from e
    return agent_repo


def get_agent(agent_id: uuid.UUID, agent_repo: AgentRepository) -> agt.Agent:
    try:
        return agent_repo.get_agent(agent_id)
    except KeyError:
        raise fastapi.HTTPException(
            status_code=404, detail=f"No agent with ID {agent_id}")


AgentRepository = t.Annotated[agt.AgentRepository,
                              fastapi.Depends(get_agent_repo)]
Agent = t.Annotated[agt.Agent, fastapi.Depends(get_agent)]
