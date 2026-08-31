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
from hamcrest import assert_that, contains_exactly, has_properties

from clawp import message as msg
from clawp import model as mdl
from clawp import store
from tests.matchers import (
    json_equivalent,
)

from .test_base import MockMessageModel, read_file_content, write_file_content


def create_file(path: pathlib.Path, lines: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.open("x").close()
    write_file_content(path, lines)


def session_file_header(session_seq, version=store.SessionsStore.VERSION):
    return {
        "version": version,
        "session_seq": session_seq,
    }


def message_file_for_base_dir(base_dir, session_seq):
    return base_dir / str(session_seq) / "messages.jsonl"


def state_file_for_base_dir(base_dir, session_seq):
    return base_dir / str(session_seq) / "state.json"


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


class MockState(pyd.BaseModel):
    payload: str = "init"


@pytest.fixture(autouse=True)
def mock_state(monkeypatch):
    monkeypatch.setattr(mdl, "SessionState", MockState)


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


class TestSessionsStore:
    @pytest.fixture
    def message_file(self, base_dir):
        def getter(session_seq):
            return message_file_for_base_dir(base_dir, session_seq)

        return getter

    @pytest.fixture
    def state_file(self, base_dir):
        def getter(session_seq):
            return state_file_for_base_dir(base_dir, session_seq)

        return getter

    async def test_append_message_creates_file_with_header(
        self, sessions_store, message_file
    ):
        assert not message_file(0).parent.exists()
        assert not message_file(0).exists()
        await sessions_store.append_message(0, MockMessage(payload="a"))
        lines = read_file_content(message_file(0))
        assert len(lines) == 2
        assert json.loads(lines[0]) == session_file_header(0)

    async def test_append_message_creates_state_file(
        self, sessions_store, state_file
    ):
        assert not state_file(0).exists()
        await sessions_store.append_message(0, MockMessage(payload="a"))
        state = MockState.model_validate_json(state_file(0).read_text())
        assert state.payload == "init"

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
        _, messages = await sessions_store.load_or_create(0)
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

    async def test_load_or_create_loads_existing_session(
        self, sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [MockState(payload="s1").model_dump_json()])
        create_file(
            message_file(0),
            [
                json.dumps(session_file_header(0)),
                (await MockMessage(payload="m1").model).model_dump_json(),
            ],
        )
        state, messages = await sessions_store.load_or_create(0)
        assert state.payload == "s1"
        assert_that(messages, contains_exactly(has_properties(payload="m1")))

    async def test_load_or_create_raises_on_missing_state(
        self, sessions_store, message_file
    ):
        create_file(message_file(0), [json.dumps(session_file_header(0))])
        with pytest.raises(store.StoreFormatError):
            await sessions_store.load_or_create(0)

    async def test_load_or_create_raises_on_invalid_state(
        self, sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [json.dumps({"payload": True})])
        create_file(message_file(0), [json.dumps(session_file_header(0))])
        with pytest.raises(store.StoreFormatError):
            await sessions_store.load_or_create(0)

    async def test_load_or_create_raises_on_invalid_message(
        self, sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [MockState().model_dump_json()])
        create_file(
            message_file(0),
            [json.dumps(session_file_header(0)), "not a valid message"],
        )
        with pytest.raises(store.StoreFormatError):
            await sessions_store.load_or_create(0)

    async def test_load_or_create_creates_state_file(
        self, sessions_store, state_file
    ):
        assert not state_file(0).parent.exists()
        assert not state_file(0).exists()
        loaded_state, _ = await sessions_store.load_or_create(0)
        content = state_file(0).read_text()
        state = MockState.model_validate_json(content)
        assert state == loaded_state

    async def test_load_or_create_raises_if_previous_dir_doesnt_exist(
        self, sessions_store
    ):
        with pytest.raises(store.StoreFormatError):
            await sessions_store.load_or_create(1)

    async def test_load_or_create_empty_and_creates_session_dir(
        self, sessions_store, state_file
    ):
        assert not state_file(0).parent.exists()
        state, messages = await sessions_store.load_or_create(0)
        assert state_file(0).parent.exists()
        assert state == MockState()
        assert messages == []

    async def test_load_or_create_empty_and_creates_base_dir(
        self, sessions_store, base_dir
    ):
        shutil.rmtree(base_dir)
        state, messages = await sessions_store.load_or_create(0)
        assert base_dir.is_dir()
        assert state == MockState()
        assert messages == []

    async def test_load_or_create_empty_session(
        self, sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [MockState().model_dump_json()])
        create_file(message_file(0), [json.dumps(session_file_header(0))])
        state, messages = await sessions_store.load_or_create(0)
        assert state == MockState()
        assert messages == []

    async def test_load_or_create_returns_existing_state_instance(
        self, sessions_store
    ):
        state1, _ = await sessions_store.load_or_create(0)
        state2, _ = await sessions_store.load_or_create(0)
        assert state1 is state2

    async def test_load_or_create_creates_missing_state_file(
        self, sessions_store, state_file
    ):
        await sessions_store.load_or_create(0)
        state_file(0).unlink()
        state, _ = await sessions_store.load_or_create(0)
        assert state == MockState()

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
        state_0, messages_0 = await sessions_store.load_or_create(0)
        assert messages_0 == [message0]
        state_1, messages_1 = await sessions_store.load_or_create(1)
        assert messages_1 == [message1]
        assert state_0 is not state_1

    async def test_aenter_after_aexit(self, make_sessions_store):
        async with make_sessions_store() as store:
            state, messages = await store.load_or_create(0)
            message = MockMessage(payload="a")
            state.payload = "updated"
            await store.append_message(0, message)
        async with store:
            state, messages = await store.load_or_create(0)
            assert state.payload == "updated"
            assert messages == [message]

    async def test_aenter_in_new_instance(self, make_sessions_store):
        async with make_sessions_store() as store:
            state, messages = await store.load_or_create(0)
            message = MockMessage(payload="a")
            state.payload = "updated"
            await store.append_message(0, message)
        async with make_sessions_store() as store:
            state, messages = await store.load_or_create(0)
            assert state.payload == "updated"
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
        self, make_sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [MockState().model_dump_json()])
        create_file(message_file(0), [json.dumps(session_file_header(0))])
        create_file(state_file(1), [MockState().model_dump_json()])
        create_file(message_file(1), [json.dumps(session_file_header(1))])
        async with make_sessions_store():
            pass

    async def test_aenter_raises_if_session_seq_doesnt_start_at_0(
        self, make_sessions_store, state_file, message_file
    ):
        create_file(state_file(1), [MockState().model_dump_json()])
        create_file(message_file(1), [json.dumps(session_file_header(1))])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_sessions_have_missing_seqs(
        self, make_sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [MockState().model_dump_json()])
        create_file(message_file(0), [json.dumps(session_file_header(0))])
        create_file(state_file(2), [MockState().model_dump_json()])
        create_file(message_file(2), [json.dumps(session_file_header(2))])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_session_has_missing_state(
        self, make_sessions_store, message_file
    ):
        create_file(message_file(0), [json.dumps(session_file_header(0))])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_session_has_missing_message_file(
        self, make_sessions_store, state_file
    ):
        create_file(state_file(0), [MockState().model_dump_json()])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_session_has_invalid_state(
        self, make_sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [json.dumps({"payload": False})])
        create_file(message_file(0), [json.dumps(session_file_header(0))])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_message_file_has_invalid_header_json(
        self, make_sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [MockState().model_dump_json()])
        create_file(message_file(0), ["not json"])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    @pytest.mark.parametrize(
        "key,value", [("version", "not an int"), ("session_seq", "not an int")]
    )
    async def test_aenter_raises_if_message_file_has_invalid_header(
        self, make_sessions_store, state_file, message_file, key, value
    ):
        create_file(state_file(0), [MockState().model_dump_json()])
        header = session_file_header(0)
        header[key] = value
        create_file(message_file(0), [json.dumps(header)])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_message_file_has_inconsistent_header(
        self, make_sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [MockState().model_dump_json()])
        header = session_file_header(0)
        header["session_seq"] = 1
        create_file(message_file(0), [json.dumps(header)])
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_upgrades_older_version(
        self, make_sessions_store, state_file, message_file, monkeypatch
    ):
        def upgrade(path):
            assert path.is_file()
            write_file_content(path, ["upgraded"])

        monkeypatch.setattr(store.SessionsStore, "VERSION", 1)
        monkeypatch.setattr(store.SessionsStore, "_upgraders", {0: upgrade})
        create_file(state_file(0), [MockState().model_dump_json()])
        create_file(
            message_file(0), [json.dumps(session_file_header(0, version=0))]
        )
        create_file(state_file(1), [MockState().model_dump_json()])
        create_file(
            message_file(1), [json.dumps(session_file_header(1, version=0))]
        )
        async with make_sessions_store():
            assert read_file_content(message_file(0)) == ["upgraded"]
            assert read_file_content(message_file(1)) == ["upgraded"]

    async def test_aenter_upgrades_multiple_version_steps(
        self, make_sessions_store, state_file, message_file, monkeypatch
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
        create_file(state_file(0), [MockState().model_dump_json()])
        create_file(
            message_file(0), [json.dumps(session_file_header(0, version=0))]
        )
        async with make_sessions_store():
            assert read_file_content(message_file(0)) == ["upgraded 1"]

    async def test_aenter_backs_up_before_upgrade(
        self,
        make_sessions_store,
        state_file,
        message_file,
        monkeypatch,
        base_dir,
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
        create_file(state_file(0), [MockState().model_dump_json()])
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
        self, make_sessions_store, state_file, message_file, monkeypatch
    ):
        def upgrade(_path):
            pass

        monkeypatch.setattr(store.SessionsStore, "VERSION", 1)
        monkeypatch.setattr(store.SessionsStore, "_upgraders", {0: upgrade})
        create_file(state_file(0), [MockState().model_dump_json()])
        create_file(
            message_file(0), [json.dumps(session_file_header(0, version=0))]
        )
        create_file(state_file(1), [MockState().model_dump_json()])
        create_file(
            message_file(1), [json.dumps(session_file_header(1, version=1))]
        )
        with pytest.raises(store.StoreFormatError):
            async with make_sessions_store():
                pass

    async def test_aenter_raises_if_future_version_in_session_files(
        self, make_sessions_store, state_file, message_file
    ):
        create_file(state_file(0), [MockState().model_dump_json()])
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
            _, messages = await store.load_or_create(0)
            assert messages == [message1, message2]

    async def test_message_with_unicode_and_newlines(self, sessions_store):
        message = MockMessage(payload="hello\nworld\n\ttab\u00e9\U0001f600")
        await sessions_store.append_message(0, message)
        _, messages = await sessions_store.load_or_create(0)
        assert messages == [message]

    async def test_read_after_append_on_same_instance(self, sessions_store):
        message = MockMessage(payload="a")
        await sessions_store.append_message(0, message)
        # Read from the same store instance (which has the file open for
        # appending). The read uses a separate file handle.
        _, messages = await sessions_store.load_or_create(0)
        assert messages == [message]

    async def test_for_session(self, sessions_store):
        message1 = MockMessage(payload="a")
        message2 = MockMessage(payload="b")
        await sessions_store.append_message(0, message1)
        session_store = sessions_store.for_session(0)
        state, messages = await session_store.load_or_create()
        state.payload = "updated"
        assert messages == [message1]
        await session_store.append_message(message2)
        state, messages = await session_store.load_or_create()
        assert state.payload == "updated"
        assert messages == [message1, message2]
