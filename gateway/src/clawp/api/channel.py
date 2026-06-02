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

import fastapi

from .. import model as mdl
from . import dependency as dep

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/channels")


@router.get("")
async def list_channels(
        channel_pool: dep.ChannelPool,
        agent_repo: dep.AgentRepository) -> list[mdl.ChannelInformation]:
    """Get a list of available channel accounts."""
    channel_assignments = {}
    for agent in agent_repo.iter_agents():
        for channel in agent.channels.values():
            if channel.id is None:
                # Channel without an ID (e.g. web ui), not interesting.
                continue
            try:
                existing_assignment = channel_assignments[channel.id]
                logger.warning(
                    f"Found {channel} assigned to both {existing_assignment} "
                    f"and {agent}.")
                continue
            except KeyError:
                channel_assignments[channel.id] = agent
    infos = []
    for channel_status in channel_pool:
        try:
            assigned_to_agent = channel_assignments[channel_status.channel.id]
            assigned_to_agent_id = assigned_to_agent.information.id
        except KeyError:
            assigned_to_agent_id = None
        infos.append(
            mdl.ChannelInformation(
                type=channel_status.channel.type,
                id=channel_status.channel.id,
                config=channel_status.config,
                status=await channel_status.channel.status,
                assigned_to_agent=assigned_to_agent_id,
            ))
    return infos
