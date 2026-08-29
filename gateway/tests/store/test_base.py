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

import json
import pathlib

import pydantic as pyd
import pytest
from hamcrest import (
    assert_that,
    contains_exactly,
    has_properties,
    is_,
)

from clawp import store
from tests.matchers import (
    json_equivalent,
)


def write_file_content(
    path: pathlib.Path, lines: list[str] | None = None
) -> None:
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)
    if not path.is_file():
        path.open("x").close()
    with path.open("w") as f:
        f.writelines(line + "\n" for line in (lines or []))


def read_file_content(path: pathlib.Path) -> list[str]:
    with path.open("r") as f:
        return [line.rstrip("\n") for line in f.readlines()]


class MockMessageModel(pyd.BaseModel):
    payload: str


class TestJsonlIO:
    @pytest.fixture
    def jsonl_path(self, tmp_path):
        return tmp_path / "test.jsonl"

    @pytest.fixture
    async def jsonl_io(self, jsonl_path):
        async with store.JsonlIO(jsonl_path, MockMessageModel) as jsonl_io:
            yield jsonl_io

    async def test_create_and_exists(self, jsonl_io, jsonl_path):
        assert not await jsonl_io.exists()
        header = {"version": 0, "type": "test"}
        await jsonl_io.create(header)
        assert await jsonl_io.exists()
        assert await jsonl_io.header == header
        assert_that(
            read_file_content(jsonl_path),
            contains_exactly(json_equivalent(header)),
        )

    async def test_create_raises_on_header_without_version(self, jsonl_io):
        with pytest.raises(ValueError):
            await jsonl_io.create({"not_version": 0})

    async def test_create_raises_on_header_with_non_int_version(
        self, jsonl_io
    ):
        with pytest.raises(ValueError):
            await jsonl_io.create({"version": "not an int"})

    async def test_create_raises_on_existing_file(self, jsonl_io, jsonl_path):
        write_file_content(jsonl_path, ["a"])
        with pytest.raises(FileExistsError):
            await jsonl_io.create({"version": 0})

    async def test_header_raises_on_nonexistent_file(self, jsonl_io):
        with pytest.raises(FileNotFoundError):
            await jsonl_io.header

    async def test_header_raises_on_invalid_header(self, jsonl_io, jsonl_path):
        write_file_content(jsonl_path, [json.dumps({"missing_version": 0})])
        with pytest.raises(store.StoreFormatError):
            await jsonl_io.header

    async def test_create_creates_directory_if_not_exists(self, tmp_path):
        jsonl_path = tmp_path / "doesnt_exist_yet" / "test.jsonl"
        async with store.JsonlIO(jsonl_path, MockMessageModel) as jsonl_io:
            assert not await jsonl_io.exists()
            await jsonl_io.create({"version": 0})
            assert await jsonl_io.exists()

    async def test_append_and_read_all(self, jsonl_io):
        await jsonl_io.create({"version": 0})

        model1 = MockMessageModel(payload="a")
        model2 = MockMessageModel(payload="b")

        await jsonl_io.append(model1)
        await jsonl_io.append(model2)

        assert_that(
            [m async for m in jsonl_io.read_all()], is_([model1, model2])
        )

    async def test_append_raises_if_file_doesnt_exist(self, jsonl_io):
        with pytest.raises(FileNotFoundError):
            await jsonl_io.append(MockMessageModel(payload="a"))

    async def test_read_all_raises_on_nonexistent_file(self, jsonl_io):
        with pytest.raises(FileNotFoundError):
            [m async for m in jsonl_io.read_all()]

    async def test_read_all_works_with_type_adapter(self, jsonl_path):
        type_adapter = pyd.TypeAdapter(MockMessageModel)
        async with store.JsonlIO(jsonl_path, type_adapter) as jsonl_io:
            await jsonl_io.create({"version": 0})
            await jsonl_io.append(MockMessageModel(payload="a"))
            assert_that(
                [m async for m in jsonl_io.read_all()],
                contains_exactly(has_properties(payload="a")),
            )

    async def test_close(self, jsonl_io):
        await jsonl_io.create({"version": 0})
        await jsonl_io.append(MockMessageModel(payload="a"))
        assert not jsonl_io._write_file.closed
        await jsonl_io.close()
        assert jsonl_io._write_file is None

    async def test_aexit(self, jsonl_io):
        async with jsonl_io:
            await jsonl_io.create({"version": 0})
            await jsonl_io.append(MockMessageModel(payload="a"))
            assert not jsonl_io._write_file.closed
        assert jsonl_io._write_file is None

    async def test_append_after_close(self, jsonl_io):
        await jsonl_io.create({"version": 0})

        model1 = MockMessageModel(payload="a")
        model2 = MockMessageModel(payload="b")

        await jsonl_io.append(model1)
        await jsonl_io.close()
        await jsonl_io.append(model2)

        assert_that(
            [m async for m in jsonl_io.read_all()], is_([model1, model2])
        )

    async def test_upgrade_and_validate_does_nothing_on_current_version(
        self, jsonl_io, jsonl_path
    ):
        await jsonl_io.create({"version": 1})
        await jsonl_io.append(MockMessageModel(payload="a"))
        await jsonl_io.close()

        def upgrade_0_to_1(_path):
            assert False

        await jsonl_io.upgrade_and_validate({0: upgrade_0_to_1})
        assert_that(
            read_file_content(jsonl_path),
            contains_exactly(
                json_equivalent({"version": 1}),
                json_equivalent({"payload": "a"}),
            ),
        )

    async def test_upgrade_and_validate_does_nothing_without_upgraders(
        self, jsonl_io, jsonl_path
    ):
        await jsonl_io.create({"version": 0})
        await jsonl_io.append(MockMessageModel(payload="a"))
        await jsonl_io.close()
        await jsonl_io.upgrade_and_validate({})
        assert_that(
            read_file_content(jsonl_path),
            contains_exactly(
                json_equivalent({"version": 0}),
                json_equivalent({"payload": "a"}),
            ),
        )

    async def test_upgrade_and_validate_applies_one_upgrade(
        self, jsonl_io, jsonl_path
    ):
        await jsonl_io.create({"version": 0})
        await jsonl_io.append(MockMessageModel(payload="a"))
        await jsonl_io.close()

        def upgrade_0_to_1(path):
            assert_that(
                read_file_content(path),
                contains_exactly(
                    json_equivalent({"version": 0}),
                    json_equivalent({"payload": "a"}),
                ),
            )
            write_file_content(
                path,
                [json.dumps({"version": 1}), json.dumps({"payload": "b"})],
            )

        await jsonl_io.upgrade_and_validate({0: upgrade_0_to_1})
        assert_that(
            read_file_content(jsonl_path),
            contains_exactly(
                json_equivalent({"version": 1}),
                json_equivalent({"payload": "b"}),
            ),
        )
        assert await jsonl_io.header == {"version": 1}

    async def test_upgrade_and_validate_applies_multiple_upgrades(
        self, jsonl_io, jsonl_path
    ):
        await jsonl_io.create({"version": 0})
        await jsonl_io.append(MockMessageModel(payload="a"))
        await jsonl_io.close()

        def upgrade_0_to_1(path):
            assert_that(
                read_file_content(path),
                contains_exactly(
                    json_equivalent({"version": 0}),
                    json_equivalent({"payload": "a"}),
                ),
            )
            write_file_content(
                path,
                [json.dumps({"version": 1}), json.dumps({"payload": "b"})],
            )

        def upgrade_1_to_2(path):
            assert_that(
                read_file_content(jsonl_path),
                contains_exactly(
                    json_equivalent({"version": 1}),
                    json_equivalent({"payload": "b"}),
                ),
            )
            write_file_content(
                path,
                [json.dumps({"version": 2}), json.dumps({"payload": "v"})],
            )

        await jsonl_io.upgrade_and_validate(
            {0: upgrade_0_to_1, 1: upgrade_1_to_2}
        )
        assert_that(
            read_file_content(jsonl_path),
            contains_exactly(
                json_equivalent({"version": 2}),
                json_equivalent({"payload": "v"}),
            ),
        )
        assert await jsonl_io.header == {"version": 2}

    async def test_upgrade_and_validate_starts_upgrade_at_right_version(
        self, jsonl_io, jsonl_path
    ):
        await jsonl_io.create({"version": 1})
        await jsonl_io.append(MockMessageModel(payload="a"))
        await jsonl_io.close()

        def upgrade_0_to_1(_path):
            assert False

        def upgrade_1_to_2(path):
            assert_that(
                read_file_content(jsonl_path),
                contains_exactly(
                    json_equivalent({"version": 1}),
                    json_equivalent({"payload": "a"}),
                ),
            )
            write_file_content(
                path,
                [json.dumps({"version": 2}), json.dumps({"payload": "b"})],
            )

        await jsonl_io.upgrade_and_validate(
            {0: upgrade_0_to_1, 1: upgrade_1_to_2}
        )
        assert_that(
            read_file_content(jsonl_path),
            contains_exactly(
                json_equivalent({"version": 2}),
                json_equivalent({"payload": "b"}),
            ),
        )

    async def test_upgrade_and_validate_raises_on_future_version(
        self, jsonl_io
    ):
        await jsonl_io.create({"version": 2})
        await jsonl_io.append(MockMessageModel(payload="a"))
        await jsonl_io.close()

        def upgrade_0_to_1(_path):
            assert False

        with pytest.raises(store.StoreFormatError):
            await jsonl_io.upgrade_and_validate({0: upgrade_0_to_1})

    async def test_upgrade_and_validate_applies_upgrade_before_validation(
        self, jsonl_io, jsonl_path
    ):
        write_file_content(
            jsonl_path,
            [
                json.dumps({"version": 0}),
                json.dumps({"old_name_for_payload": "a"}),
            ],
        )

        def upgrade_0_to_1(path):
            lines = [json.dumps({"version": 1})]
            for line in read_file_content(jsonl_path)[1:]:
                payload = json.loads(line)["old_name_for_payload"]
                lines.append(json.dumps({"payload": payload}))
            write_file_content(path, lines)

        await jsonl_io.upgrade_and_validate({0: upgrade_0_to_1})
        assert_that(
            [m async for m in jsonl_io.read_all()],
            contains_exactly(has_properties(payload="a")),
        )

    async def test_upgrade_and_validate_raises_on_missing_header(
        self, jsonl_io, jsonl_path
    ):
        write_file_content(jsonl_path, [json.dumps({"payload": "a"})])
        with pytest.raises(store.StoreFormatError):
            await jsonl_io.upgrade_and_validate({})

    async def test_upgrade_and_validate_raises_on_invalid_header(
        self, jsonl_io, jsonl_path
    ):
        write_file_content(
            jsonl_path,
            [
                json.dumps({"version": "not an int"}),
                json.dumps({"payload": "a"}),
            ],
        )
        with pytest.raises(store.StoreFormatError):
            await jsonl_io.upgrade_and_validate({})

    async def test_upgrade_and_validate_raises_on_invalid_model(
        self, jsonl_io, jsonl_path
    ):
        write_file_content(
            jsonl_path,
            [
                json.dumps({"version": 0}),
                json.dumps({"not_payload": "a"}),
                json.dumps({"payload": "a"}),
            ],
        )
        with pytest.raises(store.StoreFormatError):
            await jsonl_io.upgrade_and_validate({})

    async def test_upgrade_and_validate_raises_on_empty_line(
        self, jsonl_io, jsonl_path
    ):
        write_file_content(
            jsonl_path,
            [json.dumps({"version": 0}), "", json.dumps({"payload": "a"})],
        )
        with pytest.raises(store.StoreFormatError):
            await jsonl_io.upgrade_and_validate({})

    async def test_upgrade_and_validate_raises_on_invalid_model_last_line(
        self, jsonl_io, jsonl_path
    ):
        write_file_content(
            jsonl_path,
            [json.dumps({"version": 0}), json.dumps({"not_payload": "a"})],
        )
        with pytest.raises(store.StoreFormatError):
            await jsonl_io.upgrade_and_validate({})

    async def test_upgrade_and_validate_raises_on_empty_line_last_line(
        self, jsonl_io, jsonl_path
    ):
        write_file_content(jsonl_path, [json.dumps({"version": 0}), ""])
        with pytest.raises(store.StoreFormatError):
            await jsonl_io.upgrade_and_validate({})

    async def test_upgrade_and_validate_discards_corrupt_unterminated_line(
        self, jsonl_io, jsonl_path
    ):
        write_file_content(
            jsonl_path,
            [json.dumps({"version": 0}), json.dumps({"payload": "a"})],
        )
        with jsonl_path.open("a") as f:
            # Use raw write() so we don't write a newline and terminate the
            # line.
            f.write("not { valid json")
        await jsonl_io.upgrade_and_validate({})
        assert_that(
            [m async for m in jsonl_io.read_all()],
            contains_exactly(has_properties(payload="a")),
        )

    async def test_upgrade_and_validate_deletes_corrupt_unterminated_line(
        self, jsonl_io, jsonl_path
    ):
        write_file_content(
            jsonl_path,
            [json.dumps({"version": 0}), json.dumps({"payload": "a"})],
        )
        with jsonl_path.open("a") as f:
            # Use raw write() so we don't write a newline and terminate the
            # line.
            f.write("not { valid json")
        await jsonl_io.upgrade_and_validate({})
        assert_that(
            read_file_content(jsonl_path),
            contains_exactly(
                json_equivalent({"version": 0}),
                json_equivalent({"payload": "a"}),
            ),
        )

    async def test_upgrade_and_validate_works_with_type_adapter(
        self, jsonl_path
    ):
        write_file_content(
            jsonl_path,
            [json.dumps({"version": 0}), json.dumps({"payload": "a"})],
        )
        type_adapter = pyd.TypeAdapter(MockMessageModel)
        async with store.JsonlIO(jsonl_path, type_adapter) as jsonl_io:
            await jsonl_io.upgrade_and_validate({})
