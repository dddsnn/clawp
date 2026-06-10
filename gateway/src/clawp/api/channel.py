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

import fastapi

from .. import channel as chan
from .. import model as mdl
from . import dependency as dep

if t.TYPE_CHECKING:
    from .. import agent as agt

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/channels")


@router.get("")
async def list_channels(
        channel_pool: dep.ChannelPool,
        agent_repo: dep.AgentRepository) -> list[mdl.ChannelInformation]:
    """Get a list of available channel accounts."""
    channel_assignments = _channel_assignments(agent_repo)
    infos = []
    for channel_status in channel_pool:
        try:
            channel_key = (
                channel_status.channel.type, channel_status.channel.id)
            assigned_to_agent = channel_assignments[channel_key]
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


def _channel_assignments(
        agent_repo: "agt.AgentRepository"
) -> dict[tuple[str, str], "agt.Agent"]:
    channel_assignments = {}
    for agent in agent_repo.iter_agents():
        for channel in agent.channels.values():
            if channel.id is None:
                # Channel without an ID (e.g. web ui), not interesting.
                continue
            try:
                channel_key = (channel.type, channel.id)
                existing_assignment = channel_assignments[channel_key]
                logger.warning(
                    f"Found {channel} assigned to both {existing_assignment} "
                    f"and {agent}.")
                continue
            except KeyError:
                channel_assignments[channel_key] = agent
    return channel_assignments


@router.post(
    "/{channel_type}/{channel_id}/assignment/{agent_id}", responses={
        200: {
            "model": mdl.ChannelInformation,
            "description": "Channel assigned successfully"},
        404: {
            "model": mdl.ErrorResponse,
            "description": "The channel or agent doesn't exist"},
        409: {
            "model": mdl.ErrorResponse,
            "description": "The channel has already been assigned"},})
async def assign_channel(
        channel_pool: dep.ChannelPool, agent: dep.Agent, channel_type: str,
        channel_id: str) -> mdl.ChannelInformation:
    """
    Assign a channel to an agent.

    Channels associated with accounts need to be assigned to an agent so they
    can use it. A channel can only be assigned to one agent at a time.
    """
    try:
        channel_status = channel_pool.acquire(channel_type, channel_id)
    except chan.NoSuchChannelError:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f"Channel {channel_type}:{channel_id} doesn't exist.")
    except chan.ChannelStateError:
        raise fastapi.HTTPException(
            status_code=409, detail="Channel has already been assigned.")
    await agent.add_channel(channel_status.channel)
    return mdl.ChannelInformation(
        type=channel_status.channel.type,
        id=channel_status.channel.id,
        config=channel_status.config,
        status=await channel_status.channel.status,
        assigned_to_agent=agent.information.id,
    )


@router.delete(
    "/{channel_type}/{channel_id}/assignment/{agent_id}", responses={
        204: {"description": "Assignment deleted successfully"},
        404: {
            "model": mdl.ErrorResponse,
            "description": "The channel or agent doesn't exist"},
        409: {
            "model": mdl.ErrorResponse,
            "description": "The agent has no such channel assigned"},})
async def unassign_channel(
        channel_pool: dep.ChannelPool, agent: dep.Agent, channel_type: str,
        channel_id: str) -> None:
    """Remove an assignment of a channel from an agent."""
    try:
        channel = agent.channels[channel_type]
    except KeyError:
        raise fastapi.HTTPException(
            status_code=409,
            detail=f"The agent has no channel of type {channel_type}.")
    if channel.id != channel_id:
        raise fastapi.HTTPException(
            status_code=409,
            detail=f"The agent has channel of type {channel_type}, but with a "
            f"different ID ({channel.id}).")
    await agent.remove_channel(channel_type)
    try:
        channel_pool.release(channel)
    except (chan.NoSuchChannelError, chan.ChannelStateError):
        logger.exception(
            f"Removed {channel} from {agent}, but the channel pool doesn't "
            "know the channel or it wasn't assigned.")
    return fastapi.Response(status_code=204)
