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

import openrouter.components as or_comp
from hamcrest import (
    all_of,
    assert_that,
    contains_exactly,
    has_entries,
    has_item,
    has_properties,
    instance_of,
)

from clawp import provider as prov
from clawp import util


class FakeAsyncStream:
    def __init__(self, items: list[t.Any]):
        self._items_iter = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            item = next(self._items_iter)
        except StopIteration as e:
            raise StopAsyncIteration from e
        if isinstance(item, Exception):
            raise item
        return item


class TestOpenrouterStreamReader:
    @staticmethod
    def make_chunk(
        *,
        delta: or_comp.ChatStreamDelta | None = None,
        finish_reason: str | None = None,
        choices: list[or_comp.ChatStreamChoice] | None = None,
    ) -> or_comp.ChatStreamChunk:
        return or_comp.ChatStreamChunk(
            choices=choices or [
                or_comp.ChatStreamChoice(
                    index=0,
                    finish_reason=finish_reason,
                    delta=delta or or_comp.ChatStreamDelta(role="assistant"),
                )],
            created=0,
            id="chunk-id",
            model="test-model",
            object="chat.completion.chunk",
        )

    @staticmethod
    async def run_reader(
        items: list[t.Any],) -> tuple[list[dict], Exception | None]:
        message_parts_streamable = util.StreamableList()
        reader = prov.OpenrouterStreamReader(
            message_parts_streamable, FakeAsyncStream(items))
        stream_error = None
        stream_task = reader.read_message()
        try:
            await stream_task
        except Exception as e:
            stream_error = e

        message_parts = []
        for part in message_parts_streamable:
            fragments = [
                fragment async for fragment in part.stream_fragments()]
            message_parts.append({"type": part.type, "fragments": fragments})
        return message_parts, stream_error

    async def test_read_message_handles_empty_stream(self):
        message_parts, error = await self.run_reader([])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        instance_of(prov.MessageStreamError)),
                )))

    async def test_read_message_adds_error_part_on_missing_role(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(delta=or_comp.ChatStreamDelta(content="hello")),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(type="content", fragments=["hello"]),
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        instance_of(prov.MessageStreamError)),
                )))

    async def test_read_message_adds_error_part_on_non_assistant_role(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta.model_construct(
                    role="system", content="hello")),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta.model_construct(
                    role="assistant", content=" world")),])
        assert error is None
        assert_that(
            message_parts,
            has_item(
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        instance_of(prov.MessageStreamError)),
                )))

    async def test_read_message_streams_content_from_single_chunk(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", content="hello")),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(has_entries(type="content", fragments=["hello"])))

    async def test_read_message_streams_content_from_multiple_chunks(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant",
                                              content="hel")),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant", content="lo")),
        ])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(has_entries(type="content", fragments=["hello"])))

    async def test_read_message_streams_content_chunks_without_role_in_later_chunk(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant",
                                              content="hel")),
            self.make_chunk(delta=or_comp.ChatStreamDelta(content="lo")),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(has_entries(type="content", fragments=["hello"])))

    async def test_read_message_streams_content_when_assistant_role_appears_only_in_second_chunk(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(delta=or_comp.ChatStreamDelta(content="hel")),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant", content="lo")),
        ])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(has_entries(type="content", fragments=["hello"])))

    async def test_read_message_streams_reasoning_single_chunk(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", reasoning="step by step")),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(type="reasoning", fragments=["step by step"])))

    async def test_read_message_streams_reasoning_chunks(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", reasoning="step")),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(reasoning=" by step")),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(type="reasoning", fragments=["step by step"])))

    async def test_read_message_interleaves_reasoning_content_and_tool_call(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", reasoning="need data")),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", content="working...")),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            id="call-1",
                            function=or_comp.ChatStreamToolCallFunction(
                                name="lookup", arguments='{"q":"x"}'),
                        )],
                )),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(type="reasoning", fragments=["need data"]),
                has_entries(type="content", fragments=["working..."]),
                has_entries(
                    type="tool",
                    fragments=contains_exactly(
                        has_properties(
                            id="call-1",
                            function=has_properties(
                                name="lookup", arguments='{"q":"x"}'),
                        )),
                )))

    async def test_read_message_builds_single_complete_tool_call(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            id="call-1",
                            function=or_comp.ChatStreamToolCallFunction(
                                name="lookup", arguments='{"q":"x"}'),
                        )],
                )),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="tool",
                    fragments=contains_exactly(
                        has_properties(
                            id="call-1",
                            function=has_properties(
                                name="lookup", arguments='{"q":"x"}'),
                        )),
                )))

    async def test_read_message_builds_tool_call_from_separate_id_name_and_args_chunks(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(index=0, id="call-1")],
                )),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            function=or_comp.ChatStreamToolCallFunction(
                                name="lookup"),
                        )],
                )),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            function=or_comp.ChatStreamToolCallFunction(
                                arguments='{"q":"x"}'),
                        )],
                )),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="tool",
                    fragments=contains_exactly(
                        has_properties(
                            id="call-1",
                            function=has_properties(
                                name="lookup", arguments='{"q":"x"}'),
                        )),
                )))

    async def test_read_message_assembles_fragmented_tool_calls(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            id="call-",
                            function=or_comp.ChatStreamToolCallFunction(
                                name="look", arguments="{"),
                        )],
                )),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            id="up",
                            function=or_comp.ChatStreamToolCallFunction(
                                arguments='"q":"x"}'),
                        )],
                )),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="tool",
                    fragments=contains_exactly(
                        has_properties(
                            id="call-up",
                            function=has_properties(
                                name="look", arguments='{"q":"x"}'),
                        )),
                )))

    async def test_read_message_supports_multiple_tool_calls_by_index(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            id="call-1",
                            function=or_comp.ChatStreamToolCallFunction(
                                name="lookup"),
                        ),
                        or_comp.ChatStreamToolCall(
                            index=1,
                            id="call-2",
                            function=or_comp.ChatStreamToolCallFunction(
                                name="search"),
                        ),],
                )),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            function=or_comp.ChatStreamToolCallFunction(
                                arguments='{"q":"a"}'),
                        ),
                        or_comp.ChatStreamToolCall(
                            index=1,
                            function=or_comp.ChatStreamToolCallFunction(
                                arguments='{"q":"b"}'),
                        ),],
                )),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="tool",
                    fragments=contains_exactly(
                        has_properties(
                            id="call-1",
                            function=has_properties(
                                name="lookup", arguments='{"q":"a"}'),
                        ),
                        has_properties(
                            id="call-2",
                            function=has_properties(
                                name="search", arguments='{"q":"b"}'),
                        ),
                    ),
                )))

    async def test_read_message_tolerates_tool_call_delta_without_function(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            id="call-",
                            function=or_comp.ChatStreamToolCallFunction(
                                name="lookup"),
                        )],
                )),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[or_comp.ChatStreamToolCall(index=0, id="1")],
                )),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            function=or_comp.ChatStreamToolCallFunction(
                                arguments='{"q": 1}'),
                        )],
                )),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="tool",
                    fragments=contains_exactly(
                        has_properties(
                            id="call-1",
                            function=has_properties(
                                name="lookup", arguments='{"q": 1}'),
                        )),
                )))

    async def test_read_message_ignores_zero_choice_chunks_without_failing(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant", content="a")),
            self.make_chunk(choices=[]),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant", content="b")),
        ])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(has_entries(type="content", fragments=["ab"])))

    async def test_read_message_adds_error_part_when_chunk_has_multiple_choices(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                choices=[
                    or_comp.ChatStreamChoice(
                        index=0,
                        finish_reason=None,
                        delta=or_comp.ChatStreamDelta(
                            role="assistant", content="a"),
                    ),
                    or_comp.ChatStreamChoice(
                        index=1,
                        finish_reason=None,
                        delta=or_comp.ChatStreamDelta(
                            role="assistant", content="b"),
                    ),]),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        instance_of(prov.MessageStreamError)),
                )))

    async def test_read_message_adds_error_part_when_chunk_contains_error_payload(
            self):
        message_parts, error = await self.run_reader([
            or_comp.ChatStreamChunk(
                choices=[],
                created=0,
                id="chunk-id",
                model="test-model",
                object="chat.completion.chunk",
                error=or_comp.ChatStreamChunkError(
                    code=503, message="provider overloaded"),
            )])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        all_of(
                            instance_of(prov.OpenrouterChunkError),
                            has_properties(error_code=503))),
                )))

    async def test_read_message_adds_error_part_when_chunk_object_is_invalid(
            self):
        message_parts, error = await self.run_reader(["not a chunk"])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        instance_of(prov.MessageStreamError)),
                )))

    async def test_read_message_adds_error_part_when_stream_raises_exception(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant", content="a")),
            RuntimeError("stream failed"),])
        assert_that(error, instance_of(RuntimeError))
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(type="content", fragments=["a"]),
                has_entries(
                    type="error",
                    fragments=contains_exactly(instance_of(RuntimeError)),
                )))

    async def test_read_message_ignores_unknown_finish_reason(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant", content="a"),
                finish_reason="provider_specific_reason",
            ),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(has_entries(type="content", fragments=["a"])))

    async def test_read_message_handles_stop_finish_reason(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", content="done"),
                finish_reason="stop",
            ),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(has_entries(type="content", fragments=["done"])))

    async def test_read_message_handles_tool_calls_finish_reason_with_tool_call(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant",
                    tool_calls=[
                        or_comp.ChatStreamToolCall(
                            index=0,
                            id="call-1",
                            function=or_comp.ChatStreamToolCallFunction(
                                name="lookup", arguments='{"q":"x"}'),
                        )],
                ),
                finish_reason="tool_calls",
            ),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="tool",
                    fragments=contains_exactly(
                        has_properties(
                            id="call-1",
                            function=has_properties(
                                name="lookup", arguments='{"q":"x"}'),
                        )),
                )))

    async def test_read_message_reports_error_on_tool_calls_finish_reason_without_tool_call(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant"),
                finish_reason="tool_calls",
            ),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        all_of(
                            instance_of(prov.FinishReasonError),
                            has_properties(finish_reason="tool_calls"))),
                )))

    async def test_read_message_reports_error_on_length_finish_reason(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", content="partial"),
                finish_reason="length",
            ),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(type="content", fragments=["partial"]),
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        all_of(
                            instance_of(prov.FinishReasonError),
                            has_properties(finish_reason="length"))),
                )))

    async def test_read_message_reports_error_on_content_filter_finish_reason(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant"),
                finish_reason="content_filter",
            ),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        all_of(
                            instance_of(prov.FinishReasonError),
                            has_properties(finish_reason="content_filter"))),
                )))

    async def test_read_message_reports_error_on_error_finish_reason(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant"),
                finish_reason="error",
            ),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        all_of(
                            instance_of(prov.FinishReasonError),
                            has_properties(finish_reason="error"))),
                )))

    async def test_read_message_handles_content_and_reasoning_in_same_delta(
            self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", content="answer", reasoning="thinking")),
        ])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(type="reasoning", fragments=["thinking"]),
                has_entries(type="content", fragments=["answer"]),
            ))

    async def test_read_message_surfaces_refusal_only_delta(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", refusal="I cannot do that.")),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        instance_of(prov.AgentRefusalError)),
                )))

    async def test_read_message_surfaces_refusal_together_with_content(self):
        message_parts, error = await self.run_reader([
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(role="assistant", content="a")),
            self.make_chunk(
                delta=or_comp.ChatStreamDelta(
                    role="assistant", refusal="blocked")),])
        assert error is None
        assert_that(
            message_parts,
            contains_exactly(
                has_entries(type="content", fragments=["a"]),
                has_entries(
                    type="error",
                    fragments=contains_exactly(
                        instance_of(prov.AgentRefusalError)),
                )))
