"""內建 toolsets（files 的搜尋與修改、shell、plan、web）的測試。"""

import pytest
from aiohttp import web as aiohttp_web

import harness
from harness import builtin
from harness import scaffold
from harness import tools
from harness.builtin import files
from harness.builtin import plan
from harness.builtin import search
from harness.builtin import shell
from harness.builtin import web


def _write(context, files_by_path):
    """在專案資料夾裡建立檔案。"""
    for path, content in files_by_path.items():
        target = context.workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))


async def _call(target, arguments, context):
    return await tools.invoke(target, arguments, context)


class TestToolsets:
    """內建 toolsets 的組成與載入方式。"""

    def test_builtin_toolsets(self):
        assert list(builtin.TOOLSETS) == ["files", "shell", "plan", "web"]

    def test_tools_that_run_commands_or_reach_network_need_approval(self):
        assert shell.run_command.needs_approval
        assert web.web_fetch.needs_approval
        assert files.edit_file.needs_approval
        assert not plan.update_plan_tool.needs_approval
        assert not search.find_files.needs_approval

    def test_agent_must_list_builtin_toolsets(self, tmp_path):
        root = tmp_path / "demo"
        scaffold.create_project(root)
        (root / "agents" / "dev.md").write_text(
            "---\ntools: [shell, plan]\n---\n你是助手\n", encoding="utf-8"
        )

        loaded = harness.load_project(root)

        assert [item.name for item in loaded.agents["dev"].tools] == [
            "run_command",
            "update_plan",
        ]
        assert [item.name for item in loaded.agents["main"].tools] == [
            "word_count"
        ]

    def test_project_toolset_with_builtin_name_wins(self, tmp_path):
        root = tmp_path / "demo"
        scaffold.create_project(root)
        (root / "tools" / "web.py").write_text(
            "import harness\n\n\n@harness.tool\ndef ping() -> str:\n"
            '    """回應 pong。"""\n    return "pong"\n',
            encoding="utf-8",
        )
        (root / "agents" / "open.md").write_text(
            "---\nname: open\n---\n你是助手\n", encoding="utf-8"
        )

        loaded = harness.load_project(root)

        # 專案自己的 web.py 仍是專案工具，沒寫 tools 欄位的 agent 拿得到。
        assert "ping" in loaded.tools
        assert "ping" in [item.name for item in loaded.agents["open"].tools]
        assert [item.name for item in loaded.toolsets["web"]] == ["ping"]


class TestReadFileRange:
    """read_file 的 offset 與 limit。"""

    async def test_reads_selected_lines_with_range_note(self, context):
        _write(context, {"a.txt": "一\n二\n三\n四\n"})

        output = await _call(
            files.read_file, {"path": "a.txt", "offset": 2, "limit": 2}, context
        )

        assert output == "二\n三\n[第 2–3 行，共 4 行]"

    async def test_whole_range_has_no_note(self, context):
        _write(context, {"a.txt": "一\n二\n三\n四\n"})

        output = await _call(
            files.read_file, {"path": "a.txt", "offset": 1, "limit": 9}, context
        )

        assert output == "一\n二\n三\n四"

    async def test_offset_past_end_is_an_error(self, context):
        _write(context, {"a.txt": "一\n二\n三\n四\n"})

        with pytest.raises(tools.ToolError, match="檔案只有 4 行"):
            await _call(
                files.read_file, {"path": "a.txt", "offset": 9}, context
            )


class TestEditFile:
    """edit_file 的精準取代。"""

    async def test_replaces_unique_text(self, context):
        _write(context, {"a.py": "x = 1\ny = 2\n"})

        output = await _call(
            files.edit_file,
            {"path": "a.py", "old_text": "y = 2", "new_text": "y = 3"},
            context,
        )

        assert output == "已修改 a.py（取代 1 處）"
        assert (context.workdir / "a.py").read_text() == "x = 1\ny = 3\n"

    async def test_ambiguous_text_is_an_error(self, context):
        _write(context, {"a.py": "a\na\n"})

        with pytest.raises(tools.ToolError, match="出現 2 次"):
            await _call(
                files.edit_file,
                {"path": "a.py", "old_text": "a", "new_text": "b"},
                context,
            )

    async def test_replace_all(self, context):
        _write(context, {"a.py": "a\na\n"})

        output = await _call(
            files.edit_file,
            {
                "path": "a.py",
                "old_text": "a",
                "new_text": "b",
                "replace_all": True,
            },
            context,
        )

        assert output == "已修改 a.py（取代 2 處）"
        assert (context.workdir / "a.py").read_text() == "b\nb\n"

    async def test_keeps_windows_line_endings(self, context):
        _write(context, {"a.txt": "a\r\nb\r\n"})

        await _call(
            files.edit_file,
            {"path": "a.txt", "old_text": "b", "new_text": "c"},
            context,
        )

        assert (context.workdir / "a.txt").read_bytes() == b"a\r\nc\r\n"

    async def test_missing_text_is_an_error(self, context):
        _write(context, {"a.py": "a\n"})

        with pytest.raises(tools.ToolError, match="找不到 old_text"):
            await _call(
                files.edit_file,
                {"path": "a.py", "old_text": "z", "new_text": "b"},
                context,
            )


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("*.py", "src/deep/a.py", True),
        ("*.py", "a.pyc", False),
        ("src/*.ts", "src/a.ts", True),
        ("src/*.ts", "src/x/a.ts", False),
        ("src/**/*.ts", "src/a.ts", True),
        ("src/**/*.ts", "src/x/y/a.ts", True),
        ("test_?.{py,md}", "t/test_1.md", True),
        ("test_?.{py,md}", "test_12.py", False),
    ],
)
def test_glob_matcher(pattern, path, expected):
    assert search.glob_matcher(pattern)(path) is expected


class TestFindFiles:
    """find_files。"""

    async def test_returns_paths_from_root_and_skips_ignored(self, context):
        _write(
            context,
            {
                "src/a.ts": "",
                "src/b/c.ts": "",
                "src/d.py": "",
                "node_modules/x/index.ts": "",
            },
        )

        output = await _call(search.find_files, {"pattern": "*.ts"}, context)

        assert output == "src/a.ts\nsrc/b/c.ts"

    async def test_can_start_from_subfolder(self, context):
        _write(context, {"src/b/c.ts": "", "other/c.ts": ""})

        output = await _call(
            search.find_files, {"pattern": "*.ts", "path": "src"}, context
        )

        assert output == "src/b/c.ts"

    async def test_no_match(self, context):
        output = await _call(search.find_files, {"pattern": "*.go"}, context)

        assert output == "沒有符合 *.go 的檔案"


class TestSearchFiles:
    """search_files。"""

    async def test_returns_path_line_and_text(self, context):
        _write(
            context,
            {"a.py": "def load():\n    pass\n", "b.md": "def load in docs\n"},
        )

        output = await _call(
            search.search_files,
            {"pattern": r"def\s+load", "glob": "*.py"},
            context,
        )

        assert output == "a.py:1: def load():"

    async def test_ignore_case_and_skip_binary(self, context):
        _write(context, {"a.txt": "Hello\n", "b.bin": "hello\0"})

        output = await _call(
            search.search_files,
            {"pattern": "hello", "ignore_case": True},
            context,
        )

        assert output == "a.txt:1: Hello"

    async def test_bad_regex_is_reported(self, context):
        with pytest.raises(tools.ToolError, match="正規表示式有誤"):
            await _call(search.search_files, {"pattern": "("}, context)


class TestRunCommand:
    """run_command。"""

    async def test_returns_exit_code_and_streams(self, context):
        output = await _call(
            shell.run_command,
            {"command": "echo hello && echo oops 1>&2 && exit 3"},
            context,
        )

        assert output == "結束代碼：3\n[stdout]\nhello\n[stderr]\noops"

    async def test_runs_in_subfolder(self, context):
        _write(context, {"sub/x.txt": ""})

        output = await _call(
            shell.run_command, {"command": "ls", "cwd": "sub"}, context
        )

        assert output == "結束代碼：0\n[stdout]\nx.txt"

    async def test_timeout_kills_command(self, context):
        output = await _call(
            shell.run_command, {"command": "sleep 5", "timeout": 1}, context
        )

        assert "指令執行超過 1 秒，已終止" in output

    async def test_cannot_run_outside_project(self, context):
        with pytest.raises(ValueError, match="超出專案資料夾"):
            await _call(
                shell.run_command, {"command": "ls", "cwd": ".."}, context
            )

    def test_clip_middle_keeps_head_and_tail(self):
        clipped = shell.clip_middle("a" * 10 + "b" * 10, 10)

        assert clipped == "aaaaa\n…[中間省略 10 個字元]…\nbbbbb"


class TestUpdatePlan:
    """update_plan。"""

    async def test_returns_plan_with_progress(self, context):
        output = await _call(
            plan.update_plan_tool,
            {
                "plan": [
                    {"step": "讀需求", "status": "completed"},
                    {"step": "實作", "status": "in_progress"},
                    {"step": "測試", "status": "pending"},
                ]
            },
            context,
        )

        assert output == (
            "計畫已更新（已完成 1/3）：\n[x] 讀需求\n[>] 實作\n[ ] 測試"
        )

    async def test_two_steps_in_progress_is_an_error(self, context):
        steps = [
            {"step": "a", "status": "in_progress"},
            {"step": "b", "status": "in_progress"},
        ]

        with pytest.raises(tools.ToolError, match="只能有一個步驟"):
            await _call(plan.update_plan_tool, {"plan": steps}, context)

    async def test_unknown_status_is_an_error(self, context):
        steps = [{"step": "a", "status": "done"}]

        with pytest.raises(tools.ToolError, match="status 必須是"):
            await _call(plan.update_plan_tool, {"plan": steps}, context)


def test_html_to_text():
    source = """<html><head><title>T</title><style>p{}</style></head>
      <body><h1>標題</h1><script>alert(1)</script>
      <p>第一段&amp;更多&nbsp;文字</p>
      <ul><li>甲</li><li>乙</li></ul>
      <a href="/docs">文件</a></body></html>"""

    assert web.html_to_text(source, "https://example.com/a/") == (
        "# 標題\n\n第一段&更多 文字\n\n- 甲\n\n- 乙\n\n"
        "[文件](https://example.com/docs)"
    )


class TestWebFetch:
    """web_fetch（對本機的測試伺服器）。"""

    @pytest.fixture
    async def base_url(self):
        async def page(request):
            return aiohttp_web.Response(
                text="<title>測試頁</title><p>你好</p>",
                content_type="text/html",
            )

        async def data(request):
            return aiohttp_web.json_response({"a": 1})

        async def image(request):
            return aiohttp_web.Response(body=b"png", content_type="image/png")

        app = aiohttp_web.Application()
        app.router.add_get("/page", page)
        app.router.add_get("/data", data)
        app.router.add_get("/image", image)
        runner = aiohttp_web.AppRunner(app)
        await runner.setup()
        site = aiohttp_web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
        await runner.cleanup()

    @pytest.fixture(autouse=True)
    def no_proxy(self, monkeypatch):
        # 測試只連本機，不要經過環境設定的代理伺服器。
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            monkeypatch.delenv(name, raising=False)

    async def test_html_becomes_text_with_title(self, base_url, context):
        output = await _call(
            web.web_fetch, {"url": f"{base_url}/page"}, context
        )

        assert output == f"網址：{base_url}/page\n標題：測試頁\n\n你好"

    async def test_json_is_returned_as_is(self, base_url, context):
        output = await _call(
            web.web_fetch, {"url": f"{base_url}/data"}, context
        )

        assert output == f'網址：{base_url}/data\n\n{{"a": 1}}'

    @pytest.mark.parametrize(
        ("path", "message"),
        [("/missing", "伺服器回應 404"), ("/image", "不支援的內容類型")],
    )
    async def test_errors_are_reported(self, base_url, context, path, message):
        with pytest.raises(tools.ToolError, match=message):
            await _call(web.web_fetch, {"url": f"{base_url}{path}"}, context)

    async def test_only_http_urls(self, context):
        with pytest.raises(tools.ToolError, match="只支援 http 與 https"):
            await _call(web.web_fetch, {"url": "file:///etc/passwd"}, context)
