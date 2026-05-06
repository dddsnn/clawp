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

import fastapi

import assistant as asst


def get_agent_from_request(request: fastapi.Request) -> asst.Agent:
    try:
        agent = request.app.state.agent
        assert isinstance(agent, asst.Agent)
    except (AttributeError, AssertionError) as e:
        raise fastapi.HTTPException(
            status_code=500, detail="Agent is not available") from e
    return agent


def get_agent_from_websocket(websocket: fastapi.WebSocket) -> asst.Agent:
    try:
        agent = websocket.app.state.agent
        assert isinstance(agent, asst.Agent)
    except (AttributeError, AssertionError) as e:
        raise RuntimeError("Agent is not available") from e
    return agent


Agent = t.Annotated[asst.Agent, fastapi.Depends(get_agent_from_request)]
AgentWs = t.Annotated[asst.Agent, fastapi.Depends(get_agent_from_websocket)]
