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
# You should have received a copy of the GNU Affero General Public License along
# with clawp. If not, see <https://www.gnu.org/licenses/>.

import asyncio
import collections.abc as cl_abc
import contextlib
import typing as t

import fastapi
import uvicorn
import whenever as we

import message as msg

from . import dependency as dep
from . import model

router = fastapi.APIRouter(prefix="/api/v1")


async def _message_to_model(message: msg.Message) -> model.Message:
    metadata = model.MessageMetadata(
        time=await message.time,
        seq_in_session=message.metadata.seq_in_session)
    message_kwargs = {"metadata": metadata, "content": await message.content}
    if isinstance(message, msg.DeveloperMessage):
        return model.DeveloperMessage(**message_kwargs)
    elif isinstance(message, msg.SystemMessage):
        return model.SystemMessage(**message_kwargs)
    elif isinstance(message, msg.UserMessage):
        return model.UserMessage(**message_kwargs)
    elif isinstance(message, msg.ToolMessage):
        message_kwargs["tool_call_id"] = message.tool_call_id
        return model.ToolMessage(**message_kwargs)
    else:
        assert isinstance(message, msg.AssistantMessage)
        tool_calls = []
        for tool_call in await message.tool_calls:
            tool_calls.append(_tool_call_to_model(tool_call))
        message_kwargs["reasoning"] = await message.reasoning
        message_kwargs["tool_calls"] = tool_calls
        message_kwargs["errors"] = [
            f"Error: {exc}" for exc in await message.errors]
        return model.AssistantMessage(**message_kwargs)


def _tool_call_to_model(tool_call: msg.ToolCall) -> model.ToolCall:
    return model.ToolCall(
        id=tool_call.id, function=model.ToolCallFunction(
            name=tool_call.function.name,
            arguments=tool_call.function.arguments))


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/messages")
async def get_messages(
        consciousness: dep.Consciousness, ge_time: we.Instant = we.Instant.MIN,
        lt_seq: int = 2**64) -> list[model.Message]:
    """
    Get a list of messages.

    Optionally, filter by ge_time (only messages with time greater or equal,
    ISO 8601 format), or lt_seq (only messages with a sequence number less than
    the given one).
    """
    result = []
    for message in consciousness._sessions[-1]._messages:
        if message.metadata.seq_in_session >= lt_seq:
            break
        if await message.time >= ge_time:
            result.append(await _message_to_model(message))
    return result


@router.websocket("/stream")
async def websocket_stream(
        websocket: fastapi.WebSocket,
        consciousness: dep.ConsciousnessWs) -> None:
    """
    Open a websocket to stream messages.

    Each payload sent by the server will be a JSON object containing a
    WebSocketChunk. For most message roles, a chunk will contain the full
    message just as in the /messages endpoint.

    Assistant messages are streamed. They consist of parts of different types,
    each of which consists of fragments. Only one message is streamed at a time
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
    """
    await websocket.accept()
    try:
        async for message in consciousness.subscribe():
            async for chunk in _generate_message_chunks(message):
                # For some reason, we have to schedule the send as a task and
                # then immediately await that task. If we just await the send,
                # this loop will sometimes block until the full message content
                # is available (this happens in streaming assistant messages,
                # where the reasoning will stream fine, but then this loop will
                # only see the first chunk of the content once the entire
                # content has been received).
                send_task = asyncio.create_task(
                    websocket.send_json(chunk.model_dump()))
                await send_task
    except fastapi.WebSocketDisconnect:
        return


async def _generate_message_chunks(
        message: msg.Message) -> cl_abc.AsyncGenerator[model.WebsocketChunk]:
    if not isinstance(message, msg.AssistantMessage):
        message_model = await _message_to_model(message)
        yield model.WebsocketChunkFullMessage(payload=message_model)
        return
    # At this point, it's a streaming assistant message.
    start_metadata = model.StartMessageMetadata(
        seq_in_session=message.metadata.seq_in_session)
    yield model.WebsocketChunkAssistantMessageMarker(
        payload=model.StreamingMessageMarkerMessageStart(
            metadata=start_metadata))
    async for message_part in message.stream_parts():
        yield model.WebsocketChunkAssistantMessageMarker(
            payload=model.StreamingMessageMarkerPartStart(
                part_type=message_part.type))
        if isinstance(message_part, msg.AssistantMessageTextPart):
            fragment_gen = _generate_text_fragments(message_part)
        elif isinstance(message_part, msg.AssistantMessageErrorPart):
            fragment_gen = _generate_error_fragments(message_part)
        else:
            assert isinstance(message_part, msg.AssistantMessageToolPart)
            fragment_gen = _generate_tool_call_fragments(message_part)
        async for fragment in fragment_gen:
            yield model.WebsocketChunkAssistantMessageFragment(
                payload=fragment)
        yield model.WebsocketChunkAssistantMessageMarker(
            payload=model.StreamingMessageMarkerPartEnd())
    end_metadata = model.EndMessageMetadata(time=await message.time)
    yield model.WebsocketChunkAssistantMessageMarker(
        payload=model.StreamingMessageMarkerMessageEnd(metadata=end_metadata))


async def _generate_text_fragments(
    message_part: msg.AssistantMessageTextPart
) -> cl_abc.AsyncGenerator[model.StreamingMessageFragmentText]:
    async for fragment in message_part.stream_fragments():
        yield model.StreamingMessageFragmentText(fragment=fragment)


async def _generate_error_fragments(
    message_part: msg.AssistantMessageErrorPart
) -> cl_abc.AsyncGenerator[model.StreamingMessageFragmentText]:
    async for exc in message_part.stream_fragments():
        yield model.StreamingMessageFragmentText(fragment=f"Error: {exc}")


async def _generate_tool_call_fragments(
    message_part: msg.AssistantMessageToolPart
) -> cl_abc.AsyncGenerator[model.StreamingMessageFragmentToolCall]:
    async for tool_call in message_part.stream_fragments():
        yield model.StreamingMessageFragmentToolCall(
            fragment=_tool_call_to_model(tool_call))


class Api:
    def __init__(
            self, consciousness: msg.Consciousness, host: str = "127.0.0.1",
            port: int = 8000, log_level: str = "info") -> None:
        app = fastapi.FastAPI()
        app.state.consciousness = consciousness
        app.include_router(router)
        config = uvicorn.Config(
            app=app, host=host, port=port, log_level=log_level)
        self._server = uvicorn.Server(config)
        self._serve_task: t.Optional[asyncio.Task[None]] = None

    async def __aenter__(self) -> "Api":
        self._serve_task = asyncio.create_task(self._server.serve())

        while not self._server.started:
            if self._serve_task.done():
                await self._serve_task
                raise RuntimeError(
                    "API server exited before startup completed")
            await asyncio.sleep(0.05)

        return self

    async def __aexit__(self, *_) -> bool:
        self._server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await self._serve_task
        return False
