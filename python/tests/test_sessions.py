"""sessions 模組的測試。"""

import pytest

from harness import sessions


def test_create_writes_each_message_as_one_line(tmp_path):
    session = sessions.create(tmp_path)

    session.append({"role": "user", "content": "你好"})
    session.append({"role": "assistant", "content": "哈囉"})

    lines = session.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "你好" in lines[0]


def test_load_restores_messages(tmp_path):
    session = sessions.create(tmp_path)
    session.append({"role": "user", "content": "你好"})

    restored = sessions.load(tmp_path, session.id)

    assert restored.messages == [{"role": "user", "content": "你好"}]
    assert restored.id == session.id


def test_load_raises_on_unknown_session(tmp_path):
    with pytest.raises(FileNotFoundError, match="找不到 session"):
        sessions.load(tmp_path, "nope")


def test_loaded_session_keeps_appending_to_same_file(tmp_path):
    session = sessions.create(tmp_path)
    session.append({"role": "user", "content": "一"})

    restored = sessions.load(tmp_path, session.id)
    restored.append({"role": "user", "content": "二"})

    assert len(sessions.load(tmp_path, session.id).messages) == 2


def test_latest_returns_newest_session(tmp_path):
    for name in ("20260101-000000-aaaaaa", "20260201-000000-bbbbbb"):
        (tmp_path / f"{name}.jsonl").write_text("", encoding="utf-8")

    assert sessions.latest(tmp_path).id == "20260201-000000-bbbbbb"


def test_latest_returns_none_when_directory_is_missing(tmp_path):
    assert sessions.latest(tmp_path / "missing") is None


def test_in_memory_session_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = sessions.Session()

    session.append({"role": "user", "content": "你好"})

    assert session.path is None
    assert list(tmp_path.iterdir()) == []
