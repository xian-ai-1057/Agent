"""內建檔案工具的測試。"""

import os
import threading
import time

import pytest

from harness import datatypes
from harness import tools
from harness.builtin import files
from harness.builtin import paths
from harness.builtin import search


async def test_write_then_read_round_trips_content(context):
    await tools.invoke(
        files.write_file, {"path": "out/a.md", "content": "報告"}, context
    )

    output = await tools.invoke(files.read_file, {"path": "out/a.md"}, context)

    assert output == "報告"


async def test_list_dir_marks_directories(context):
    (context.workdir / "sub").mkdir()
    (context.workdir / "a.txt").write_text("x", encoding="utf-8")

    output = await tools.invoke(files.list_dir, {}, context)

    assert output == '["a.txt", "sub/"]'


async def test_read_file_notes_truncation(context, monkeypatch):
    monkeypatch.setattr(files, "MAX_READ_CHARS", 5)
    (context.workdir / "big.txt").write_text("0123456789", encoding="utf-8")

    output = await tools.invoke(files.read_file, {"path": "big.txt"}, context)

    assert output.startswith("01234\n")
    assert "還有 5 個字元沒有顯示" in output


@pytest.mark.parametrize("path", ["../secret.txt", "/etc/passwd", "a/../../b"])
def test_resolve_path_rejects_paths_outside_workdir(context, path):
    with pytest.raises(ValueError, match="超出專案資料夾"):
        files.resolve_path(context, path)


def test_write_file_requires_approval():
    assert files.write_file.needs_approval
    assert not files.read_file.needs_approval


class TestNoFollow:
    """路徑上的捷徑（symlink）一律不跟著走（見 builtin.paths）。"""

    @pytest.fixture
    def tree(self, context):
        root = context.workdir
        (root / "real").mkdir()
        (root / "real" / "a.txt").write_text("inside", encoding="utf-8")
        (root / "link").symlink_to(root / "real")
        (root / "file-link.txt").symlink_to(root / "real" / "a.txt")
        return root

    @pytest.mark.parametrize("path", ["link/a.txt", "file-link.txt"])
    async def test_read_refuses_links(self, context, tree, path):
        with pytest.raises(ValueError, match="捷徑"):
            await tools.invoke(files.read_file, {"path": path}, context)

    async def test_write_refuses_links_and_creates_real_folders(
        self, context, tree
    ):
        with pytest.raises(ValueError, match="捷徑"):
            await tools.invoke(
                files.write_file,
                {"path": "link/b.txt", "content": "x"},
                context,
            )
        await tools.invoke(
            files.write_file,
            {"path": "new/deep/c.txt", "content": "c"},
            context,
        )

        assert not (tree / "real" / "b.txt").exists()
        assert (tree / "new" / "deep" / "c.txt").read_text() == "c"

    async def test_edit_refuses_a_linked_file(self, context, tree):
        with pytest.raises(ValueError, match="捷徑"):
            await tools.invoke(
                files.edit_file,
                {
                    "path": "file-link.txt",
                    "old_text": "inside",
                    "new_text": "changed",
                },
                context,
            )

        assert (tree / "real" / "a.txt").read_text() == "inside"

    async def test_listing_and_search_skip_links(self, context, tree):
        listed = await tools.invoke(files.list_dir, {}, context)
        found = await tools.invoke(
            search.find_files, {"pattern": "*.txt"}, context
        )
        searched = await tools.invoke(
            search.search_files, {"pattern": "inside"}, context
        )

        assert listed == '["real/"]'
        assert found == "real/a.txt"
        assert searched == "real/a.txt:1: inside"

    async def test_hidden_names_and_dotfiles(self, tmp_path):
        (tmp_path / "private").mkdir()
        (tmp_path / "private" / "x.txt").write_text("x", encoding="utf-8")
        (tmp_path / ".env").write_text("KEY=1", encoding="utf-8")
        (tmp_path / "ok.txt").write_text("ok", encoding="utf-8")
        context = datatypes.RunContext(
            "a", tmp_path, "s", hidden=("private",), hide_dotfiles=True
        )

        listed = await tools.invoke(files.list_dir, {}, context)
        found = await tools.invoke(search.find_files, {"pattern": "*"}, context)

        assert listed == '["ok.txt"]'
        assert found == "ok.txt"
        for path in ("private/x.txt", "a/../private/x.txt", ".env"):
            with pytest.raises(ValueError, match="不能存取"):
                await tools.invoke(files.read_file, {"path": path}, context)

    def test_swapping_a_folder_for_a_link_never_leaks(self, tmp_path):
        """沙盒一直把資料夾換成指向金鑰的捷徑：讀到的永遠不是金鑰。"""
        secrets = tmp_path / ".harness" / "secrets"
        secrets.mkdir(parents=True)
        (secrets / "env.json").write_text("SECRET", encoding="utf-8")
        folder = tmp_path / "private" / "alice"
        folder.mkdir(parents=True)
        real = folder / "d"
        real.mkdir()
        (real / "env.json").write_text("benign", encoding="utf-8")
        context = datatypes.RunContext("a", folder, "s")
        stop = threading.Event()

        def swap():
            while not stop.is_set():
                os.rename(real, folder / "hold")
                os.symlink("../../.harness/secrets", real)
                os.unlink(real)
                os.rename(folder / "hold", real)

        worker = threading.Thread(target=swap)
        worker.start()
        leaks = reads = 0
        deadline = time.monotonic() + 1
        try:
            while time.monotonic() < deadline:
                try:
                    text = paths.read_text(context, "d/env.json")
                except (OSError, ValueError):
                    continue
                reads += 1
                leaks += text == "SECRET"
        finally:
            stop.set()
            worker.join()

        assert reads > 0
        assert leaks == 0
