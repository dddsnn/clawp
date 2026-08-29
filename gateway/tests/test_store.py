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
import dataclasses as dc
import json
import pathlib
import re
import shutil

import pydantic as pyd
import pytest
import whenever as we
from hamcrest import (
    all_of,
    assert_that,
    contains_exactly,
    has_properties,
    instance_of,
    is_,
)
from matchers import (  # pyright: ignore[reportImplicitRelativeImport]
    json_equivalent,
)

from clawp import message as msg
from clawp import model as mdl
from clawp import store


def create_file(path: pathlib.Path, lines: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.open("x").close()
    write_file_content(path, lines)


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


def session_file_header(session_seq, version=store.SessionsStore.VERSION):
    return {
        "version": version,
        "session_seq": session_seq,
    }


def message_file_for_base_dir(base_dir, session_seq):
    return base_dir / str(session_seq) / "messages.jsonl"


class MockMessageModel(pyd.BaseModel):
    payload: str


@dc.dataclass
class MockMessage:
    payload: str

    @staticmethod
    def from_model(model: MockMessageModel) -> MockMessage:
        assert model.payload.startswith("encoded ")
        # Create a task here to make sure there's an event loop running at the
        # point where we load models (we need this for the StreamableList).
        asyncio.create_task(asyncio.sleep(0))
        return MockMessage(payload=model.payload.removeprefix("encoded "))

    @property
    async def model(self) -> pyd.BaseModel:
        return MockMessageModel(payload=f"encoded {self.payload}")


@pytest.fixture(autouse=True)
def mock_message(monkeypatch):
    monkeypatch.setattr(msg, "Message", MockMessage)
    monkeypatch.setattr(mdl, "Message", MockMessageModel)


@pytest.fixture
def base_dir(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
async def make_sessions_store(base_dir):
    def factory():
        return store.SessionsStore(base_dir)

    return factory


@pytest.fixture
async def sessions_store(make_sessions_store):
    async with make_sessions_store() as s:
        yield s


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


class TestSessionsStore:
    @pytest.fixture
    def message_file(self, base_dir):
        def getter(session_seq):
            return message_file_for_base_dir(base_dir, session_seq)

        return getter

    async def test_append_message_creates_file_with_header(
        self, sessions_store, message_file
    ):
        await sessions_store.append_message(0, MockMessage(payload="a"))
        lines = read_file_content(message_file(0))
        assert len(lines) == 2
        assert json.loads(lines[0]) == session_file_header(0)

    async def test_append_message_raises_if_previous_dir_doesnt_exist(
        self, sessions_store
    ):
        with pytest.raises(store.StoreFormatError):
            await sessions_store.append_message(1, MockMessage(payload="a"))

    async def test_append_message(self, sessions_store, message_file):
        message = MockMessage(payload="a")
        await sessions_store.append_message(0, message)
        lines = read_file_content(message_file(0))
        assert len(lines) == 2
        assert (
            MockMessage.from_model(
                MockMessageModel.model_validate_json(lines[1])
            )
            == message
        )

    async def test_append_multiple_messages(self, sessions_store):
        message1 = MockMessage(payload="a")
        message2 = MockMessage(payload="b")
        await sessions_store.append_message(0, message1)
        await sessions_store.append_message(0, message2)
        messages = await sessions_store.read_session_messages(0)
        assert messages == [message1, message2]

    async def test_append_message_creates_session_dir(
        self, sessions_store, base_dir, message_file
    ):
        shutil.rmtree(base_dir)
        message = MockMessage(payload="a")
        await sessions_store.append_message(0, message)
        lines = read_file_content(message_file(0))
        assert len(lines) == 2
        assert (
            MockMessage.from_model(
                MockMessageModel.model_validate_json(lines[1])
            )
            == message
        )

    async def test_read_session_messages_empty_if_no_base_dir(
        self, sessions_store, base_dir
    ):
        shutil.rmtree(base_dir)
        messages = await sessions_store.read_session_messages(0)
        assert messages == []

    async def test_read_session_messages_empty_if_no_sessions_dir(
        self, sessions_store
    ):
        messages = await sessions_store.read_session_messages(0)
        assert messages == []

    async def test_read_session_messages_empty_session(
        self, sessions_store, message_file
    ):
        write_file_content(
            message_file(0), [json.dumps(session_file_header(0))]
        )
        messages = await sessions_store.read_session_messages(0)
        assert messages == []

    async def test_get_active_session_seq_0_if_no_base_dir(
        self, sessions_store, base_dir
    ):
        shutil.rmtree(base_dir)
        assert sessions_store.get_active_session_seq() == 0

    async def test_get_active_session_seq_0_if_no_sessions_dir(
        self, sessions_store
    ):
        assert sessions_store.get_active_session_seq() == 0

    async def test_get_active_session_seq(self, sessions_store, message_file):
        create_file(message_file(2))
        create_file(message_file(0))
        create_file(message_file(1))
        assert sessions_store.get_active_session_seq() == 2

    async def test_get_active_session_seq_returns_latest_even_if_some_missing(
        self, sessions_store, message_file
    ):
        create_file(message_file(3))
        create_file(message_file(0))
        create_file(message_file(1))
        assert sessions_store.get_active_session_seq() == 3

    async def test_get_active_session_seq_ignores_non_session_files(
        self, sessions_store, message_file, base_dir
    ):
        create_file(message_file(0))
        (base_dir / "1_then_not_a_number").mkdir()
        assert sessions_store.get_active_session_seq() == 0

    async def test_multiple_sessions_are_independent(self, sessions_store):
        message0 = MockMessage(payload="a")
        message1 = MockMessage(payload="b")
        await sessions_store.append_message(0, message0)
        await sessions_store.append_message(1, message1)
        assert await sessions_store.read_session_messages(0) == [message0]
        assert await sessions_store.read_session_messages(1) == [message1]

    async def test_aenter_after_aexit(self, make_sessions_store):
        async with make_sessions_store() as store:
            message = MockMessage(payload="a")
            await store.append_message(0, message)
        async with store:
            messages = await store.read_session_messages(0)
            assert messages == [message]

    async def test_aenter_in_new_instance(self, make_sessions_store):
        async with make_sessions_store() as store:
            message = MockMessage(payload="a")
            await store.append_message(0, message)
        async with make_sessions_store() as store:
            messages = await store.read_session_messages(0)
            assert messages == [message]

    async def test_only_one_instance_can_be_active_per_directory(
        self, tmp_path
    ):
        base_dir = tmp_path / "store"
        async with store.SessionsStore(base_dir):
            with pytest.raises(store.StoreConcurrentError):
                async with store.SessionsStore(base_dir):
                    pass

    async def test_allows_multiple_instances_for_other_directories(
        self, tmp_path
    ):
        base_dir1 = tmp_path / "store1"
        base_dir2 = tmp_path / "store2"
        async with (
            store.SessionsStore(base_dir1),
            store.SessionsStore(base_dir2),
        ):
            pass

    async def test_aenter_creates_base_dir(self, tmp_path):
        base_dir = tmp_path / "store"
        assert not base_dir.exists()
        async with store.SessionsStore(base_dir):
            assert base_dir.exists()

    async def test_aenter_accepts_valid_existing_base_dir(
        self, make_sessions_store, message_file
    ):
        create_file(message_file(0), [json.dumps(session_file_header(0))])
        create_file(message_file(1), [json.dumps(session_file_header(1))])
        async with make_sessions_store():
            pass

    async def test_aenter_raises_if_session_seq_doesnt_start_at_0(
        self, make_sessions_store, message_file
    ):
        create_file(message_file(1), [json.dumps(session_file_header(1))])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_sessions_have_missing_seqs(
        self, make_sessions_store, message_file
    ):
        create_file(message_file(0), [json.dumps(session_file_header(0))])
        create_file(message_file(2), [json.dumps(session_file_header(2))])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_session_has_invalid_header_json(
        self, make_sessions_store, message_file
    ):
        create_file(message_file(0), ["not json"])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    @pytest.mark.parametrize(
        "key,value", [("version", "not an int"), ("session_seq", "not an int")]
    )
    async def test_aenter_raises_if_session_has_invalid_header(
        self, make_sessions_store, message_file, key, value
    ):
        header = session_file_header(0)
        header[key] = value
        create_file(message_file(0), [json.dumps(header)])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_session_has_inconsistent_header(
        self, make_sessions_store, message_file
    ):
        header = session_file_header(0)
        header["session_seq"] = 1
        create_file(message_file(0), [json.dumps(header)])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_upgrades_older_version(
        self, make_sessions_store, message_file, monkeypatch
    ):
        def upgrade(path):
            assert path.is_file()
            write_file_content(path, ["upgraded"])

        monkeypatch.setattr(store.SessionsStore, "VERSION", 1)
        monkeypatch.setattr(store.SessionsStore, "_upgraders", {0: upgrade})
        create_file(
            message_file(0), [json.dumps(session_file_header(0, version=0))]
        )
        create_file(
            message_file(1), [json.dumps(session_file_header(1, version=0))]
        )
        async with make_sessions_store():
            assert read_file_content(message_file(0)) == ["upgraded"]
            assert read_file_content(message_file(1)) == ["upgraded"]

    async def test_aenter_upgrades_multiple_version_steps(
        self, make_sessions_store, message_file, monkeypatch
    ):
        def upgrade_0(path):
            assert path.is_file()
            write_file_content(path, ["upgraded 0"])

        def upgrade_1(path):
            assert read_file_content(path) == ["upgraded 0"]
            assert path.is_file()
            write_file_content(path, ["upgraded 1"])

        monkeypatch.setattr(store.SessionsStore, "VERSION", 2)
        monkeypatch.setattr(
            store.SessionsStore, "_upgraders", {0: upgrade_0, 1: upgrade_1}
        )
        create_file(
            message_file(0), [json.dumps(session_file_header(0, version=0))]
        )
        async with make_sessions_store():
            assert read_file_content(message_file(0)) == ["upgraded 1"]

    async def test_aenter_backs_up_before_upgrade(
        self, make_sessions_store, message_file, monkeypatch, base_dir
    ):
        def upgrade(path):
            assert path.is_file()
            write_file_content(path, ["upgraded"])

        monkeypatch.setattr(store.SessionsStore, "VERSION", 1)
        monkeypatch.setattr(store.SessionsStore, "_upgraders", {0: upgrade})
        lines_before_upgrade = [
            json.dumps(session_file_header(0, version=0)),
            json.dumps({"payload": "a"}),
        ]
        create_file(message_file(0), lines_before_upgrade)
        async with make_sessions_store():
            backup_dirs = list(base_dir.parent.glob("backup*"))
            assert len(backup_dirs) == 1
            backup_dir_match = re.match(
                f"backup_{base_dir.name}_version_(?P<version>[0-9]+)"
                "_(?P<timestamp>.*)",
                backup_dirs[0].name,
            )
            assert backup_dir_match
            assert backup_dir_match.group("version") == "0"
            # Make sure the timestamp parses.
            we.Instant(backup_dir_match.group("timestamp"))
        backup_file = message_file_for_base_dir(backup_dirs[0], 0)
        assert read_file_content(backup_file) == lines_before_upgrade

    async def test_aenter_raises_if_multiple_versions_in_session_files(
        self, make_sessions_store, message_file, monkeypatch
    ):
        def upgrade(_path):
            pass

        monkeypatch.setattr(store.SessionsStore, "VERSION", 1)
        monkeypatch.setattr(store.SessionsStore, "_upgraders", {0: upgrade})
        create_file(
            message_file(0), [json.dumps(session_file_header(0, version=0))]
        )
        create_file(
            message_file(1), [json.dumps(session_file_header(1, version=1))]
        )
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_future_version_in_session_files(
        self, make_sessions_store, message_file
    ):
        create_file(
            message_file(0),
            [
                json.dumps(
                    session_file_header(
                        0, version=store.SessionsStore.VERSION + 1
                    )
                )
            ],
        )
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_deletes_truncated_last_line(
        self, make_sessions_store, message_file
    ):
        async with make_sessions_store() as store:
            message = MockMessage(payload="a")
            await store.append_message(0, message)
        # Simulate a crash by appending a partial line.
        with message_file(0).open("a") as f:
            f.write('{"payload": "some s')
        async with store:
            assert_that(
                read_file_content(message_file(0)),
                contains_exactly(
                    json_equivalent(session_file_header(0)),
                    json_equivalent({"payload": "encoded a"}),
                ),
            )

    async def test_aenter_raises_on_corrupt_non_last_line(
        self, make_sessions_store, message_file
    ):
        async with make_sessions_store() as sessions_store:
            message = MockMessage(payload="a")
            await sessions_store.append_message(0, message)
        # Write a corrupt line followed by a valid line.
        with message_file(0).open("a") as f:
            f.write("not json\n")
            f.write('{"payload":"a"}\n')
        with pytest.raises(store.StoreFormatError):
            async with sessions_store:
                pass

    async def test_aenter_raises_on_empty_non_last_line(
        self, make_sessions_store, message_file
    ):
        async with make_sessions_store() as sessions_store:
            message = MockMessage(payload="a")
            await sessions_store.append_message(0, message)
        with message_file(0).open("a") as f:
            f.write("\n")
            f.write('{"payload":"a"}\n')
        with pytest.raises(store.StoreFormatError):
            async with sessions_store:
                pass

    async def test_append_after_reopen(self, make_sessions_store):
        async with make_sessions_store() as store:
            message1 = MockMessage(payload="a")
            await store.append_message(0, message1)
        async with store:
            message2 = MockMessage(payload="b")
            await store.append_message(0, message2)
            messages = await store.read_session_messages(0)
            assert messages == [message1, message2]

    async def test_message_with_unicode_and_newlines(self, sessions_store):
        message = MockMessage(payload="hello\nworld\n\ttab\u00e9\U0001f600")
        await sessions_store.append_message(0, message)
        messages = await sessions_store.read_session_messages(0)
        assert messages == [message]

    async def test_read_after_append_on_same_instance(self, sessions_store):
        message = MockMessage(payload="a")
        await sessions_store.append_message(0, message)
        # Read from the same store instance (which has the file open for
        # appending). The read uses a separate file handle.
        messages = await sessions_store.read_session_messages(0)
        assert messages == [message]

    async def test_for_session(self, sessions_store):
        message1 = MockMessage(payload="a")
        message2 = MockMessage(payload="b")
        await sessions_store.append_message(0, message1)
        session_store = sessions_store.for_session(0)
        assert await session_store.read_session_messages() == [message1]
        await session_store.append_message(message2)
        assert await sessions_store.read_session_messages(0) == [
            message1,
            message2,
        ]


def memory(**kwargs):
    return all_of(instance_of(mdl.Memory), has_properties(**kwargs))


class TestJsonlMemoryStore:
    @pytest.fixture
    def base_dir(self, tmp_path):
        return tmp_path / "memory"

    @pytest.fixture
    def memory_store(self, base_dir):
        return store.JsonlMemoryStore(base_dir)

    async def test_log_memory_creates_file(self, memory_store, base_dir):
        await memory_store.log_memory("test event")
        lines = read_file_content(base_dir / "memory.jsonl")
        assert len(lines) == 2
        data = json.loads(lines[1])
        assert data["content"] == "test event"
        assert "id" in data
        assert "time" in data

    async def test_search_memory_no_filters(self, memory_store):
        await memory_store.log_memory("event 1")
        await memory_store.log_memory("event 2")
        results = [m async for m in memory_store.search_memory()]
        assert_that(
            results,
            contains_exactly(
                memory(content="event 1"), memory(content="event 2")
            ),
        )

    async def test_search_memory_by_start_time(self, memory_store):
        t1 = we.Instant.from_utc(2026, 1, 1, 12, 0, 0)
        t2 = we.Instant.from_utc(2026, 1, 2, 12, 0, 0)
        t3 = we.Instant.from_utc(2026, 1, 3, 12, 0, 0)

        with we.patch_current_time(t1, keep_ticking=False):
            await memory_store.log_memory("event 1")
        with we.patch_current_time(t2, keep_ticking=False):
            await memory_store.log_memory("event 2")
        with we.patch_current_time(t3, keep_ticking=False):
            await memory_store.log_memory("event 3")

        results = [m async for m in memory_store.search_memory(start_time=t2)]
        assert_that(
            results,
            contains_exactly(
                memory(content="event 2"), memory(content="event 3")
            ),
        )

    async def test_search_memory_by_end_time(self, memory_store):
        t1 = we.Instant.from_utc(2026, 1, 1, 12, 0, 0)
        t2 = we.Instant.from_utc(2026, 1, 2, 12, 0, 0)
        t3 = we.Instant.from_utc(2026, 1, 3, 12, 0, 0)

        with we.patch_current_time(t1, keep_ticking=False):
            await memory_store.log_memory("event 1")
        with we.patch_current_time(t2, keep_ticking=False):
            await memory_store.log_memory("event 2")
        with we.patch_current_time(t3, keep_ticking=False):
            await memory_store.log_memory("event 3")

        results = [m async for m in memory_store.search_memory(end_time=t2)]
        assert_that(
            results,
            contains_exactly(
                memory(content="event 1"), memory(content="event 2")
            ),
        )

    async def test_search_memory_by_start_and_end_time(self, memory_store):
        t1 = we.Instant.from_utc(2026, 1, 1, 12, 0, 0)
        t2 = we.Instant.from_utc(2026, 1, 2, 12, 0, 0)
        t3 = we.Instant.from_utc(2026, 1, 3, 12, 0, 0)

        with we.patch_current_time(t1, keep_ticking=False):
            await memory_store.log_memory("event 1")
        with we.patch_current_time(t2, keep_ticking=False):
            await memory_store.log_memory("event 2")
        with we.patch_current_time(t3, keep_ticking=False):
            await memory_store.log_memory("event 3")

        results = [
            m
            async for m in memory_store.search_memory(
                start_time=t2, end_time=t2
            )
        ]
        assert_that(results, contains_exactly(memory(content="event 2")))

    async def test_search_memory_by_search_term(self, memory_store):
        await memory_store.log_memory("hello world")
        await memory_store.log_memory("testing memories")
        await memory_store.log_memory("goodbye")

        results = [
            m async for m in memory_store.search_memory(search_term="hello")
        ]
        assert_that(results, contains_exactly(memory(content="hello world")))

    async def test_search_memory_by_search_term_is_case_insensitive(
        self, memory_store
    ):
        await memory_store.log_memory("Hello World")
        await memory_store.log_memory("testing memories")
        await memory_store.log_memory("hello again")

        results = [
            m async for m in memory_store.search_memory(search_term="HeLlO")
        ]
        assert_that(
            results,
            contains_exactly(
                memory(content="Hello World"), memory(content="hello again")
            ),
        )

    async def test_search_memory_raises_format_error_on_corrupt_line(
        self, memory_store, base_dir
    ):
        await memory_store.log_memory("valid event")
        # Manually append an invalid line.
        with (base_dir / "memory.jsonl").open("a") as f:
            f.write("this is not json\n")
        with pytest.raises(store.StoreFormatError):
            [m async for m in memory_store.search_memory()]

    async def test_search_memory_raises_format_error_on_empty_line(
        self, memory_store, base_dir
    ):
        await memory_store.log_memory("valid event")
        # Manually append an empty line.
        with (base_dir / "memory.jsonl").open("a") as f:
            f.write("\n")
        with pytest.raises(store.StoreFormatError):
            [m async for m in memory_store.search_memory()]
