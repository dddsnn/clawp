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

import json
import uuid

import pytest

import store


def asst_id(i):
    return uuid.UUID(int=i)


def con_id(i):
    return uuid.UUID(int=1 << 8 + i)


class TestMessageStore:
    @pytest.fixture
    def base_dir(self, tmp_path):
        d = tmp_path / "store"
        d.mkdir()
        return d

    @pytest.fixture
    async def message_store(self, base_dir):
        s = store.MessageStore(base_dir)
        yield s
        await s.close()

    async def test_create_session_creates_file_with_header(
            self, message_store, base_dir):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        path = (
            base_dir / "assistants" / str(asst_id(1)) / "consciousnesses"
            / str(con_id(1)) / "sessions" / "0.jsonl")
        assert path.exists()
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        header = json.loads(lines[0])
        assert header == {
            "version": store.VERSION,
            "assistant_id": str(asst_id(1)),
            "consciousness_id": str(con_id(1)),
            "session_seq": 0,}

    async def test_create_session_raises_if_exists(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        with pytest.raises(FileExistsError):
            await message_store.create_session(asst_id(1), con_id(1), 0)

    async def test_create_session_creates_directories(
            self, message_store, base_dir):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        assert (base_dir / "assistants" / str(asst_id(1))).is_dir()
        assert (
            base_dir / "assistants" / str(asst_id(1)) / "consciousnesses"
            / str(con_id(1))).is_dir()
        assert (
            base_dir / "assistants" / str(asst_id(1)) / "consciousnesses"
            / str(con_id(1)) / "sessions").is_dir()

    async def test_append_message(self, message_store, base_dir):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        msg = {"role": "user", "content": "hello"}
        await message_store.append_message(asst_id(1), con_id(1), 0, msg)
        path = (
            base_dir / "assistants" / str(asst_id(1)) / "consciousnesses"
            / str(con_id(1)) / "sessions" / "0.jsonl")
        lines = path.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1]) == msg

    async def test_append_multiple_messages(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        msg1 = {"role": "user", "content": "hello"}
        msg2 = {"role": "assistant", "content": "hi there"}
        await message_store.append_message(asst_id(1), con_id(1), 0, msg1)
        await message_store.append_message(asst_id(1), con_id(1), 0, msg2)
        messages = await message_store.read_session_messages(
            asst_id(1), con_id(1), 0)
        assert messages == [msg1, msg2]

    async def test_append_message_raises_if_session_missing(
            self, message_store):
        with pytest.raises(FileNotFoundError):
            await message_store.append_message(
                asst_id(1), con_id(1), 0, {"role": "user", "content": "hello"})

    async def test_read_session_header(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 5)
        header = await message_store.read_session_header(
            asst_id(1), con_id(1), 5)
        assert header == {
            "version": store.VERSION,
            "assistant_id": asst_id(1),
            "consciousness_id": con_id(1),
            "session_seq": 5,}

    async def test_read_session_header_raises_if_missing(self, message_store):
        with pytest.raises(FileNotFoundError):
            await message_store.read_session_header(asst_id(1), con_id(1), 0)

    async def test_read_session_messages_empty_session(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        messages = await message_store.read_session_messages(
            asst_id(1), con_id(1), 0)
        assert messages == []

    async def test_read_session_messages_raises_if_missing(
            self, message_store):
        with pytest.raises(FileNotFoundError):
            await message_store.read_session_messages(asst_id(1), con_id(1), 0)

    async def test_read_session_messages_returns_all_messages(
            self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        messages_in = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "hi",
                "reasoning": "greeting",
                "tool_calls": [],},]
        for msg in messages_in:
            await message_store.append_message(asst_id(1), con_id(1), 0, msg)
        messages_out = await message_store.read_session_messages(
            asst_id(1), con_id(1), 0)
        assert messages_out == messages_in

    async def test_list_assistants_empty(self, message_store):
        assert await message_store.list_assistants() == []

    async def test_list_assistants(self, message_store):
        await message_store.create_session(asst_id(2), con_id(1), 0)
        await message_store.create_session(asst_id(1), con_id(1), 0)
        assert await message_store.list_assistants() == [
            asst_id(1), asst_id(2)]

    async def test_list_consciousnesses_empty(self, message_store):
        assert await message_store.list_consciousnesses(asst_id(1)) == []

    async def test_list_consciousnesses(self, message_store):
        await message_store.create_session(asst_id(1), con_id(2), 0)
        await message_store.create_session(asst_id(1), con_id(1), 0)
        assert await message_store.list_consciousnesses(asst_id(1)) == [
            con_id(1), con_id(2)]

    async def test_list_sessions_empty(self, message_store):
        assert await message_store.list_sessions(asst_id(1), con_id(1)) == []

    async def test_list_sessions(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 3)
        await message_store.create_session(asst_id(1), con_id(1), 0)
        await message_store.create_session(asst_id(1), con_id(1), 1)
        assert await message_store.list_sessions(asst_id(1),
                                                 con_id(1)) == [0, 1, 3]

    async def test_list_sessions_ignores_non_session_files(
            self, message_store, base_dir):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        sessions_dir = (
            base_dir / "assistants" / str(asst_id(1)) / "consciousnesses"
            / str(con_id(1)) / "sessions")
        (sessions_dir / "1.not_jsonl").open("x").close()
        (sessions_dir / "not_a_number.jsonl").open("x").close()
        assert await message_store.list_sessions(asst_id(1), con_id(1)) == [0]

    async def test_multiple_assistants_are_independent(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        await message_store.create_session(asst_id(2), con_id(1), 0)
        msg1 = {"role": "user", "content": "from ast-1"}
        msg2 = {"role": "user", "content": "from ast-2"}
        await message_store.append_message(asst_id(1), con_id(1), 0, msg1)
        await message_store.append_message(asst_id(2), con_id(1), 0, msg2)
        assert await message_store.read_session_messages(
            asst_id(1), con_id(1), 0) == [msg1]
        assert await message_store.read_session_messages(
            asst_id(2), con_id(1), 0) == [msg2]

    async def test_multiple_sessions_are_independent(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        await message_store.create_session(asst_id(1), con_id(1), 1)
        msg0 = {"role": "user", "content": "session 0"}
        msg1 = {"role": "user", "content": "session 1"}
        await message_store.append_message(asst_id(1), con_id(1), 0, msg0)
        await message_store.append_message(asst_id(1), con_id(1), 1, msg1)
        assert await message_store.read_session_messages(
            asst_id(1), con_id(1), 0) == [msg0]
        assert await message_store.read_session_messages(
            asst_id(1), con_id(1), 1) == [msg1]

    async def test_close_and_reopen(self, base_dir):
        store1 = store.MessageStore(base_dir)
        await store1.create_session(asst_id(1), con_id(1), 0)
        msg = {"role": "user", "content": "persisted"}
        await store1.append_message(asst_id(1), con_id(1), 0, msg)
        await store1.close()
        store2 = store.MessageStore(base_dir)
        try:
            messages = await store2.read_session_messages(
                asst_id(1), con_id(1), 0)
            assert messages == [msg]
        finally:
            await store2.close()

    async def test_append_after_reopen(self, base_dir):
        store1 = store.MessageStore(base_dir)
        await store1.create_session(asst_id(1), con_id(1), 0)
        msg1 = {"role": "user", "content": "first"}
        await store1.append_message(asst_id(1), con_id(1), 0, msg1)
        await store1.close()
        store2 = store.MessageStore(base_dir)
        try:
            msg2 = {"role": "assistant", "content": "second"}
            await store2.append_message(asst_id(1), con_id(1), 0, msg2)
            messages = await store2.read_session_messages(
                asst_id(1), con_id(1), 0)
            assert messages == [msg1, msg2]
        finally:
            await store2.close()

    async def test_read_discards_truncated_last_line(
            self, message_store, base_dir):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        msg = {"role": "user", "content": "hello"}
        await message_store.append_message(asst_id(1), con_id(1), 0, msg)
        await message_store.close()
        # Simulate a crash by appending a partial line.
        path = (
            base_dir / "assistants" / str(asst_id(1)) / "consciousnesses"
            / str(con_id(1)) / "sessions" / "0.jsonl")
        with open(path, "a") as f:
            f.write('{"role": "assistant", "cont')
        store2 = store.MessageStore(base_dir)
        try:
            messages = await store2.read_session_messages(
                asst_id(1), con_id(1), 0)
            assert messages == [msg]
        finally:
            await store2.close()

    async def test_read_raises_on_corrupt_non_last_line(
            self, message_store, base_dir):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        await message_store.close()
        # Write a corrupt line followed by a valid line.
        path = (
            base_dir / "assistants" / str(asst_id(1)) / "consciousnesses"
            / str(con_id(1)) / "sessions" / "0.jsonl")
        with open(path, "a") as f:
            f.write("not json\n")
            f.write('{"role": "user", "content": "hello"}\n')
        store2 = store.MessageStore(base_dir)
        try:
            with pytest.raises(json.JSONDecodeError):
                await store2.read_session_messages(asst_id(1), con_id(1), 0)
        finally:
            await store2.close()

    async def test_message_with_unicode_and_newlines(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        msg = {
            "role": "user",
            "content": "hello\nworld\n\ttab\u00e9\U0001f600",}
        await message_store.append_message(asst_id(1), con_id(1), 0, msg)
        messages = await message_store.read_session_messages(
            asst_id(1), con_id(1), 0)
        assert messages == [msg]

    async def test_read_after_append_on_same_instance(self, message_store):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        msg = {"role": "user", "content": "hello"}
        await message_store.append_message(asst_id(1), con_id(1), 0, msg)
        # Read from the same store instance (which has the file open for
        # appending). The read uses a separate file handle.
        messages = await message_store.read_session_messages(
            asst_id(1), con_id(1), 0)
        assert messages == [msg]

    async def test_list_sessions_ignores_non_jsonl_files(
            self, message_store, base_dir):
        await message_store.create_session(asst_id(1), con_id(1), 0)
        sessions_dir = (
            base_dir / "assistants" / str(asst_id(1)) / "consciousnesses"
            / str(con_id(1)) / "sessions")
        (sessions_dir / "notes.txt").write_text("not a session")
        assert await message_store.list_sessions(asst_id(1), con_id(1)) == [0]


class TestUpgrade:
    def test_fresh_store_creates_version_file(self, tmp_path):
        base_dir = tmp_path / "store"
        store.upgrade(base_dir)
        version_path = base_dir / "version"
        assert version_path.exists()
        assert int(version_path.read_text().strip()) == store.VERSION

    def test_current_version_is_noop(self, tmp_path):
        base_dir = tmp_path / "store"
        base_dir.mkdir()
        (base_dir / "version").write_text(str(store.VERSION) + "\n")
        store.upgrade(base_dir)
        assert int((base_dir / "version").read_text().strip()) == store.VERSION

    def test_refuses_to_downgrade(self, tmp_path):
        base_dir = tmp_path / "store"
        base_dir.mkdir()
        (base_dir / "version").write_text(str(store.VERSION + 1) + "\n")
        with pytest.raises(RuntimeError, match="refusing to downgrade"):
            store.upgrade(base_dir)

    def test_missing_version_file_assumes_version_1(self, tmp_path):
        base_dir = tmp_path / "store"
        base_dir.mkdir()
        # No version file, should assume version 1. Since VERSION is 1,
        # this should write the version file and be a noop.
        store.upgrade(base_dir)
        assert int((base_dir / "version").read_text().strip()) == store.VERSION

    def test_upgrade_runs_upgraders_in_sequence(self, tmp_path, monkeypatch):
        base_dir = tmp_path / "store"
        base_dir.mkdir()
        (base_dir / "version").write_text("1\n")
        call_log = []
        upgraders = {
            1: lambda d: call_log.append(("1->2", d)),
            2: lambda d: call_log.append(("2->3", d)),}
        monkeypatch.setattr(store, "_UPGRADERS", upgraders)
        monkeypatch.setattr(store, "VERSION", 3)
        store.upgrade(base_dir)
        assert call_log == [("1->2", base_dir), ("2->3", base_dir)]
        assert int((base_dir / "version").read_text().strip()) == 3

    def test_upgrade_raises_if_upgrader_missing(self, tmp_path, monkeypatch):
        base_dir = tmp_path / "store"
        base_dir.mkdir()
        (base_dir / "version").write_text("1\n")
        monkeypatch.setattr(store, "_UPGRADERS", {})
        monkeypatch.setattr(store, "VERSION", 2)
        with pytest.raises(RuntimeError, match="no upgrader from version 1"):
            store.upgrade(base_dir)
