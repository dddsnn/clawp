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
)

from clawp import message as msg
from clawp import model as mdl
from clawp import store


def create_file(path: pathlib.Path, lines: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.open("x").close()
    write_file_content(path, lines)


def write_file_content(
        path: pathlib.Path, lines: list[str] | None = None) -> None:
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)
    if not path.is_file():
        path.open("x").close()
    with path.open("w") as f:
        f.writelines(line + "\n" for line in (lines or []))


def read_file_content(path: pathlib.Path) -> list[str]:
    with path.open("r") as f:
        return [line.rstrip("\n") for line in f.readlines()]


def session_file_header(session_seq, version=store.MessageStore.VERSION):
    return {
        "version": version,
        "session_seq": session_seq,}


def session_file_for_base_dir(base_dir, session_seq):
    return (base_dir / "sessions" / f"{session_seq}.jsonl")


class MockMessageModel(pyd.BaseModel):
    payload: str


@dc.dataclass
class MockMessage:
    payload: str

    @staticmethod
    def from_model(model: MockMessageModel) -> "MockMessage":
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
    monkeypatch.setattr(
        mdl, "MessageTypeAdapter", pyd.TypeAdapter(MockMessageModel))


@pytest.fixture
def base_dir(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return d


@pytest.fixture
async def make_message_store(base_dir, monkeypatch):
    # Set a new class-level _message_store_lock so it is bound to this
    # test's event loop.
    assert not store.MessageStore._message_store_lock.locked()
    monkeypatch.setattr(
        store.MessageStore, "_message_store_lock", asyncio.Lock())

    def factory():
        return store.MessageStore(base_dir)

    return factory


@pytest.fixture
async def message_store(make_message_store):
    async with make_message_store() as s:
        yield s


class TestMessageStore:
    @pytest.fixture
    def session_file(self, base_dir):
        def getter(session_seq):
            return session_file_for_base_dir(base_dir, session_seq)

        return getter

    async def test_append_message_creates_file_with_header(
            self, message_store, session_file):
        await message_store.append_message(0, MockMessage(payload="a"))
        lines = read_file_content(session_file(0))
        assert len(lines) == 2
        assert json.loads(lines[0]) == session_file_header(0)

    async def test_append_message_raises_if_previous_file_doesnt_exist(
            self, message_store):
        with pytest.raises(store.StoreFormatError):
            await message_store.append_message(1, MockMessage(payload="a"))

    async def test_append_message(self, message_store, session_file):
        message = MockMessage(payload="a")
        await message_store.append_message(0, message)
        lines = read_file_content(session_file(0))
        assert len(lines) == 2
        assert MockMessage.from_model(
            MockMessageModel.model_validate_json(lines[1])) == message

    async def test_append_multiple_messages(self, message_store):
        message1 = MockMessage(payload="a")
        message2 = MockMessage(payload="b")
        await message_store.append_message(0, message1)
        await message_store.append_message(0, message2)
        messages = await message_store.read_session_messages(0)
        assert messages == [message1, message2]

    async def test_append_message_creates_base_dir(
            self, message_store, base_dir, session_file):
        shutil.rmtree(base_dir)
        message = MockMessage(payload="a")
        await message_store.append_message(0, message)
        lines = read_file_content(session_file(0))
        assert len(lines) == 2
        assert MockMessage.from_model(
            MockMessageModel.model_validate_json(lines[1])) == message

    async def test_append_message_creates_sessions_dir(
            self, message_store, base_dir, session_file):
        shutil.rmtree(base_dir / "sessions")
        message = MockMessage(payload="a")
        await message_store.append_message(0, message)
        lines = read_file_content(session_file(0))
        assert len(lines) == 2
        assert MockMessage.from_model(
            MockMessageModel.model_validate_json(lines[1])) == message

    async def test_read_session_messages_empty_if_no_base_dir(
            self, message_store, base_dir):
        shutil.rmtree(base_dir)
        messages = await message_store.read_session_messages(0)
        assert messages == []

    async def test_read_session_messages_empty_if_no_sessions_dir(
            self, message_store, base_dir):
        shutil.rmtree(base_dir / "sessions")
        messages = await message_store.read_session_messages(0)
        assert messages == []

    async def test_read_session_messages_empty_if_missing(self, message_store):
        messages = await message_store.read_session_messages(0)
        assert messages == []

    async def test_read_session_messages_empty_session(
            self, message_store, session_file):
        write_file_content(
            session_file(0), [json.dumps(session_file_header(0))])
        messages = await message_store.read_session_messages(0)
        assert messages == []

    async def test_get_active_session_seq_0_if_no_base_dir(
            self, message_store, base_dir):
        shutil.rmtree(base_dir)
        assert message_store.get_active_session_seq() == 0

    async def test_get_active_session_seq_0_if_no_sessions_dir(
            self, message_store, base_dir):
        shutil.rmtree(base_dir / "sessions")
        assert message_store.get_active_session_seq() == 0

    async def test_get_active_session_seq_0_if_no_sessions(
            self, message_store, session_file):
        sessions_dir = session_file(0).parent
        assert not list(sessions_dir.iterdir())
        assert message_store.get_active_session_seq() == 0

    async def test_get_active_session_seq(self, message_store, session_file):
        create_file(session_file(2))
        create_file(session_file(0))
        create_file(session_file(1))
        assert message_store.get_active_session_seq() == 2

    async def test_get_active_session_seq_returns_latest_even_if_some_missing(
            self, message_store, session_file):
        create_file(session_file(3))
        create_file(session_file(0))
        create_file(session_file(1))
        assert message_store.get_active_session_seq() == 3

    async def test_get_active_session_seq_ignores_non_session_files(
            self, message_store, session_file):
        create_file(session_file(0))
        sessions_dir = session_file(0).parent
        create_file(sessions_dir / "1.not_jsonl")
        create_file(sessions_dir / "1_then_not_a_number.jsonl")
        assert message_store.get_active_session_seq() == 0

    async def test_multiple_sessions_are_independent(self, message_store):
        message0 = MockMessage(payload="a")
        message1 = MockMessage(payload="b")
        await message_store.append_message(0, message0)
        await message_store.append_message(1, message1)
        assert await message_store.read_session_messages(0) == [message0]
        assert await message_store.read_session_messages(1) == [message1]

    async def test_aenter_after_aexit(self, make_message_store):
        async with make_message_store() as store:
            message = MockMessage(payload="a")
            await store.append_message(0, message)
        async with store:
            messages = await store.read_session_messages(0)
            assert messages == [message]

    async def test_aenter_in_new_instance(self, make_message_store):
        async with make_message_store() as store:
            message = MockMessage(payload="a")
            await store.append_message(0, message)
        async with make_message_store() as store:
            messages = await store.read_session_messages(0)
            assert messages == [message]

    async def test_only_one_instance_can_be_active(self, make_message_store):
        async with make_message_store():
            with pytest.raises(store.StoreConcurrentError):
                async with make_message_store():
                    pass

    async def test_aenter_creates_base_dir_and_sessions_dir(self, tmp_path):
        base_dir = tmp_path / "store"
        assert not base_dir.exists()
        async with store.MessageStore(base_dir):
            assert base_dir.exists()
            assert (base_dir / "sessions").exists()

    async def test_aenter_accepts_valid_existing_base_dir(
            self, make_message_store, session_file):
        create_file(session_file(0), [json.dumps(session_file_header(0))])
        create_file(session_file(1), [json.dumps(session_file_header(1))])
        async with make_message_store():
            pass

    async def test_aenter_raises_if_session_seq_doesnt_start_at_0(
            self, make_message_store, session_file):
        create_file(session_file(1), [json.dumps(session_file_header(1))])
        with pytest.raises(store.StoreFormatError):
            async with make_message_store():
                pass

    async def test_aenter_raises_if_sessions_have_missing_seqs(
            self, make_message_store, session_file):
        create_file(session_file(0), [json.dumps(session_file_header(0))])
        create_file(session_file(2), [json.dumps(session_file_header(2))])
        with pytest.raises(store.StoreFormatError):
            async with make_message_store():
                pass

    async def test_aenter_raises_if_session_has_invalid_header_json(
            self, make_message_store, session_file):
        create_file(session_file(0), ["not json"])
        with pytest.raises(store.StoreFormatError):
            async with make_message_store():
                pass

    @pytest.mark.parametrize(
        "key,value", [("version", "not an int"),
                      ("session_seq", "not an int")])
    async def test_aenter_raises_if_session_has_invalid_header(
            self, make_message_store, session_file, key, value):
        header = session_file_header(0)
        header[key] = value
        create_file(session_file(0), [json.dumps(header)])
        with pytest.raises(store.StoreFormatError):
            async with make_message_store():
                pass

    async def test_aenter_raises_if_session_has_inconsistent_header(
            self, make_message_store, session_file):
        header = session_file_header(0)
        header["session_seq"] = 1
        create_file(session_file(0), [json.dumps(header)])
        with pytest.raises(store.StoreFormatError):
            async with make_message_store():
                pass

    async def test_aenter_upgrades_older_version(
            self, make_message_store, session_file, monkeypatch):
        def upgrade(path):
            assert path.is_file()
            write_file_content(path, ["upgraded"])

        monkeypatch.setattr(store.MessageStore, "VERSION", 1)
        monkeypatch.setattr(store.MessageStore, "_upgraders", {0: upgrade})
        create_file(
            session_file(0), [json.dumps(session_file_header(0, version=0))])
        create_file(
            session_file(1), [json.dumps(session_file_header(1, version=0))])
        async with make_message_store():
            assert read_file_content(session_file(0)) == ["upgraded"]
            assert read_file_content(session_file(1)) == ["upgraded"]

    async def test_aenter_upgrades_multiple_version_steps(
            self, make_message_store, session_file, monkeypatch):
        def upgrade_0(path):
            assert path.is_file()
            write_file_content(path, ["upgraded 0"])

        def upgrade_1(path):
            assert read_file_content(path) == ["upgraded 0"]
            assert path.is_file()
            write_file_content(path, ["upgraded 1"])

        monkeypatch.setattr(store.MessageStore, "VERSION", 2)
        monkeypatch.setattr(
            store.MessageStore, "_upgraders", {0: upgrade_0, 1: upgrade_1})
        create_file(
            session_file(0), [json.dumps(session_file_header(0, version=0))])
        async with make_message_store():
            assert read_file_content(session_file(0)) == ["upgraded 1"]

    async def test_aenter_backs_up_before_upgrade(
            self, make_message_store, session_file, monkeypatch, base_dir):
        def upgrade(path):
            assert path.is_file()
            write_file_content(path, ["upgraded"])

        monkeypatch.setattr(store.MessageStore, "VERSION", 1)
        monkeypatch.setattr(store.MessageStore, "_upgraders", {0: upgrade})
        lines_before_upgrade = [
            json.dumps(session_file_header(0, version=0)),
            json.dumps({"payload": "a"})]
        create_file(session_file(0), lines_before_upgrade)
        async with make_message_store():
            backup_dirs = list(base_dir.parent.glob("backup*"))
            assert len(backup_dirs) == 1
            backup_dir_match = re.match(
                f"backup_{base_dir.name}_version_(?P<version>[0-9]+)"
                "_(?P<timestamp>.*)", backup_dirs[0].name)
            assert backup_dir_match
            assert backup_dir_match.group("version") == "0"
            # Make sure the timestamp parses.
            we.Instant(backup_dir_match.group("timestamp"))
        backup_file = session_file_for_base_dir(backup_dirs[0], 0)
        assert read_file_content(backup_file) == lines_before_upgrade

    async def test_aenter_raises_if_multiple_versions_in_session_files(
            self, make_message_store, session_file, monkeypatch):
        def upgrade(path):
            pass

        monkeypatch.setattr(store.MessageStore, "VERSION", 1)
        monkeypatch.setattr(store.MessageStore, "_upgraders", {0: upgrade})
        create_file(
            session_file(0), [json.dumps(session_file_header(0, version=0))])
        create_file(
            session_file(1), [json.dumps(session_file_header(1, version=1))])
        with pytest.raises(store.StoreFormatError):
            async with make_message_store():
                pass

    async def test_aenter_raises_if_future_version_in_session_files(
            self, make_message_store, session_file):
        create_file(
            session_file(0), [
                json.dumps(
                    session_file_header(
                        0, version=store.MessageStore.VERSION + 1))])
        with pytest.raises(store.StoreFormatError):
            async with make_message_store():
                pass

    async def test_append_after_reopen(self, make_message_store):
        async with make_message_store() as store:
            message1 = MockMessage(payload="a")
            await store.append_message(0, message1)
        async with store:
            message2 = MockMessage(payload="b")
            await store.append_message(0, message2)
            messages = await store.read_session_messages(0)
            assert messages == [message1, message2]

    async def test_read_discards_truncated_last_line(
            self, make_message_store, session_file):
        async with make_message_store() as store:
            message = MockMessage(payload="a")
            await store.append_message(0, message)
        # Simulate a crash by appending a partial line.
        with open(session_file(0), "a") as f:
            f.write('{"payload": "some s')
        async with store:
            messages = await store.read_session_messages(0)
            assert messages == [message]

    async def test_read_deletes_truncated_last_line(
            self, make_message_store, session_file):
        async with make_message_store() as store:
            message = MockMessage(payload="a")
            await store.append_message(0, message)
        # Simulate a crash by appending a partial line.
        with open(session_file(0), "a") as f:
            f.write('{"payload": "some s')
        async with store:
            await store.read_session_messages(0)
        content = read_file_content(session_file(0))
        assert len(content) == 2
        assert json.loads(content[1]) == (await message.model).model_dump()

    async def test_read_raises_on_corrupt_non_last_line(
            self, make_message_store, session_file):
        async with make_message_store() as message_store:
            message = MockMessage(payload="a")
            await message_store.append_message(0, message)
        # Write a corrupt line followed by a valid line.
        with open(session_file(0), "a") as f:
            f.write("not json\n")
            f.write('{"payload":"a"}\n')
        async with message_store:
            with pytest.raises(store.StoreFormatError):
                await message_store.read_session_messages(0)

    async def test_read_raises_on_empty_non_last_line(
            self, make_message_store, session_file):
        async with make_message_store() as message_store:
            message = MockMessage(payload="a")
            await message_store.append_message(0, message)
        with open(session_file(0), "a") as f:
            f.write("\n")
            f.write('{"payload":"a"}\n')
        async with message_store:
            with pytest.raises(store.StoreFormatError):
                await message_store.read_session_messages(0)

    async def test_message_with_unicode_and_newlines(self, message_store):
        message = MockMessage(payload="hello\nworld\n\ttab\u00e9\U0001f600")
        await message_store.append_message(0, message)
        messages = await message_store.read_session_messages(0)
        assert messages == [message]

    async def test_read_after_append_on_same_instance(self, message_store):
        message = MockMessage(payload="a")
        await message_store.append_message(0, message)
        # Read from the same store instance (which has the file open for
        # appending). The read uses a separate file handle.
        messages = await message_store.read_session_messages(0)
        assert messages == [message]

    async def test_get_session_message_store(self, message_store):
        message1 = MockMessage(payload="a")
        message2 = MockMessage(payload="b")
        await message_store.append_message(0, message1)
        session_message_store = message_store.get_session_message_store(0)
        assert await session_message_store.read_session_messages() == [
            message1]
        await session_message_store.append_message(message2)
        assert await message_store.read_session_messages(0) == [
            message1, message2]


def memory(**kwargs):
    return all_of(instance_of(mdl.Memory), has_properties(**kwargs))


class TestJsonlMemoryStore:
    @pytest.fixture
    def memory_store(self, tmp_path):
        return store.JsonlMemoryStore(tmp_path / "memory")

    async def test_log_memory_creates_file(self, memory_store):
        await memory_store.log_memory("test event")
        lines = read_file_content(memory_store._base_dir / "memory.jsonl")
        assert len(lines) == 1
        data = json.loads(lines[0])
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
                memory(content="event 1"), memory(content="event 2")))

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
                memory(content="event 2"), memory(content="event 3")))

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
                memory(content="event 1"), memory(content="event 2")))

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
            m async for m in memory_store.search_memory(
                start_time=t2, end_time=t2)]
        assert_that(results, contains_exactly(memory(content="event 2")))

    async def test_search_memory_by_search_term(self, memory_store):
        await memory_store.log_memory("hello world")
        await memory_store.log_memory("testing memories")
        await memory_store.log_memory("goodbye")

        results = [
            m async for m in memory_store.search_memory(search_term="hello")]
        assert_that(results, contains_exactly(memory(content="hello world")))

    async def test_search_memory_by_search_term_is_case_insensitive(
            self, memory_store):
        await memory_store.log_memory("Hello World")
        await memory_store.log_memory("testing memories")
        await memory_store.log_memory("hello again")

        results = [
            m async for m in memory_store.search_memory(search_term="HeLlO")]
        assert_that(
            results,
            contains_exactly(
                memory(content="Hello World"), memory(content="hello again")))

    async def test_search_memory_raises_format_error_on_corrupt_line(
            self, memory_store):
        await memory_store.log_memory("valid event")
        # Manually append an invalid line.
        with open(memory_store._base_dir / "memory.jsonl", "a") as f:
            f.write("this is not json\n")
        with pytest.raises(store.StoreFormatError):
            [m async for m in memory_store.search_memory()]

    async def test_search_memory_raises_format_error_on_empty_line(
            self, memory_store):
        await memory_store.log_memory("valid event")
        # Manually append an empty line.
        with open(memory_store._base_dir / "memory.jsonl", "a") as f:
            f.write("\n")
        with pytest.raises(store.StoreFormatError):
            [m async for m in memory_store.search_memory()]
