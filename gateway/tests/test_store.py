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
import dataclasses as dc
import json
import pathlib
import re
import uuid

import pydantic as pyd
import pytest
import whenever as we

import store


def asst_id(id_int):
    return uuid.UUID(int=id_int)


def con_id(id_int):
    return uuid.UUID(int=1 << 8 + id_int)


def create_file(path: pathlib.Path, lines: list[str] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.open("x").close()
    write_file_content(path, lines)


def write_file_content(path: pathlib.Path, lines: list[str] = None) -> None:
    assert path.is_file()
    with path.open("w") as f:
        f.writelines(line + "\n" for line in (lines or []))


def read_file_content(path: pathlib.Path) -> list[str]:
    with path.open("r") as f:
        return [line.rstrip("\n") for line in f.readlines()]


def session_file_header(
        assistant_id_int, consciousness_id_int, session_seq,
        version=store.MessageStore.VERSION):
    return {
        "version": version,
        "assistant_id": str(asst_id(assistant_id_int)),
        "consciousness_id": str(con_id(consciousness_id_int)),
        "session_seq": session_seq,}


def session_file_for_base_dir(
        base_dir, assistant_id_int, consciousness_id_int, session_seq):
    return (
        base_dir / "assistants" / str(asst_id(assistant_id_int))
        / "consciousnesses" / str(con_id(consciousness_id_int)) / "sessions"
        / f"{session_seq}.jsonl")


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


class TestMessageStore:
    @pytest.fixture(autouse=True)
    def mock_message(self, monkeypatch):
        import message
        import model
        monkeypatch.setattr(message, "Message", MockMessage)
        monkeypatch.setattr(
            model, "MessageTypeAdapter", pyd.TypeAdapter(MockMessageModel))

    @pytest.fixture
    def base_dir(self, tmp_path):
        d = tmp_path / "store"
        d.mkdir()
        return d

    @pytest.fixture
    async def make_message_store(self, base_dir, monkeypatch):
        # Set a new class-level _message_store_lock so it is bound to this
        # test's event loop.
        assert not store.MessageStore._message_store_lock.locked()
        monkeypatch.setattr(
            store.MessageStore, "_message_store_lock", asyncio.Lock())

        def factory():
            return store.MessageStore(base_dir)

        return factory

    @pytest.fixture
    async def message_store(self, make_message_store):
        async with make_message_store() as s:
            yield s

    @pytest.fixture
    def session_file(self, base_dir):
        def getter(assistant_id_int, consciousness_id_int, session_seq):
            return session_file_for_base_dir(
                base_dir, assistant_id_int, consciousness_id_int, session_seq)

        return getter

    async def test_create_session_creates_file_with_header(
            self, message_store, session_file):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        assert read_file_content(session_file(1, 1, 0)) == [
            json.dumps(session_file_header(1, 1, 0))]

    async def test_create_session_raises_if_exists(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        with pytest.raises(FileExistsError):
            await message_store.create_session(asst_id(1), con_id(1), 0)

    async def test_append_message(self, message_store, session_file):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        msg = MockMessage(payload="a")
        await message_store.append_message(asst_id(1), con_id(1), 0, msg)
        lines = read_file_content(session_file(1, 1, 0))
        assert len(lines) == 2
        assert MockMessage.from_model(
            MockMessageModel.model_validate_json(lines[1])) == msg

    async def test_append_multiple_messages(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        msg1 = MockMessage(payload="a")
        msg2 = MockMessage(payload="b")
        await message_store.append_message(asst_id(1), con_id(1), 0, msg1)
        await message_store.append_message(asst_id(1), con_id(1), 0, msg2)
        messages = await message_store.read_session_messages(
            asst_id(1), con_id(1), 0)
        assert messages == [msg1, msg2]

    async def test_append_message_raises_if_session_missing(
            self, message_store):
        with pytest.raises(FileNotFoundError):
            await message_store.append_message(
                asst_id(1), con_id(1), 0, MockMessage(payload="a"))

    async def test_read_session_messages_empty_session(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        messages = await message_store.read_session_messages(
            asst_id(1), con_id(1), 0)
        assert messages == []

    async def test_read_session_messages_raises_if_missing(
            self, message_store):
        with pytest.raises(FileNotFoundError):
            await message_store.read_session_messages(asst_id(1), con_id(1), 0)

    async def test_list_assistants_empty(self, message_store):
        assert message_store.list_assistants() == []

    async def test_list_assistants(self, message_store):
        await message_store.create_session(asst_id(2), con_id(1), 0)
        await message_store.create_session(asst_id(1), con_id(1), 0)
        assert message_store.list_assistants() == [asst_id(1), asst_id(2)]

    async def test_list_consciousnesses_empty(self, message_store):
        assert message_store.list_consciousnesses(asst_id(1)) == []

    async def test_list_consciousnesses(self, message_store):
        await message_store.create_session(asst_id(1), con_id(2), 0)
        await message_store.create_session(asst_id(1), con_id(1), 0)
        assert message_store.list_consciousnesses(asst_id(1)) == [
            con_id(1), con_id(2)]

    async def test_list_sessions_empty(self, message_store):
        assert message_store.list_sessions(asst_id(1), con_id(1)) == []

    async def test_list_sessions(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 3)
        await message_store.create_session(asst_id(1), con_id(1), 0)
        await message_store.create_session(asst_id(1), con_id(1), 1)
        assert message_store.list_sessions(asst_id(1), con_id(1)) == [0, 1, 3]

    async def test_list_sessions_ignores_non_session_files(
            self, message_store, session_file):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        sessions_dir = session_file(1, 1, 0).parent
        create_file(sessions_dir / "1.not_jsonl")
        create_file(sessions_dir / "not_a_number.jsonl")
        assert message_store.list_sessions(asst_id(1), con_id(1)) == [0]

    async def test_multiple_assistants_are_independent(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        await message_store.create_session(asst_id(2), con_id(1), 0)
        msg1 = MockMessage(payload="a")
        msg2 = MockMessage(payload="b")
        await message_store.append_message(asst_id(1), con_id(1), 0, msg1)
        await message_store.append_message(asst_id(2), con_id(1), 0, msg2)
        assert await message_store.read_session_messages(
            asst_id(1), con_id(1), 0) == [msg1]
        assert await message_store.read_session_messages(
            asst_id(2), con_id(1), 0) == [msg2]

    async def test_multiple_sessions_are_independent(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        await message_store.create_session(asst_id(1), con_id(1), 1)
        msg0 = MockMessage(payload="a")
        msg1 = MockMessage(payload="b")
        await message_store.append_message(asst_id(1), con_id(1), 0, msg0)
        await message_store.append_message(asst_id(1), con_id(1), 1, msg1)
        assert await message_store.read_session_messages(
            asst_id(1), con_id(1), 0) == [msg0]
        assert await message_store.read_session_messages(
            asst_id(1), con_id(1), 1) == [msg1]

    async def test_aenter_after_aexit(self, make_message_store):
        async with make_message_store() as store:
            await store.create_session(asst_id(1), con_id(1), 0)
            msg = MockMessage(payload="a")
            await store.append_message(asst_id(1), con_id(1), 0, msg)
        async with store:
            messages = await store.read_session_messages(
                asst_id(1), con_id(1), 0)
            assert messages == [msg]

    async def test_aenter_in_new_instance(self, make_message_store):
        async with make_message_store() as store:
            await store.create_session(asst_id(1), con_id(1), 0)
            msg = MockMessage(payload="a")
            await store.append_message(asst_id(1), con_id(1), 0, msg)
        async with make_message_store() as store:
            messages = await store.read_session_messages(
                asst_id(1), con_id(1), 0)
            assert messages == [msg]

    async def test_only_one_instance_can_be_active(self, make_message_store):
        async with make_message_store():
            with pytest.raises(store.MessageStoreConcurrentError):
                async with make_message_store():
                    pass

    async def test_aenter_creates_base_dir(self, tmp_path):
        base_dir = tmp_path / "store"
        assert not base_dir.exists()
        async with store.MessageStore(base_dir):
            assert base_dir.exists()

    async def test_aenter_accepts_valid_existing_base_dir(
            self, make_message_store, session_file):
        create_file(
            session_file(1, 1, 0), [json.dumps(session_file_header(1, 1, 0))])
        create_file(
            session_file(1, 1, 1), [json.dumps(session_file_header(1, 1, 1))])
        create_file(
            session_file(1, 2, 0), [json.dumps(session_file_header(1, 2, 0))])
        create_file(
            session_file(2, 1, 0), [json.dumps(session_file_header(2, 1, 0))])
        async with make_message_store():
            pass

    @pytest.mark.parametrize("seq", [1, -1])
    async def test_aenter_raises_if_session_seq_doesnt_start_at_0(
            self, make_message_store, session_file, seq):
        create_file(
            session_file(1, 1, seq),
            [json.dumps(session_file_header(1, 1, seq))])
        with pytest.raises(store.MessageStoreFormatError):
            async with make_message_store():
                pass

    async def test_aenter_raises_if_sessions_have_missing_seqs(
            self, make_message_store, session_file):
        create_file(
            session_file(1, 1, 0), [json.dumps(session_file_header(1, 1, 0))])
        create_file(
            session_file(1, 1, 2), [json.dumps(session_file_header(1, 1, 2))])
        with pytest.raises(store.MessageStoreFormatError):
            async with make_message_store():
                pass

    async def test_aenter_raises_if_session_has_invalid_header_json(
            self, make_message_store, session_file):
        create_file(session_file(1, 1, 0), ["not json"])
        with pytest.raises(store.MessageStoreFormatError):
            async with make_message_store():
                pass

    @pytest.mark.parametrize(
        "key,value", [("version", "not an int"),
                      ("assistant_id", "not a uuid"),
                      ("consciousness_id", "not a uuid"),
                      ("session_seq", "not an int")])
    async def test_aenter_raises_if_session_has_invalid_header(
            self, make_message_store, session_file, key, value):
        header = session_file_header(1, 1, 0)
        header[key] = value
        create_file(session_file(1, 1, 0), [json.dumps(header)])
        with pytest.raises(store.MessageStoreFormatError):
            async with make_message_store():
                pass

    @pytest.mark.parametrize(
        "key,value", [("assistant_id", str(asst_id(2))),
                      ("consciousness_id", str(con_id(2))),
                      ("session_seq", 1)])
    async def test_aenter_raises_if_session_has_inconsistent_header(
            self, make_message_store, session_file, key, value):
        header = session_file_header(1, 1, 0)
        header[key] = value
        create_file(session_file(1, 1, 0), [json.dumps(header)])
        with pytest.raises(store.MessageStoreFormatError):
            async with make_message_store():
                pass

    async def test_aenter_upgrades_older_version(
            self, make_message_store, session_file, monkeypatch):
        def upgrade(path):
            write_file_content(path, ["upgraded"])

        monkeypatch.setattr(store.MessageStore, "VERSION", 1)
        monkeypatch.setattr(store.MessageStore, "_upgraders", {0: upgrade})
        create_file(
            session_file(1, 1, 0),
            [json.dumps(session_file_header(1, 1, 0, version=0))])
        create_file(
            session_file(2, 1, 0),
            [json.dumps(session_file_header(2, 1, 0, version=0))])
        async with make_message_store():
            assert read_file_content(session_file(1, 1, 0)) == ["upgraded"]
            assert read_file_content(session_file(2, 1, 0)) == ["upgraded"]

    async def test_aenter_upgrades_multiple_version_steps(
            self, make_message_store, session_file, monkeypatch):
        def upgrade_0(path):
            write_file_content(path, ["upgraded 0"])

        def upgrade_1(path):
            assert read_file_content(path) == ["upgraded 0"]
            write_file_content(path, ["upgraded 1"])

        monkeypatch.setattr(store.MessageStore, "VERSION", 2)
        monkeypatch.setattr(
            store.MessageStore, "_upgraders", {0: upgrade_0, 1: upgrade_1})
        create_file(
            session_file(1, 1, 0),
            [json.dumps(session_file_header(1, 1, 0, version=0))])
        async with make_message_store():
            assert read_file_content(session_file(1, 1, 0)) == ["upgraded 1"]

    async def test_aenter_backs_up_before_upgrade(
            self, make_message_store, session_file, monkeypatch, base_dir):
        def upgrade(path):
            write_file_content(path, ["upgraded"])

        monkeypatch.setattr(store.MessageStore, "VERSION", 1)
        monkeypatch.setattr(store.MessageStore, "_upgraders", {0: upgrade})
        lines_before_upgrade = [
            json.dumps(session_file_header(1, 1, 0, version=0)),
            json.dumps({"payload": "a"})]
        create_file(session_file(1, 1, 0), lines_before_upgrade)
        async with make_message_store():
            backup_dirs = list(base_dir.parent.glob("backup*"))
            assert len(backup_dirs) == 1
            backup_dir_match = re.match(
                f"backup_{base_dir.name}_version_(?P<version>[0-9]+)"
                "_(?P<timestamp>.*)", backup_dirs[0].name)
            assert backup_dir_match.group("version") == "0"
            # Make sure the timestamp parses.
            we.Instant(backup_dir_match.group("timestamp"))
        backup_file = session_file_for_base_dir(backup_dirs[0], 1, 1, 0)
        assert read_file_content(backup_file) == lines_before_upgrade

    async def test_aenter_raises_if_multiple_versions_in_session_files(
            self, make_message_store, session_file, monkeypatch):
        def upgrade(path):
            pass

        monkeypatch.setattr(store.MessageStore, "VERSION", 1)
        monkeypatch.setattr(store.MessageStore, "_upgraders", {0: upgrade})
        create_file(
            session_file(1, 1, 0),
            [json.dumps(session_file_header(1, 1, 0, version=0))])
        create_file(
            session_file(2, 1, 0),
            [json.dumps(session_file_header(2, 1, 0, version=1))])
        with pytest.raises(store.MessageStoreFormatError):
            async with make_message_store():
                pass

    async def test_aenter_raises_if_future_version_in_session_files(
            self, make_message_store, session_file):
        create_file(
            session_file(1, 1, 0), [
                json.dumps(
                    session_file_header(
                        1, 1, 0, version=store.MessageStore.VERSION + 1))])
        with pytest.raises(store.MessageStoreFormatError):
            async with make_message_store():
                pass

    async def test_append_after_reopen(self, make_message_store):
        async with make_message_store() as store:
            await store.create_session(asst_id(1), con_id(1), 0)
            msg1 = MockMessage(payload="a")
            await store.append_message(asst_id(1), con_id(1), 0, msg1)
        async with store:
            msg2 = MockMessage(payload="b")
            await store.append_message(asst_id(1), con_id(1), 0, msg2)
            messages = await store.read_session_messages(
                asst_id(1), con_id(1), 0)
            assert messages == [msg1, msg2]

    async def test_read_discards_truncated_last_line(
            self, make_message_store, session_file):
        async with make_message_store() as store:
            await store.create_session(asst_id(1), con_id(1), 0)
            msg = MockMessage(payload="a")
            await store.append_message(asst_id(1), con_id(1), 0, msg)
        # Simulate a crash by appending a partial line.
        with open(session_file(1, 1, 0), "a") as f:
            f.write('{"payload": "some s')
        async with store:
            messages = await store.read_session_messages(
                asst_id(1), con_id(1), 0)
            assert messages == [msg]

    async def test_read_raises_on_corrupt_non_last_line(
            self, make_message_store, session_file):
        async with make_message_store() as message_store:
            await message_store.create_session(asst_id(1), con_id(1), 0)
        # Write a corrupt line followed by a valid line.
        with open(session_file(1, 1, 0), "a") as f:
            f.write("not json\n")
            f.write('{"payload":"a"}\n')
        async with message_store:
            with pytest.raises(store.MessageStoreFormatError):
                await message_store.read_session_messages(
                    asst_id(1), con_id(1), 0)

    async def test_read_raises_on_empty_non_last_line(
            self, make_message_store, session_file):
        async with make_message_store() as message_store:
            await message_store.create_session(asst_id(1), con_id(1), 0)
        with open(session_file(1, 1, 0), "a") as f:
            f.write("\n")
            f.write('{"payload":"a"}\n')
        async with message_store:
            with pytest.raises(store.MessageStoreFormatError):
                await message_store.read_session_messages(
                    asst_id(1), con_id(1), 0)

    async def test_message_with_unicode_and_newlines(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        msg = MockMessage(payload="hello\nworld\n\ttab\u00e9\U0001f600")
        await message_store.append_message(asst_id(1), con_id(1), 0, msg)
        messages = await message_store.read_session_messages(
            asst_id(1), con_id(1), 0)
        assert messages == [msg]

    async def test_read_after_append_on_same_instance(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        msg = MockMessage(payload="a")
        await message_store.append_message(asst_id(1), con_id(1), 0, msg)
        # Read from the same store instance (which has the file open for
        # appending). The read uses a separate file handle.
        messages = await message_store.read_session_messages(
            asst_id(1), con_id(1), 0)
        assert messages == [msg]
