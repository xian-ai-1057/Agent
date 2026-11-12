"""沙盒（sandbox toolset）的測試。

docker 指令換成假的腳本：記錄收到的參數，再用本機的 Python 在掛載的
資料夾裡執行程式。設定 HARNESS_DOCKER_TESTS=1 時，另外用真的 Docker 跑
一次（需要能取得 python:3.12-slim）。
"""

import asyncio
import json
import os
import pathlib
import subprocess
import sys

import pytest

import harness
from harness import cli
from harness import datatypes
from harness import sandbox
from harness import tools
from tests import conftest

FAKE_DOCKER = """#!{python}
import json, os, sys
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "kill":
    sys.exit(0)
args = sys.argv[1:]
os.chdir(args[args.index("-v") + 1].rsplit(":", 1)[0])
code = sys.stdin.read()
os.execv(sys.executable, [sys.executable, "-c", code])
"""


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    """建立假的 docker 指令。"""
    script = tmp_path / "fake-docker"
    script.write_text(FAKE_DOCKER.format(python=sys.executable))
    script.chmod(0o755)
    log = tmp_path / "docker.log"
    log.touch()
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))

    return _FakeDocker(script, log)


class _FakeDocker:
    """假 docker 指令的位置與它收到的呼叫。"""

    def __init__(self, script: pathlib.Path, log: pathlib.Path) -> None:
        self.script = script
        self.log = log

    def __str__(self) -> str:
        return str(self.script)

    def calls(self) -> list[list[str]]:
        lines = self.log.read_text().splitlines()
        return [json.loads(line) for line in lines]


@pytest.fixture
def box(tmp_path, fake_docker):
    return sandbox.Sandbox(
        workspace=tmp_path / "workspace",
        command=str(fake_docker),
        timeout_seconds=10,
    )


def _invoke(tool, code, context):
    return tools.invoke(tool, {"code": code}, context)


class TestDockerArgs:
    """docker run 的隔離設定。"""

    def test_container_is_locked_down(self, tmp_path):
        box = sandbox.Sandbox(workspace=tmp_path / "ws", image="my-image")

        args = box.docker_args("box-1")

        joined = " ".join(args)
        for flag in (
            "run --rm -i --name box-1",
            "--network none",
            "--read-only",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--memory 1g --memory-swap 1g",
            "--pids-limit 256",
            f"-v {tmp_path / 'ws'}:/workspace -w /workspace",
        ):
            assert flag in joined
        assert args[-3:] == ["my-image", "python", "-"]

    def test_volume_mount_when_harness_runs_in_a_container(self, tmp_path):
        box = sandbox.Sandbox(
            workspace=tmp_path / "ws",
            volume="harness-data",
            volume_subpath="ws",
        )

        args = box.docker_args("box-1")

        mount = args[args.index("--mount") + 1]
        assert mount == (
            "type=volume,src=harness-data,dst=/workspace,volume-nocopy,"
            "volume-subpath=ws"
        )
        assert "-v" not in args

    def test_runs_as_current_user(self, tmp_path):
        args = sandbox.Sandbox(workspace=tmp_path).docker_args("x")

        user = args[args.index("--user") + 1]
        assert user == f"{os.getuid()}:{os.getgid()}"

    def test_network_can_be_allowed(self, tmp_path):
        args = sandbox.Sandbox(workspace=tmp_path, network=True).docker_args(
            "x"
        )

        assert "--network" not in args


class TestRun:
    """執行程式與回報結果。"""

    async def test_runs_code_in_workspace(self, box, context):
        box.workspace.mkdir()
        (box.workspace / "data.csv").write_text("a\n1\n2\n")
        [tool] = box.tools()

        output = await _invoke(
            tool,
            "rows = open('data.csv').read().split()[1:]\n"
            "print(sum(map(int, rows)))\n"
            "open('out.txt', 'w').write('done')",
            context,
        )

        assert output == f"3\n\n{sandbox.PRODUCED_MARKER}workspace/out.txt"
        assert (box.workspace / "out.txt").read_text() == "done"

    async def test_lists_new_and_modified_files_only(self, box, context):
        box.workspace.mkdir()
        (box.workspace / "same.txt").write_text("不變")
        (box.workspace / "edit.txt").write_text("舊")
        (box.workspace / "charts").mkdir()
        [tool] = box.tools()

        output = await _invoke(
            tool,
            "import os, time\n"
            "open('edit.txt', 'w').write('新的內容')\n"
            "time.sleep(0.05)\n"
            "open('charts/a.png', 'wb').write(b'png')\n"
            "open('.hidden', 'w').write('x')\n"
            "os.makedirs('.cache', exist_ok=True)\n"
            "open('.cache/b.txt', 'w').write('x')\n"
            "print('ok')",
            context,
        )

        lines = output.split("\n")
        assert lines[:2] == ["ok", ""]
        assert lines[2:] == [
            f"{sandbox.PRODUCED_MARKER}workspace/charts/a.png",
            f"{sandbox.PRODUCED_MARKER}workspace/edit.txt",
        ]

    async def test_no_produced_block_when_nothing_changed(self, box, context):
        box.workspace.mkdir()
        (box.workspace / "data.csv").write_text("a\n1\n")
        [tool] = box.tools()

        output = await _invoke(
            tool, "print(open('data.csv').read()[0])", context
        )

        assert output == "a"

    async def test_failed_run_still_lists_partial_outputs(self, box, context):
        [tool] = box.tools()

        with pytest.raises(tools.ToolError) as raised:
            await _invoke(
                tool,
                "open('partial.csv', 'w').write('a')\nraise ValueError('壞')",
                context,
            )

        message = str(raised.value)
        assert "結束代碼 1" in message
        assert message.endswith(
            f"\n\n{sandbox.PRODUCED_MARKER}workspace/partial.csv"
        )

    async def test_files_outside_project_root_are_not_listed(
        self, box, tmp_path
    ):
        elsewhere = datatypes.RunContext(
            agent_name="test", workdir=tmp_path / "other", session_id="s1"
        )
        [tool] = box.tools()

        output = await _invoke(tool, "open('a.txt', 'w').write('x')", elsewhere)

        assert output == sandbox.NO_OUTPUT

    def test_produced_list_is_capped_newest_first(self, tmp_path):
        before = {"old.txt": (1, 1)}
        after = {f"f{i}.txt": (100 + i, 1) for i in range(25)}
        after["old.txt"] = (1, 1)

        text = sandbox.produced_lines(
            tmp_path / "workspace", tmp_path, before, after
        )

        lines = text.strip().split("\n")
        assert len(lines) == 21
        assert lines[0] == f"{sandbox.PRODUCED_MARKER}workspace/f24.txt"
        assert "old.txt" not in text
        assert "5 個檔案沒有列出" in lines[-1]

    def test_marker_matches_the_web_protocol(self):
        path = pathlib.Path(__file__).parents[2] / "src" / "web" / "protocol.ts"
        if not path.exists():
            pytest.skip("不在 repo 裡執行，找不到 protocol.ts")
        protocol = path.read_text(encoding="utf-8")

        assert f'PRODUCED_MARKER = "{sandbox.PRODUCED_MARKER}"' in protocol

    async def test_stderr_is_appended(self, box, context):
        [tool] = box.tools()

        output = await _invoke(
            tool,
            "import sys; print('ok'); print('注意', file=sys.stderr)",
            context,
        )

        assert output == "ok\n\n--- stderr ---\n注意"

    async def test_no_output_is_explained(self, box, context):
        [tool] = box.tools()

        assert await _invoke(tool, "x = 1", context) == sandbox.NO_OUTPUT

    async def test_failure_is_a_tool_error_with_traceback(self, box, context):
        [tool] = box.tools()

        with pytest.raises(tools.ToolError) as raised:
            await _invoke(tool, "raise ValueError('壞掉了')", context)

        assert "結束代碼 1" in str(raised.value)
        assert "ValueError: 壞掉了" in str(raised.value)

    async def test_timeout_stops_the_container(self, box, fake_docker, context):
        box.timeout_seconds = 0.5
        [tool] = box.tools()

        with pytest.raises(tools.ToolError, match="超過 0.5 秒"):
            await _invoke(tool, "import time; time.sleep(30)", context)

        run, kill = fake_docker.calls()
        assert kill == ["kill", run[run.index("--name") + 1]]

    async def test_cancelling_stops_the_container(self, box, fake_docker):
        task = asyncio.create_task(box.run("import time; time.sleep(30)"))
        await asyncio.sleep(0.5)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert [call[0] for call in fake_docker.calls()] == ["run", "kill"]

    async def test_long_output_keeps_head_and_tail(self, box):
        box.max_output_chars = 100

        result = await box.run("print('a' * 500 + 'END')")

        assert result.stdout.startswith("a" * 75)
        assert "中間省略" in result.stdout
        assert result.stdout.rstrip().endswith("END")

    async def test_missing_docker_is_explained(self, tmp_path):
        box = sandbox.Sandbox(workspace=tmp_path, command="no-such-docker")

        with pytest.raises(RuntimeError, match="需要安裝 Docker"):
            await box.run("print(1)")

    def test_description_mentions_limits_and_notes(self, tmp_path):
        box = sandbox.Sandbox(
            workspace=tmp_path, timeout_seconds=30, notes="已安裝 pandas。"
        )

        [tool] = box.tools()

        assert tool.name == "run_python"
        assert "沒有網路" in tool.description
        assert "30 秒" in tool.description
        assert tool.description.endswith("已安裝 pandas。")
        assert not tool.needs_approval


class TestConfig:
    """harness.yaml 的 sandbox 區塊。"""

    def test_true_uses_defaults(self, tmp_path):
        box = sandbox.from_config(tmp_path, True)

        assert box.image == sandbox.DEFAULT_IMAGE
        assert box.workspace == tmp_path.resolve() / "workspace"
        assert not box.network

    def test_settings_are_applied(self, tmp_path):
        box = sandbox.from_config(
            tmp_path,
            {
                "image": "analysis",
                "workspace": "data/shared",
                "timeoutSeconds": 120,
                "memory": "2g",
                "cpus": 2,
                "network": True,
                "command": "podman",
                "notes": "有 pandas",
            },
        )

        assert box.workspace == tmp_path.resolve() / "data/shared"
        assert (box.image, box.timeout_seconds, box.memory) == (
            "analysis",
            120,
            "2g",
        )
        assert (box.cpus, box.network, box.command) == (2, True, "podman")

    def test_missing_or_false_means_no_sandbox(self, tmp_path):
        assert sandbox.from_config(tmp_path, None) is None
        assert sandbox.from_config(tmp_path, False) is None

    @pytest.mark.parametrize(
        ("workspace", "message"),
        [
            (".", "不能是專案根目錄"),
            ("tools", "不能放在 tools 裡"),
            ("agents/data", "不能放在 agents 裡"),
            (".harness", "不能放在 .harness 裡"),
            ("../outside", "必須在專案資料夾裡"),
        ],
    )
    def test_workspace_cannot_expose_project_code(
        self, tmp_path, workspace, message
    ):
        with pytest.raises(ValueError, match=message):
            sandbox.from_config(tmp_path, {"workspace": workspace})

    def test_unknown_setting_is_an_error(self, tmp_path):
        with pytest.raises(ValueError, match="不認得的設定"):
            sandbox.from_config(tmp_path, {"timeout": 5})

    def test_deployment_environment_supplies_defaults(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HARNESS_SANDBOX_IMAGE", "harness-sandbox")
        monkeypatch.setenv("HARNESS_SANDBOX_NOTES", "已安裝 pandas。")
        monkeypatch.setenv("HARNESS_SANDBOX_VOLUME", "harness-data")

        box = sandbox.from_config(tmp_path, True)
        custom = sandbox.from_config(
            tmp_path, {"image": "mine", "workspace": "data/ws"}
        )

        assert box.image == "harness-sandbox"
        assert box.notes == "已安裝 pandas。"
        assert (box.volume, box.volume_subpath) == ("harness-data", "workspace")
        assert custom.image == "mine"
        assert custom.volume_subpath == "data/ws"

    def test_without_deployment_environment_mounts_the_folder(
        self, tmp_path, monkeypatch
    ):
        for name in (sandbox.IMAGE_ENV, sandbox.NOTES_ENV, sandbox.VOLUME_ENV):
            monkeypatch.delenv(name, raising=False)

        box = sandbox.from_config(tmp_path, True)

        assert box.image == sandbox.DEFAULT_IMAGE
        assert box.volume is None


class TestProject:
    """從專案設定掛上沙盒。"""

    @pytest.fixture
    def project_dir(self, tmp_path):
        root = tmp_path / "proj"
        assert cli.main(["new", str(root)]) == 0
        return root

    def _write_agent(self, root, frontmatter):
        (root / "agents" / "analyst.md").write_text(
            f"---\nname: analyst\n{frontmatter}\n---\n你是分析師\n",
            encoding="utf-8",
        )

    def _enable(self, root, text="sandbox: true\n"):
        config = root / "harness.yaml"
        config.write_text(config.read_text(encoding="utf-8") + text)

    def test_agent_listing_sandbox_gets_run_python(self, project_dir):
        self._enable(project_dir)
        self._write_agent(project_dir, "tools: [sandbox]")

        loaded = harness.load_project(project_dir)

        names = [item.name for item in loaded.agents["analyst"].tools]
        assert names == ["run_python"]
        assert loaded.sandbox.workspace == project_dir / "workspace"

    def test_agent_without_tools_field_does_not_get_sandbox(self, project_dir):
        self._enable(project_dir)
        self._write_agent(project_dir, "description: 分析")

        agent = harness.load_project(project_dir).agents["analyst"]

        assert "run_python" not in [item.name for item in agent.tools]

    def test_listing_sandbox_without_config_explains_setup(self, project_dir):
        self._write_agent(project_dir, "tools: [sandbox]")

        with pytest.raises(ValueError, match="加上 sandbox 設定"):
            harness.load_project(project_dir)

    def test_tools_file_named_sandbox_conflicts(self, project_dir):
        self._enable(project_dir)
        (project_dir / "tools" / "sandbox.py").write_text("")

        with pytest.raises(ValueError, match="保留給沙盒"):
            harness.load_project(project_dir)

    async def test_agent_runs_code_through_the_loop(
        self, project_dir, fake_docker, fake_model
    ):
        self._enable(project_dir, f"sandbox:\n  command: {fake_docker}\n")
        self._write_agent(project_dir, "tools: [sandbox]")
        agent = harness.load_project(project_dir).agents["analyst"]
        model = fake_model(
            [
                conftest.tool_response(
                    ("run_python", {"code": "print(6 * 7)"})
                ),
                conftest.text_response("答案是 42"),
            ]
        )

        result = await harness.run(agent, "6 乘 7？", model_fn=model)

        assert result.output == "答案是 42"
        assert model.requests[1]["messages"][-1]["content"] == "42"

    def test_cli_check_reports_success(self, project_dir, fake_docker, capsys):
        self._enable(project_dir, f"sandbox:\n  command: {fake_docker}\n")

        code = cli.main(["sandbox", "-p", str(project_dir)])

        output = capsys.readouterr().out
        assert code == 0
        assert f"掛載：{project_dir / 'workspace'} → /workspace" in output
        assert "✓ 沙盒可以正常使用" in output

    def test_cli_check_without_config(self, project_dir, capsys):
        code = cli.main(["sandbox", "-p", str(project_dir)])

        assert code == 1
        assert "沒有設定沙盒" in capsys.readouterr().out


def _docker_ready() -> bool:
    if os.environ.get("HARNESS_DOCKER_TESTS") != "1":
        return False
    try:
        return (
            subprocess.run(["docker", "info"], capture_output=True).returncode
            == 0
        )
    except FileNotFoundError:
        return False


@pytest.mark.skipif(
    not _docker_ready(),
    reason="設定 HARNESS_DOCKER_TESTS=1 且有 Docker 時才執行",
)
async def test_real_docker_isolation(tmp_path):
    box = sandbox.Sandbox(workspace=tmp_path / "ws", timeout_seconds=60)
    checks = (
        "import os, socket\n"
        "try:\n"
        "    socket.create_connection(('pypi.org', 443), timeout=3)\n"
        "    print('network: open')\n"
        "except OSError:\n"
        "    print('network: blocked')\n"
        "try:\n"
        "    open('/etc/harness-test', 'w')\n"
        "    print('rootfs: writable')\n"
        "except OSError:\n"
        "    print('rootfs: read-only')\n"
        "open('result.txt', 'w').write('ok')\n"
    )

    result = await box.run(checks)

    assert result.exit_code == 0, result.stderr
    assert result.stdout.split("\n")[:2] == [
        "network: blocked",
        "rootfs: read-only",
    ]
    assert (pathlib.Path(tmp_path) / "ws" / "result.txt").read_text() == "ok"
