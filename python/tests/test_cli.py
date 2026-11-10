"""CLI 的測試。"""

import pytest

from harness import cli
from harness import llm
from harness import sessions
from tests import conftest


@pytest.fixture
def project_dir(tmp_path):
    """用 `harness new` 建立的專案。"""
    root = tmp_path / "demo"
    assert cli.main(["new", str(root)]) == 0
    return root


@pytest.fixture
def scripted_model(monkeypatch, fake_model):
    """把 CLI 會用到的模型換成依劇本回應的假模型。"""

    def install(script):
        model = fake_model(script)
        monkeypatch.setattr(llm, "complete", model)
        return model

    return install


def test_new_creates_project_files_and_prints_next_step(tmp_path, capsys):
    code = cli.main(["new", str(tmp_path / "fresh")])

    assert code == 0
    assert (tmp_path / "fresh" / "harness.yaml").exists()
    assert "harness chat" in capsys.readouterr().out


def test_run_prints_tool_calls_and_answer(project_dir, scripted_model, capsys):
    scripted_model(
        [
            conftest.tool_response(("word_count", {"text": "一\n二"})),
            conftest.text_response("共 2 行"),
        ]
    )

    code = cli.main(["run", "-p", str(project_dir), "幾行？"])

    output = capsys.readouterr().out
    assert code == 0
    assert "⚙ word_count" in output
    assert "共 2 行" in output


def test_run_continue_flag_resumes_latest_session(project_dir, scripted_model):
    scripted_model([conftest.text_response("第一次")])
    cli.main(["run", "-p", str(project_dir), "一"])
    model = scripted_model([conftest.text_response("第二次")])

    cli.main(["run", "-p", str(project_dir), "-c", "二"])

    roles = [m["role"] for m in model.requests[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert len(sessions.list_ids(project_dir / ".harness" / "sessions")) == 1


def test_run_resume_flag_loads_named_session(project_dir, scripted_model):
    scripted_model([conftest.text_response("第一次")])
    cli.main(["run", "-p", str(project_dir), "一"])
    session_id = sessions.list_ids(project_dir / ".harness" / "sessions")[0]
    model = scripted_model([conftest.text_response("第二次")])

    cli.main(["run", "-p", str(project_dir), "-r", session_id, "二"])

    assert len(model.requests[0]["messages"]) == 4


def test_run_continue_reports_when_no_session_exists(project_dir, capsys):
    code = cli.main(["run", "-p", str(project_dir), "-c", "嗨"])

    assert code == 1
    assert "沒有任何 session" in capsys.readouterr().err


def test_run_model_flag_overrides_project_model(project_dir, scripted_model):
    model = scripted_model([conftest.text_response("好")])

    cli.main(["run", "-p", str(project_dir), "-m", "ollama/hermes3", "嗨"])

    assert model.requests[0]["model"] == "ollama/hermes3"


def test_run_reports_missing_project(tmp_path, capsys):
    code = cli.main(["run", "-p", str(tmp_path), "嗨"])

    assert code == 1
    assert "harness.yaml" in capsys.readouterr().err


def test_run_reports_unknown_agent(project_dir, capsys):
    code = cli.main(["run", "-p", str(project_dir), "-a", "ghost", "嗨"])

    assert code == 1
    assert "ghost" in capsys.readouterr().err


def test_run_warns_when_max_turns_reached(project_dir, scripted_model, capsys):
    (project_dir / "harness.yaml").write_text("maxTurns: 1\n", encoding="utf-8")
    scripted_model([conftest.tool_response(("word_count", {"text": "x"}))])

    cli.main(["run", "-p", str(project_dir), "嗨"])

    assert "輪數上限" in capsys.readouterr().out


def test_chat_runs_each_input_until_exit_command(
    project_dir, scripted_model, monkeypatch, capsys
):
    model = scripted_model(
        [conftest.text_response("第一次"), conftest.text_response("第二次")]
    )
    inputs = iter(["一", "", "二", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    code = cli.main(["chat", "-p", str(project_dir)])

    output = capsys.readouterr().out
    assert code == 0
    assert "第一次" in output and "第二次" in output
    assert len(model.requests) == 2
    assert len(model.requests[1]["messages"]) == 4


def test_run_reports_model_error_without_traceback(
    project_dir, monkeypatch, capsys
):
    async def broken_model(model, messages, tools, params):
        raise ConnectionError("連不上模型")
        yield

    monkeypatch.setattr(llm, "complete", broken_model)

    code = cli.main(["run", "-p", str(project_dir), "嗨"])

    assert code == 1
    assert "ConnectionError: 連不上模型" in capsys.readouterr().out


def test_chat_exits_cleanly_on_end_of_input(
    project_dir, scripted_model, monkeypatch
):
    scripted_model([])

    def raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    assert cli.main(["chat", "-p", str(project_dir)]) == 0
