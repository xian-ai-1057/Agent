"""內建檔案工具的測試。"""

import pytest

from harness import tools
from harness.builtin import files


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
