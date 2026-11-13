"""網頁版把 Word、PowerPoint 轉成 PDF 預覽（harness.web.office）。

不需要真的 LibreOffice：用一個假的 soffice（本機模式）或假的沙盒執行
（沙盒模式）寫出 PDF，確認伺服器怎麼呼叫它、快取與錯誤處理。
"""

import os
import pathlib
import stat

import pytest
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer

from harness import project as project_lib
from harness import sandbox as sandbox_lib
from harness.web import office as office_lib
from harness.web import server as server_lib
from tests import test_web

PDF = b"%PDF-1.4\n% fake\n"


def fake_soffice(directory: pathlib.Path, body: str) -> pathlib.Path:
    """寫一個假的 soffice：記下被呼叫的參數，再執行 body。"""
    script = directory / "soffice"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{directory}/calls.txt"\n'
        # 參數的最後兩個是輸出資料夾與來源檔。
        "for last; do :; done\n"
        'out=$(dirname "$last")\n'
        f"{body}\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture
async def office(tmp_path, monkeypatch):
    """建立本機模式的伺服器，soffice 換成 body 決定的假程式。"""
    made = []

    async def make(body: str, extra: str = ""):
        root = test_web.make_project(tmp_path / f"p{len(made)}", extra)
        uploads = root / "workspace" / "uploads"
        uploads.mkdir(parents=True)
        (uploads / "報告.docx").write_bytes(b"PK\x03\x04 docx")
        (uploads / "簡報.pptx").write_bytes(b"PK\x03\x04 pptx")
        tools = tmp_path / f"bin{len(made)}"
        tools.mkdir()
        script = fake_soffice(tools, body)
        monkeypatch.setattr(
            office_lib,
            "_find_soffice",
            lambda allow: str(script) if allow else None,
        )
        loaded = project_lib.load_project(root)
        app = server_lib.create_app(loaded)
        client = TestClient(TestServer(app))
        await client.start_server()
        made.append((client, loaded))
        return client, root, tools

    yield make
    for client, loaded in made:
        await client.close()
        loaded.close()


# sh 的 printf：%% 是 %，\n 是換行，寫出的內容和 PDF 相同。
WRITE_PDF = "printf '%%PDF-1.4\\n%% fake\\n' > \"$out/source.pdf\""


async def get_pdf(client, path="workspace/uploads/簡報.pptx"):
    return await client.get("/api/files/pdf", params={"path": path})


async def test_office_files_become_cached_pdfs(office):
    client, root, tools = await office(WRITE_PDF)

    project = await (await client.get("/api/project")).json()
    first = await get_pdf(client)
    body = await first.read()
    second = await get_pdf(client)
    await second.read()
    calls = (tools / "calls.txt").read_text().splitlines()

    assert project["features"]["officePdf"] is True
    assert first.status == 200
    assert body == PDF
    assert first.headers["Content-Type"] == "application/pdf"
    assert first.headers["X-Content-Type-Options"] == "nosniff"
    assert "%E7%B0%A1%E5%A0%B1.pdf" in first.headers["Content-Disposition"]
    # 同樣的內容只轉換一次；設定檔放在暫存的地方。
    assert len(calls) == 1
    assert "--convert-to pdf" in calls[0]
    assert "-env:UserInstallation=file://" in calls[0]
    assert second.status == 200
    # 暫存資料夾轉完就刪掉，PDF 留在 .harness/previews/。
    previews = root / ".harness" / "previews"
    assert [p.suffix for p in previews.iterdir() if p.is_file()] == [".pdf"]
    assert list((previews / "jobs").iterdir()) == []


@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        (
            "echo 'Error: source file could not be loaded' >&2; exit 1",
            422,
            "無法轉成 PDF",
        ),
        # 沒有產生 PDF。
        ("exit 0", 422, "沒有產生檔案"),
        # 轉出來的是指向金鑰的捷徑：不跟著走。
        ('ln -s /etc/hosts "$out/source.pdf"', 422, "無法轉成 PDF"),
    ],
)
async def test_failed_conversions_are_explained(office, body, status, message):
    client, _, _ = await office(body)

    response = await get_pdf(client)

    assert response.status == status
    assert message in (await response.json())["error"]


async def test_only_word_and_powerpoint_are_converted(office):
    client, root, _ = await office(WRITE_PDF)
    (root / "workspace" / "uploads" / "表.xlsx").write_bytes(b"PK")

    sheet = await get_pdf(client, "workspace/uploads/表.xlsx")
    outside = await get_pdf(client, "../etc/passwd")
    secret = await get_pdf(client, ".harness/previews/x.pdf")

    assert sheet.status == 415
    assert outside.status == 403
    assert secret.status == 403


async def test_without_libreoffice_the_page_uses_its_own_preview(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(office_lib, "_find_soffice", lambda allow: None)
    root = test_web.make_project(tmp_path / "none")
    (root / "a.docx").write_bytes(b"PK")
    with project_lib.load_project(root) as loaded:
        client = TestClient(TestServer(server_lib.create_app(loaded)))
        await client.start_server()
        try:
            project = await (await client.get("/api/project")).json()
            response = await get_pdf(client, "a.docx")
        finally:
            await client.close()

    assert project["features"]["officePdf"] is False
    assert response.status == 501


async def test_conversion_runs_in_the_sandbox(tmp_path, monkeypatch):
    runs = []

    async def fake_execute(self, argv, stdin, workdir, timeout):
        runs.append((self.workspace, argv, workdir))
        (self.workspace / "source.pdf").write_bytes(PDF)
        return sandbox_lib.Result(0, "", "")

    async def never(*args, **kwargs):
        raise AssertionError("有沙盒時不能在伺服器上轉換")

    monkeypatch.setattr(sandbox_lib.Sandbox, "_execute", fake_execute)
    monkeypatch.setattr(office_lib.asyncio, "create_subprocess_exec", never)
    root = test_web.make_project(tmp_path / "box", "sandbox: true\n")
    (root / "a.pptx").write_bytes(b"PK pptx")
    converter = office_lib.OfficeConverter(
        root, project_lib.load_project(root).sandbox, allow_host=True
    )

    fd = os.open(root / "a.pptx", os.O_RDONLY)
    try:
        pdf = await converter.to_pdf(fd, 7, ".pptx")
    finally:
        os.close(fd)

    [(workspace, argv, workdir)] = runs
    assert pdf.read_bytes() == PDF
    assert workspace.parent == root / ".harness" / "previews" / "jobs"
    assert argv[:2] == ("bash", "-lc")
    assert "soffice -env:UserInstallation=file:///tmp/lo" in argv[2]
    assert "/workspace/source.pptx" in argv[2]
    assert workdir == sandbox_lib.CONTAINER_WORKDIR
    assert not workspace.exists()


async def test_a_sandbox_without_libreoffice_stops_offering_pdfs(
    tmp_path, monkeypatch
):
    async def missing(self, argv, stdin, workdir, timeout):
        return sandbox_lib.Result(127, "", "")

    monkeypatch.setattr(sandbox_lib.Sandbox, "_execute", missing)
    root = test_web.make_project(tmp_path / "old", "sandbox: true\n")
    (root / "a.docx").write_bytes(b"PK")
    converter = office_lib.OfficeConverter(
        root, project_lib.load_project(root).sandbox, allow_host=False
    )

    fd = os.open(root / "a.docx", os.O_RDONLY)
    try:
        with pytest.raises(office_lib.ConvertError) as raised:
            await converter.to_pdf(fd, 2, ".docx")
    finally:
        os.close(fd)

    assert raised.value.status == 501
    assert converter.available is False


def test_accounts_mode_never_converts_on_the_server(tmp_path):
    converter = office_lib.OfficeConverter(tmp_path, None, allow_host=False)

    assert converter.available is False


def test_cache_keeps_the_most_recent_pdfs(tmp_path):
    for index, name in enumerate(["old", "mid", "new"]):
        path = tmp_path / f"{name}.pdf"
        path.write_bytes(b"x" * 100)
        os.utime(path, (index, index))

    office_lib._prune(tmp_path, 250)  # noqa: SLF001

    assert sorted(p.name for p in tmp_path.iterdir()) == ["mid.pdf", "new.pdf"]
