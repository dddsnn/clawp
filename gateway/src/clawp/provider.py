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

import abc
import asyncio
import collections.abc as cl_abc
import logging

import fastmcp.tools
import openrouter
import openrouter.components as or_comp
import openrouter.utils.eventstreaming as or_stream

from . import message as msg
from . import model as mdl
from . import util


class ProviderError(Exception):
    """Base exception for errors with the provider."""


class MessageStreamError(ProviderError):
    """Raised when there is an error streaming a message."""


class ChunkError(MessageStreamError):
    """Raised when there is an error in a message chunk."""
    def __init__(self, message: str, error_code: int) -> None:
        super().__init__(f"error {error_code}: {message}")
        self.error_code = error_code


class AgentRefusalError(ProviderError):
    """Error signalling that the provider refused a request."""


class FinishReasonError(MessageStreamError):
    """Error signalling a finish_reason indicating non-successful output."""
    def __init__(self, message: str, finish_reason: str) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason


class Provider(abc.ABC):
    """
    Provider of LLM chat completions.

    Abstract provider capable of generating an AgentMessage in response to a
    context of messages.
    """
    @abc.abstractmethod
    async def stream_agent_message(
            self, message_parts: util.StreamableList,
            messages: cl_abc.Iterable[msg.Message],
            tools: cl_abc.Iterable[fastmcp.tools.Tool]) -> asyncio.Task[None]:
        """
        Stream an agent response.

        Request the response of the agent to the context given by the messages,
        and stream the parts into the list of message parts.

        :param message_parts: The list of message parts into which the result
            should be streamed.
        :param messages: The messages making up the current context.
        :param tools: An iterable of tools that should be made available to the
            agent.
        :return: A task that is done when the message is complete.
        """
        raise NotImplementedError


OpenRouterMessage = (
    or_comp.ChatAssistantMessage | or_comp.ChatDeveloperMessage
    | or_comp.ChatSystemMessage
    | or_comp.ChatToolMessage | or_comp.ChatUserMessage)


class OpenrouterProvider(Provider):
    def __init__(self, config: mdl.OpenRouterConfig):
        self._config = config
        self._openrouter_client = openrouter.OpenRouter(
            api_key=self._config.api_key)

    async def __aenter__(self):
        await self._openrouter_client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._openrouter_client.__aexit__(*args)

    async def stream_agent_message(
            self, message_parts: util.StreamableList,
            messages: cl_abc.Iterable[msg.Message],
            tools: cl_abc.Iterable[fastmcp.tools.Tool]) -> asyncio.Task[None]:
        stream = await self._openrouter_client.chat.send_async(
            messages=await self._as_openrouter_messages(messages),
            model=self._config.model.name,
            tools=self._as_openrouter_tools(tools), stream=True)
        stream_reader = OpenrouterStreamReader(message_parts, stream)
        return stream_reader.read_message()

    async def _as_openrouter_messages(
            self,
            messages: cl_abc.Iterable[msg.Message]) -> list[OpenRouterMessage]:
        openrouter_messages = []
        for message in messages:
            if message.role == "agent":
                openrouter_message = (
                    await self._create_openrouter_assistant_message(message))
            elif message.role == "developer":
                openrouter_message = or_comp.ChatDeveloperMessage(
                    role=message.role, content=await message.content)
            elif message.role == "system":
                openrouter_message = or_comp.ChatSystemMessage(
                    role=message.role, content=await message.content)
            elif message.role == "tool":
                openrouter_message = or_comp.ChatToolMessage(
                    role=message.role, content=await message.content,
                    tool_call_id=message.tool_call_id)
            elif message.role == "user":
                openrouter_message = or_comp.ChatUserMessage(
                    role=message.role, content=await message.content)
            else:
                raise ValueError(f"invalid message role {message.role}")
            openrouter_messages.append(openrouter_message)
        return openrouter_messages

    @staticmethod
    async def _create_openrouter_assistant_message(
            message: msg.AgentMessage) -> or_comp.AssistantMessage:
        tool_calls = []
        for tc in await message.tool_calls:
            function = or_comp.ChatToolCallFunction(
                name=tc.function.name, arguments=tc.function.arguments)
            tool_calls.append(
                or_comp.ChatToolCall(
                    id=tc.id, type="function", function=function))
        return or_comp.ChatAssistantMessage(
            role="assistant", content=await message.content, reasoning=await
            message.reasoning, tool_calls=tool_calls)

    def _as_openrouter_tools(
        self, tools: cl_abc.Iterable[fastmcp.tools.Tool]
    ) -> list[or_comp.ChatFunctionToolFunction]:
        return [
            or_comp.ChatFunctionToolFunction(
                type="function",
                function=or_comp.ChatFunctionToolFunctionFunction(
                    name=t.name, description=t.description,
                    parameters=t.inputSchema, strict=True)) for t in tools]


class OpenrouterStreamReader:
    TIMEOUT = 120

    def __init__(
            self, message_parts: util.StreamableList,
            stream: or_stream.EventStreamAsync):
        self._logger = logging.getLogger(type(self).__name__)
        self._message_parts = message_parts
        self._stream = stream
        self._saw_assistant_role = False

    def read_message(self) -> asyncio.Task[None]:
        return asyncio.create_task(
            asyncio.wait_for(self._read_stream(), timeout=self.TIMEOUT))

    async def _read_stream(self) -> None:
        try:
            tool_calls_kwargs = {}
            saw_response_payload = False
            saw_error_payload = False
            finish_reasons = []
            async for chunk in self._stream:
                part_type, text, finish_reason = self._parse_chunk(
                    chunk, tool_calls_kwargs)
                if finish_reason:
                    finish_reasons.append(finish_reason)
                if not part_type:
                    continue
                saw_response_payload = True
                if part_type == "reasoning":
                    current_part = await self._ensure_current_part(
                        msg.AgentMessageReasoningPart)
                else:
                    assert part_type == "content"
                    current_part = await self._ensure_current_part(
                        msg.AgentMessageContentPart)
                await current_part.append(text)
            if tool_calls_kwargs:
                tool_part = await self._ensure_current_part(
                    msg.AgentMessageToolPart)
                for _, tool_call_kwargs in sorted(tool_calls_kwargs.items()):
                    function = msg.ToolCallFunction(
                        name=tool_call_kwargs["name"],
                        arguments=tool_call_kwargs["arguments"])
                    await tool_part.append(
                        msg.ToolCall(
                            id=tool_call_kwargs["id"], function=function))
            for finish_reason in finish_reasons:
                if finish_reason == "stop":
                    continue
                if finish_reason == "tool_calls":
                    if tool_calls_kwargs:
                        continue
                    saw_error_payload = True
                    error_part = await self._ensure_current_part(
                        msg.AgentMessageErrorPart)
                    await error_part.append(
                        FinishReasonError(
                            "finish_reason=tool_calls but no tool calls were "
                            "received in stream", finish_reason))
                    continue
                if finish_reason in {"length", "content_filter", "error"}:
                    saw_error_payload = True
                    error_part = await self._ensure_current_part(
                        msg.AgentMessageErrorPart)
                    await error_part.append(
                        FinishReasonError(
                            f"provider returned finish_reason={finish_reason}",
                            finish_reason))
                    continue
                self._logger.warning(
                    "Received unknown finish_reason from API: %s",
                    finish_reason)
            # TODO: Empty responses should likely be surfaced more explicitly,
            # but changing that cleanly probably requires downstream message /
            # UI handling changes (e.g. dedicated error/empty marker).
            if (not saw_response_payload and not tool_calls_kwargs
                    and not saw_error_payload):
                self._logger.warning(
                    "OpenRouter stream ended without content, reasoning, or "
                    "tool calls.")
            if saw_response_payload and not self._saw_assistant_role:
                raise ValueError("assistant role missing in all stream chunks")
        except (Exception, asyncio.CancelledError) as e:
            error_part = await self._ensure_current_part(
                msg.AgentMessageErrorPart)
            await error_part.append(e)
            raise e
        finally:
            try:
                # Make sure the last part is finalized.
                await self._message_parts[-1].finalize()
            except IndexError:
                pass
            await self._message_parts.finalize()

    def _parse_chunk(self, chunk, tool_calls_kwargs: dict[int, dict]):
        if not isinstance(chunk, or_comp.ChatStreamChunk):
            raise ValueError(f"unexpected chunk type {type(chunk)} in stream")
        if chunk.error:
            raise ChunkError(chunk.error.message, chunk.error.code)
        if len(chunk.choices) == 0:
            self._logger.debug("Received stream chunk with 0 choices.")
            return None, None, None
        elif len(chunk.choices) != 1:
            raise ValueError(
                f"unexpected number of choices ({len(chunk.choices)}) in "
                "chunk")
        choice = chunk.choices[0]
        delta = choice.delta
        if delta.role == "assistant":
            self._saw_assistant_role = True
        elif delta.role is not None:
            raise ValueError(
                f"unexpected role {delta.role} in assistant message")
        else:
            self._logger.debug(
                "Received stream chunk without role before an assistant role "
                "has been observed. Waiting for later chunks.")
        if delta.content and delta.reasoning:
            raise ValueError(
                "assistant message contains both content "
                f"('{delta.content}') and reasoning ('{delta.reasoning}')")
        if delta.refusal:
            raise AgentRefusalError(
                f"provider refused request: {delta.refusal}")
        for tool_call in delta.tool_calls or []:
            tool_call_kwargs = tool_calls_kwargs.setdefault(
                tool_call.index, {})
            tool_call_kwargs.setdefault("id", "")
            tool_call_kwargs.setdefault("name", "")
            tool_call_kwargs.setdefault("arguments", "")
            tool_call_kwargs["id"] += tool_call.id or ""
            if tool_call.function is None:
                self._logger.debug(
                    "Received tool-call chunk without function payload at "
                    f"index {tool_call.index}, waiting for subsequent chunks.")
            else:
                tool_call_kwargs["name"] += tool_call.function.name or ""
                tool_call_kwargs["arguments"] += (
                    tool_call.function.arguments or "")
        if not delta.content and not delta.reasoning:
            return None, None, choice.finish_reason
        part_type = "content" if delta.content else "reasoning"
        text = delta.content or delta.reasoning
        return part_type, text, choice.finish_reason

    async def _ensure_current_part(self, part_type):
        try:
            if not isinstance(self._message_parts[-1], part_type):
                await self._message_parts[-1].finalize()
                await self._message_parts.append(part_type())
        except IndexError:
            await self._message_parts.append(part_type())
        return self._message_parts[-1]
