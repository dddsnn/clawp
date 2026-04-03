import abc
import asyncio
import collections.abc as cl_abc

import fastmcp.tools
import message as msg
import openrouter
import openrouter.components as or_comp


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
        parts = msg.StreamableList()
        asyncio.create_task(self._read_stream(stream, parts))
        return msg.AssistantMessage(parts)

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
                raise ValueError(f"Invalid message role {message.role}.")
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

    async def _read_stream(
            self, stream: or_comp.EventStreamAsync,
            parts: msg.StreamableList) -> None:
        tool_calls_kwargs = {}
        async for chunk in stream:
            if not isinstance(chunk, or_comp.ChatStreamChunk):
                raise ValueError(
                    f"Unexpected chunk type {type(chunk)} in stream.")
            if len(chunk.choices) != 1:
                raise ValueError(
                    f"Unexpected number of choices ({len(chunk.choices)}) in "
                    "chunk.")
            delta = chunk.choices[0].delta
            if delta.role != "assistant":
                raise ValueError(
                    f"Unexpected role {delta.role} in assistant message.")
            if delta.content and delta.reasoning:
                raise ValueError(
                    "Assistant message contains both content "
                    f"('{delta.content}') and reasoning ('{delta.reasoning}')."
                )
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
                continue
            part_type = "content" if delta.content else "reasoning"
            text = delta.content or delta.reasoning
            try:
                if parts[-1].type != part_type:
                    parts[-1].finalize()
                    await parts.append(msg.AssistantMessageTextPart(part_type))
            except IndexError:
                await parts.append(msg.AssistantMessageTextPart(part_type))
            await parts[-1].append(text)
        try:
            # All parts have been parsed, now also finalize the last one.
            parts[-1].finalize()
        except IndexError:
            pass
        if tool_calls_kwargs:
            await parts.append(msg.AssistantMessageToolPart())
            for _, tool_call_kwargs in sorted(tool_calls_kwargs.items()):
                function = msg.ToolCallFunction(
                    name=tool_call_kwargs["name"],
                    arguments=tool_call_kwargs["arguments"])
                await parts[-1].append(
                    msg.ToolCall(id=tool_call_kwargs["id"], function=function))
            parts[-1].finalize()
        parts.finalize()
