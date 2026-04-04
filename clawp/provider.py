import abc
import asyncio
import collections.abc as cl_abc
import functools as ft
import logging

import fastmcp.tools
import message as msg
import openrouter
import openrouter.components as or_comp
import openrouter.utils.eventstreaming as or_stream


class Provider(abc.ABC):
    @abc.abstractmethod
    async def request_assistant_message(
            self, messages: list[msg.Message],
            tools: cl_abc.Iterable[fastmcp.tools.Tool]
    ) -> msg.AssistantMessage:
        raise NotImplementedError


OpenRouterMessage = (
    or_comp.ChatAssistantMessage | or_comp.ChatDeveloperMessage
    | or_comp.ChatSystemMessage
    | or_comp.ChatToolMessage | or_comp.ChatUserMessage)


class OpenrouterProvider(Provider):
    def __init__(self, api_key: str):
        self._openrouter_client = openrouter.OpenRouter(api_key=api_key)

    async def __aenter__(self):
        await self._openrouter_client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._openrouter_client.__aexit__(*args)

    async def request_assistant_message(
            self, messages: list[msg.Message],
            tools: cl_abc.Iterable[fastmcp.tools.Tool]
    ) -> msg.AssistantMessage:
        stream = await self._openrouter_client.chat.send_async(
            messages=await self._as_openrouter_messages(messages),
            model="stepfun/step-3.5-flash:free",
            tools=self._as_openrouter_tools(tools), stream=True)
        stream_reader = OpenrouterStreamReader(stream)
        return stream_reader.read_message()

    async def _as_openrouter_messages(
            self, messages: list[msg.Message]) -> list[OpenRouterMessage]:
        openrouter_messages = []
        for message in messages:
            if message.role == "assistant":
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
            message: msg.AssistantMessage) -> or_comp.AssistantMessage:
        tool_calls = []
        for tc in await message.tool_calls:
            function = or_comp.ChatToolCallFunction(
                name=tc.function.name, arguments=tc.function.arguments)
            tool_calls.append(
                or_comp.ChatToolCall(
                    id=tc.id, type="function", function=function))
        return or_comp.ChatAssistantMessage(
            role=message.role, content=await message.content, reasoning=await
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

    def __init__(self, stream: or_stream.EventStreamAsync):
        self._logger = logging.getLogger(type(self).__name__)
        self._stream = stream
        self._parts = msg.StreamableList()
        self._assistant_message = msg.AssistantMessage(self._parts)

    def read_message(self) -> msg.AssistantMessage:
        asyncio.create_task(
            asyncio.wait_for(self._read_stream(), timeout=self.TIMEOUT))
        return self._assistant_message

    async def _read_stream(self) -> None:
        try:
            tool_calls_kwargs = {}
            async for chunk in self._stream:
                part_type, text = self._parse_chunk(chunk, tool_calls_kwargs)
                if not part_type:
                    continue
                current_part = await self._ensure_current_text_part(part_type)
                await current_part.append(text)
            if tool_calls_kwargs:
                tool_part = await self._ensure_current_tool_part()
                for _, tool_call_kwargs in sorted(tool_calls_kwargs.items()):
                    function = msg.ToolCallFunction(
                        name=tool_call_kwargs["name"],
                        arguments=tool_call_kwargs["arguments"])
                    await tool_part.append(
                        msg.ToolCall(
                            id=tool_call_kwargs["id"], function=function))
        except (Exception, asyncio.CancelledError) as e:
            self._logger.exception(
                "Error reading assistant message from stream.")
            error_part = await self._ensure_current_error_part()
            await error_part.append(e)
        finally:
            try:
                # Make sure the last part is finalized.
                self._parts[-1].finalize()
            except IndexError:
                pass
            self._parts.finalize()

    def _parse_chunk(self, chunk, tool_calls_kwargs: dict[int, dict]):
        if not isinstance(chunk, or_comp.ChatStreamChunk):
            raise ValueError(f"unexpected chunk type {type(chunk)} in stream")
        if len(chunk.choices) != 1:
            raise ValueError(
                f"unexpected number of choices ({len(chunk.choices)}) in "
                "chunk")
        delta = chunk.choices[0].delta
        if delta.role != "assistant":
            raise ValueError(
                f"unexpected role {delta.role} in assistant message")
        if delta.content and delta.reasoning:
            raise ValueError(
                "assistant message contains both content "
                f"('{delta.content}') and reasoning ('{delta.reasoning}')")
        for tool_call in delta.tool_calls or []:
            tool_call_kwargs = tool_calls_kwargs.setdefault(
                tool_call.index, {})
            tool_call_kwargs.setdefault("id", "")
            tool_call_kwargs.setdefault("name", "")
            tool_call_kwargs.setdefault("arguments", "")
            tool_call_kwargs["id"] += tool_call.id or ""
            tool_call_kwargs["name"] += tool_call.function.name or ""
            tool_call_kwargs["arguments"] += (
                tool_call.function.arguments or "")
        if not delta.content and not delta.reasoning:
            return None, None
        part_type = "content" if delta.content else "reasoning"
        text = delta.content or delta.reasoning
        return part_type, text

    async def _ensure_current_text_part(self, part_type):
        return await self._ensure_current_part(
            lambda part: part.type == part_type,
            ft.partial(msg.AssistantMessageTextPart, part_type))

    async def _ensure_current_tool_part(self):
        return await self._ensure_current_part(
            lambda part: isinstance(part, msg.AssistantMessageToolPart),
            msg.AssistantMessageToolPart)

    async def _ensure_current_error_part(self):
        return await self._ensure_current_part(
            lambda part: isinstance(part, msg.AssistantMessageErrorPart),
            msg.AssistantMessageErrorPart)

    async def _ensure_current_part(self, part_is_correct_type, part_factory):
        try:
            if not part_is_correct_type(self._parts[-1]):
                self._parts[-1].finalize()
                await self._parts.append(part_factory())
        except IndexError:
            await self._parts.append(part_factory())
        return self._parts[-1]
