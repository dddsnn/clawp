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
import enum
import logging
import typing as t

import fastapi as fa
import fastapi.exceptions as fa_exc
import whenever as we

from .. import file
from .. import message as msg
from .. import model as mdl
from . import dependency as dep

if t.TYPE_CHECKING:
    from .. import agent as agt

logger = logging.getLogger(__name__)

router = fa.APIRouter(prefix="/agents")


class WebsocketCloseCode(enum.IntEnum):
    NORMAL_CLOSURE = 1000
    GOING_AWAY = 1001
    PROTOCOL_ERROR = 1002
    UNACCEPTABLE_DATA = 1003
    RESERVED_NO_CLOSE_CODE = 1005
    RESERVED_ABNORMAL_CLOSURE = 1006
    INCONSISTENT_DATA = 1007
    POLICY_VIOLATION = 1008
    MESSAGE_TOO_BIG = 1009
    MISSING_NEGOTIATION = 1010
    UNEXPECTED_CONDITION = 1011
    RESERVED_FAILED_TLS_HANDSHAKE = 1015


@router.get("")
async def list_agents(
        agent_repo: dep.AgentRepository) -> list[mdl.AgentInformation]:
    """Get a list of agents."""
    return [agent.information for agent in agent_repo.iter_agents()]


@router.get("/hatch")
async def hatch_new_agent(
        agent_repo: dep.AgentRepository,
        agent_name: t.Annotated[str, fa.Query(min_length=1)],
        personality_name: str) -> mdl.AgentInformation:
    """
    Hatch a new agent.

    Create a new agent with the given personality and return its info.
    """
    try:
        agent = await agent_repo.hatch_agent(agent_name, personality_name)
    except file.PersonalityNotFoundError:
        raise fa_exc.HTTPException(
            status_code=404,
            detail=f"No personality named {personality_name}.")
    return agent.information


@router.get("/{agent_id}/messages")
async def get_messages(
        agent: dep.Agent, ge_time: we.Instant = we.Instant.MIN,
        lt_message_seq: int = 2**64) -> list[mdl.MessageInSession]:
    """
    Get a list of messages.

    Optionally, filter by ge_time (only messages with time greater or equal,
    ISO 8601 format), or lt_message_seq (only messages with a sequence number
    less than the given one).
    """
    result = []
    for message_in_session in agent.messages():
        if message_in_session.message_offset.message_seq >= lt_message_seq:
            break
        if await message_in_session.message.metadata.time.value >= ge_time:
            result.append(
                mdl.MessageInSession(
                    message=await message_in_session.message.model,
                    message_offset=message_in_session.message_offset))
    return result


@router.websocket(
    "/{agent_id}/stream/{cachebuster_to_circumvent_reconnection_delay}")
async def websocket_stream(
        websocket: fa.WebSocket, agent: dep.Agent,
        cachebuster_to_circumvent_reconnection_delay: str) -> None:
    """
    Open a websocket to stream messages.

    Each payload sent by the server will be a JSON object containing a
    WebSocketChunk. For most message roles, a chunk will contain the full
    message just as in the /messages endpoint.

    Agent messages are streamed. They consist of parts of different types, each
    of which consists of fragments. Only one message is streamed at a time
    (i.e. a message's stream must finish before another one can start). It is a
    stateful protocol:
        - a message marker is sent signalling the start of the message,
          including some metadata
        - each part start with a message marker signalling its start, including
          the type of the part
        - the following chunks are the fragments of the part, their type
          depending on the type of the part
        - each part ends in a message marker signalling its end
        - after all parts have been sent, another message marker signals the
          end of the message, including some final metadata

    The websocket can receive new user messages which will be appended to the
    agent's session and prompt a response. These must be JSON objects
    conforming to the UserInputMessage model.

    The cachebuster_to_circumvent_reconnection_delay path parameter is ignored
    and can be any value. It is there to provide a mechanism to circumvent
    Firefox's (and possibly other browsers') builtin websocket reconnection
    delay. If connecting to a websocket fails repeatedly, Firefox will impose
    delays that are outside the control of the application, leading to very
    long annoying wait times. Adding a path parameter that can change between
    requests circumvents this restriction.
    """
    await websocket.accept()
    send_task = asyncio.create_task(
        _send_websocket(websocket, agent.subscribe()))
    try:
        while True:
            input_json = await websocket.receive_json()
            await _handle_websocket_input(input_json, agent)
    except fa.WebSocketDisconnect:
        # The client closed the connection.
        return
    except asyncio.CancelledError:
        # The server is shutting down.
        await _try_close_websocket(websocket, WebsocketCloseCode.GOING_AWAY)
        raise
    except Exception:
        logger.exception("Error in websocket.")
        await _try_close_websocket(
            websocket, WebsocketCloseCode.UNEXPECTED_CONDITION)
    finally:
        send_task.cancel()
        await send_task


async def _send_websocket(
        websocket: fa.WebSocket,
        message_iter: cl_abc.AsyncIterable["agt.MessageInSession"]) -> None:
    try:
        async for message_in_session in message_iter:
            async for chunk in _generate_message_chunks(message_in_session):
                # For some reason, we have to schedule the send as a task and
                # then immediately await that task. If we just await the send,
                # this loop will sometimes block until the full message content
                # is available (this happens in streaming agent messages, where
                # the reasoning will stream fine, but then this loop will only
                # see the first chunk of the content once the entire content
                # has been received).
                send_task = asyncio.create_task(
                    websocket.send_text(chunk.model_dump_json()))
                await send_task
    except asyncio.CancelledError:
        return


async def _generate_message_chunks(
    message_in_session: "agt.MessageInSession"
) -> cl_abc.AsyncGenerator[mdl.WebsocketChunk]:
    if not isinstance(message_in_session.message, msg.AgentMessage):
        yield mdl.WebsocketChunkFullMessage(
            payload=mdl.MessageInSession(
                message=await message_in_session.message.model,
                message_offset=message_in_session.message_offset))
        return
    # At this point, it's a streaming agent message.
    yield mdl.WebsocketChunkAgentMessageMarker(
        payload=mdl.StreamingMessageMarkerMessageStart(
            metadata=message_in_session.message.metadata.start_model,
            message_offset=message_in_session.message_offset))
    async for message_part in message_in_session.message.stream_parts():
        yield mdl.WebsocketChunkAgentMessageMarker(
            payload=mdl.StreamingMessageMarkerPartStart(
                part_type=message_part.type))
        if isinstance(message_part, msg.AgentMessageTextPart):
            fragment_gen = _generate_text_fragments(message_part)
        elif isinstance(message_part, msg.AgentMessageErrorPart):
            fragment_gen = _generate_error_fragments(message_part)
        else:
            assert isinstance(message_part, msg.AgentMessageToolPart)
            fragment_gen = _generate_tool_call_fragments(message_part)
        async for fragment in fragment_gen:
            yield mdl.WebsocketChunkAgentMessageFragment(payload=fragment)
        yield mdl.WebsocketChunkAgentMessageMarker(
            payload=mdl.StreamingMessageMarkerPartEnd())
    end_metadata = mdl.EndMessageMetadata(
        time=await message_in_session.message.metadata.time.value)
    yield mdl.WebsocketChunkAgentMessageMarker(
        payload=mdl.StreamingMessageMarkerMessageEnd(metadata=end_metadata))


async def _generate_text_fragments(
    message_part: msg.AgentMessageTextPart
) -> cl_abc.AsyncGenerator[mdl.StreamingMessageFragmentText]:
    async for fragment in message_part.stream_fragments():
        yield mdl.StreamingMessageFragmentText(fragment=fragment)


async def _generate_error_fragments(
    message_part: msg.AgentMessageErrorPart
) -> cl_abc.AsyncGenerator[mdl.StreamingMessageFragmentError]:
    async for exc in message_part.stream_fragments():
        yield mdl.StreamingMessageFragmentError(
            fragment=msg.AgentMessage.error_model(exc))


async def _generate_tool_call_fragments(
    message_part: msg.AgentMessageToolPart
) -> cl_abc.AsyncGenerator[mdl.StreamingMessageFragmentToolCall]:
    async for tool_call in message_part.stream_fragments():
        yield mdl.StreamingMessageFragmentToolCall(fragment=tool_call.model)


async def _handle_websocket_input(input_json, agent: "agt.Agent") -> None:
    input_message = mdl.UserInputMessageTypeAdapter.validate_python(input_json)
    if isinstance(input_message, mdl.UserInputMessageContent):
        await agent.web_ui_channel.add_incoming_user_message(
            we.Instant.now(), input_message.content)
    else:
        assert isinstance(input_message, mdl.UserInputMessageRequestResponse)
        await agent.request_response()


async def _try_close_websocket(
        websocket: fa.WebSocket, close_code: WebsocketCloseCode) -> None:
    try:
        async with asyncio.timeout(5):
            await websocket.close(code=close_code)
    except Exception:
        logger.exception("Error while trying to close the websocket.")
