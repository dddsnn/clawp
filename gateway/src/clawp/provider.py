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
import typing as t

import mcp.types
import openrouter
import openrouter.components as or_comp
import openrouter.errors
import openrouter.utils.eventstreaming as or_stream

from . import message as msg
from . import model as mdl
from . import util


class ProviderError(Exception):
    """Base exception for errors with the provider."""


class MessageStreamError(ProviderError):
    """Raised when there is an error streaming a message."""


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
        self,
        message_parts: util.StreamableList[msg.AgentMessagePart],
        messages: cl_abc.Iterable[msg.Message[msg.MessageMetadata]],
        tools: cl_abc.Iterable[mcp.types.Tool],
    ) -> cl_abc.Awaitable[None]:
        """
        Stream an agent response.

        Request the response of the agent to the context given by the messages,
        and provide a coroutine that streams the parts into the list of message
        parts.

        :param message_parts: The list of message parts into which the result
            should be streamed.
        :param messages: The messages making up the current context.
        :param tools: An iterable of tools that should be made available to the
            agent.
        :return: A coroutine that streams the response into the message parts
            list.
        """
        raise NotImplementedError


class OpenrouterRequestError(ProviderError):
    """Raised when there is an error in a request to Openrouter."""


class OpenrouterChunkError(MessageStreamError):
    """Raised when there is an error in an Openrouter message chunk."""

    def __init__(self, message: str, error_code: int) -> None:
        super().__init__(f"error {error_code}: {message}")
        self.error_code = error_code


class OpenrouterProvider(Provider):
    def __init__(self, config: mdl.OpenRouterConfig):
        self._config = config
        self._openrouter_client = openrouter.OpenRouter(
            api_key=self._config.api_key.value
        )
        self._schema_strict_compliance_cache = {}

    async def __aenter__(self):
        await self._openrouter_client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._openrouter_client.__aexit__(*args)

    async def stream_agent_message(
        self,
        message_parts: util.StreamableList[msg.AgentMessagePart],
        messages: cl_abc.Iterable[msg.Message[msg.MessageMetadata]],
        tools: cl_abc.Iterable[mcp.types.Tool],
    ) -> cl_abc.Awaitable[None]:
        try:
            stream = await self._openrouter_client.chat.send_async(
                messages=await self._as_openrouter_messages(messages),
                model=self._config.model.name,
                tools=self._as_openrouter_tools(tools),
                stream=True,
            )
        except openrouter.errors.BadRequestResponseError as e:
            raise OpenrouterRequestError(
                f"error {e.data.error.code} in request: {e.data.error.message}"
            ) from e
        except Exception as e:
            raise OpenrouterRequestError("error in request") from e
        stream_reader = OpenrouterStreamReader(message_parts, stream)
        return stream_reader.read_message()

    async def _as_openrouter_messages(
        self, messages: cl_abc.Iterable[msg.Message[t.Any]]
    ) -> list[or_comp.ChatMessages]:
        openrouter_messages = []
        for message in messages:
            if isinstance(message, msg.AgentMessage):
                openrouter_message = (
                    await self._create_openrouter_assistant_message(message)
                )
            elif isinstance(message, msg.DeveloperMessage):
                openrouter_message = or_comp.ChatDeveloperMessage(
                    role=message.role, content=await message.content
                )
            elif isinstance(message, msg.SystemMessage):
                openrouter_message = or_comp.ChatSystemMessage(
                    role=message.role, content=await message.content
                )
            elif isinstance(message, msg.ToolMessage):
                openrouter_message = or_comp.ChatToolMessage(
                    role=message.role,
                    content=await message.content,
                    tool_call_id=message.tool_call_id,
                )
            elif isinstance(message, msg.UserMessage):
                openrouter_message = or_comp.ChatUserMessage(
                    role=message.role, content=await message.content
                )
            else:
                raise ValueError(f"invalid message role {message.role}")  # noqa: TRY004
            openrouter_messages.append(openrouter_message)
        return openrouter_messages

    @staticmethod
    async def _create_openrouter_assistant_message(
        message: msg.AgentMessage,
    ) -> or_comp.AssistantMessage:
        tool_calls = []
        for tc in await message.tool_calls:
            function = or_comp.ChatToolCallFunction(
                name=tc.function.name, arguments=tc.function.arguments
            )
            tool_calls.append(
                or_comp.ChatToolCall(
                    id=tc.id, type="function", function=function
                )
            )
        return or_comp.ChatAssistantMessage(
            role="assistant",
            content=await message.content,
            reasoning=await message.reasoning,
            tool_calls=tool_calls,
        )

    def _as_openrouter_tools(
        self, tools: cl_abc.Iterable[mcp.types.Tool]
    ) -> list[or_comp.ChatFunctionToolFunction]:
        openrouter_tools: list[or_comp.ChatFunctionToolFunction] = []
        for tool in tools:
            function = or_comp.ChatFunctionToolFunctionFunction(
                name=tool.name,
                description=tool.description,
                parameters=tool.inputSchema,
                strict=self._tool_schema_is_strict_compliant(tool),
            )
            openrouter_tools.append(
                or_comp.ChatFunctionToolFunction(
                    type="function", function=function
                )
            )
        return openrouter_tools

    def _tool_schema_is_strict_compliant(self, tool: mcp.types.Tool) -> bool:
        """Check if a tool schema is compatible with strict adherence."""
        try:
            return self._schema_strict_compliance_cache[tool.name]
        except KeyError:
            return self._schema_strict_compliance_cache.setdefault(
                tool.name, self._schema_is_strict_compliant(tool.inputSchema)
            )

    def _schema_is_strict_compliant(self, schema: t.Any) -> bool:
        if not isinstance(schema, dict):
            return False
        # oneOf and allOf are forbidden.
        if "oneOf" in schema or "allOf" in schema:
            return False

        # anyOf is only allowed for simple type unions.
        try:
            any_of = schema["anyOf"]
            if not isinstance(any_of, list):
                return False
            for sub_schema in any_of:
                is_simple_type_dict = (
                    isinstance(sub_schema, dict)
                    and len(sub_schema) == 1
                    and "type" in sub_schema
                    and isinstance(sub_schema["type"], str)
                )
                if not is_simple_type_dict:
                    return False
        except KeyError:
            pass

        # For objects, additionalProperties must explicitly be False, and all
        # properties must be required.
        if schema.get("type") == "object" or "properties" in schema:
            if schema.get("additionalProperties") is not False:
                return False
            props = schema.get("properties", {})
            req = set(schema.get("required", []))
            if set(props.keys()) != req:
                return False
            # Recursively check sub-properties.
            for prop_schema in props.values():
                if not self._schema_is_strict_compliant(prop_schema):
                    return False

        # For arrays: inspect items.
        try:
            items = schema["items"]
            if isinstance(items, dict):
                if not self._schema_is_strict_compliant(items):
                    return False
            elif isinstance(items, list):
                for item in items:
                    if not self._schema_is_strict_compliant(item):
                        return False
        except KeyError:
            pass

        return True


class OpenrouterStreamReader:
    """
    Reader for an Openrouter stream.

    This class handles one stream. It is stateful and can't be reused.
    """

    def __init__(
        self,
        message_parts: util.StreamableList[msg.AgentMessagePart],
        stream: or_stream.EventStreamAsync[or_comp.ChatStreamChunk],
    ):
        self._logger = logging.getLogger(type(self).__name__)
        self._message_parts = message_parts
        self._stream = stream
        self._tool_calls_kwargs: dict[int, dict[str, str]] = {}
        self._saw_assistant_role = False

    async def read_message(self) -> None:
        """
        Read the message from the stream.

        Consumes the reader's stream and appends message parts to the reader's
        list.

        Only raises exceptions that happen with reading the stream. Any errors
        with the response itself like unexpected format are appended as error
        parts.
        """
        try:
            finish_reasons = await self._read_stream_chunks()
            await self._check_finish_reasons(finish_reasons)
            await self._append_tool_calls()
            if not self._message_parts:
                await self._append_to_part(
                    msg.AgentMessageErrorPart,
                    MessageStreamError(
                        "stream ended without any payload or error"
                    ),
                )
            has_payload = any(
                isinstance(
                    part, (msg.AgentMessageTextPart, msg.AgentMessageToolPart)
                )
                for part in self._message_parts
            )
            if has_payload and not self._saw_assistant_role:
                await self._append_to_part(
                    msg.AgentMessageErrorPart,
                    MessageStreamError(
                        "assistant role missing in all stream chunks"
                    ),
                )
        except (Exception, asyncio.CancelledError) as e:
            self._logger.exception("Error in stream.")
            if isinstance(e, asyncio.CancelledError):
                exc_to_raise = asyncio.CancelledError("stream was cancelled")
                exc_to_raise.__cause__ = e
            else:
                exc_to_raise = e
            await self._append_to_part(msg.AgentMessageErrorPart, e)
            raise exc_to_raise
        finally:
            try:
                # Make sure the last part is finalized.
                await self._message_parts[-1].finalize()
            except IndexError:
                pass
            await self._message_parts.finalize()

    async def _read_stream_chunks(self):
        finish_reasons = set()
        async for chunk in self._stream:
            for part_type, payload in self._parse_chunk(chunk):
                if part_type == "reasoning":
                    await self._append_to_part(
                        msg.AgentMessageReasoningPart, payload
                    )
                elif part_type == "content":
                    await self._append_to_part(
                        msg.AgentMessageContentPart, payload
                    )
                elif part_type == "error":
                    await self._append_to_part(
                        msg.AgentMessageErrorPart, payload
                    )
                else:
                    assert part_type == "finish_reason"
                    finish_reasons.add(payload)
        return finish_reasons

    def _parse_chunk(self, chunk: or_comp.ChatStreamChunk):
        if not isinstance(chunk, or_comp.ChatStreamChunk):  # pyright: ignore[reportUnnecessaryIsInstance]
            yield (  # pyright: ignore[reportUnreachable]
                "error",
                MessageStreamError(
                    f"unexpected chunk type {type(chunk)} in stream"
                ),
            )
            return
        if chunk.error:
            yield (
                "error",
                OpenrouterChunkError(chunk.error.message, chunk.error.code),
            )
        if len(chunk.choices) == 0:
            self._logger.debug("Received stream chunk with 0 choices.")
            return
        elif len(chunk.choices) != 1:
            yield (
                "error",
                MessageStreamError(
                    f"unexpected number of choices ({len(chunk.choices)}) in "
                    "chunk"
                ),
            )
            return
        yield from self._parse_chunk_choice(chunk.choices[0])

    def _parse_chunk_choice(self, choice):
        delta = choice.delta
        if delta.role == "assistant":
            self._saw_assistant_role = True
        elif delta.role is not None:
            yield (
                "error",
                MessageStreamError(
                    f"unexpected role {delta.role} in assistant message"
                ),
            )
        elif not self._saw_assistant_role:
            self._logger.debug(
                "Received stream chunk without role before an assistant role "
                "has been observed. Waiting for later chunks."
            )
        self._parse_chunk_tool_calls(delta)
        if delta.refusal:
            yield (
                "error",
                AgentRefusalError(
                    f"provider refused request: {delta.refusal}"
                ),
            )
        if delta.reasoning:
            yield "reasoning", delta.reasoning
        if delta.content:
            yield "content", delta.content
        if choice.finish_reason is not None:
            yield "finish_reason", choice.finish_reason

    def _parse_chunk_tool_calls(self, delta):
        for tool_call in delta.tool_calls or []:
            tool_call_kwargs = self._tool_calls_kwargs.setdefault(
                tool_call.index, {}
            )
            tool_call_kwargs.setdefault("id", "")
            tool_call_kwargs.setdefault("name", "")
            tool_call_kwargs.setdefault("arguments", "")
            tool_call_kwargs["id"] += tool_call.id or ""
            if tool_call.function is None:
                self._logger.debug(
                    "Received tool-call chunk without function payload at "
                    f"index {tool_call.index}, waiting for subsequent chunks."
                )
            else:
                tool_call_kwargs["name"] += tool_call.function.name or ""
                tool_call_kwargs["arguments"] += (
                    tool_call.function.arguments or ""
                )

    async def _append_tool_calls(self):
        for _, tool_call_kwargs in sorted(self._tool_calls_kwargs.items()):
            function = msg.ToolCallFunction(
                name=tool_call_kwargs["name"],
                arguments=tool_call_kwargs["arguments"],
            )
            await self._append_to_part(
                msg.AgentMessageToolPart,
                msg.ToolCall(id=tool_call_kwargs["id"], function=function),
            )

    async def _check_finish_reasons(self, finish_reasons: set[str]):
        if len(finish_reasons) > 1:
            self._logger.warning(
                f"Received more than one finish_reason: {finish_reasons}."
            )
        for finish_reason in finish_reasons:
            if finish_reason == "stop":
                continue
            if finish_reason == "tool_calls":
                if self._tool_calls_kwargs:
                    continue
                await self._append_to_part(
                    msg.AgentMessageErrorPart,
                    FinishReasonError(
                        "finish_reason=tool_calls but no tool calls were "
                        "received in stream",
                        finish_reason,
                    ),
                )
                continue
            if finish_reason in {"length", "content_filter", "error"}:
                await self._append_to_part(
                    msg.AgentMessageErrorPart,
                    FinishReasonError(
                        f"provider returned finish_reason {finish_reason}",
                        finish_reason,
                    ),
                )
                continue
            self._logger.warning(
                f"Received unknown finish_reason {finish_reason}."
            )

    async def _append_to_part(self, part_type, payload):
        try:
            if not isinstance(self._message_parts[-1], part_type):
                await self._message_parts[-1].finalize()
                await self._message_parts.append(part_type())
        except IndexError:
            await self._message_parts.append(part_type())
        await self._message_parts[-1].append(payload)
