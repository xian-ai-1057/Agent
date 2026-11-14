"""多個知識庫：設定、解析器、合併搜尋、群組開放、維護者與管理 API。"""

import pathlib
import zipfile

import aiohttp
import pytest

import harness
from harness import datatypes
from harness import knowledge as knowledge_lib
from harness import project as project_lib
from tests import conftest
from tests import test_web_accounts
from tests.test_web_accounts import AGENT
from tests.test_web_accounts import password_of
from tests.test_web_accounts import user_id

site = test_web_accounts.site
fast_hash = test_web_accounts.fast_hash

KNOWLEDGE_AGENT = AGENT.replace(
    "description: 測試用的助手\n",
    "description: 測試用的助手\nknowledge: true\n",
)

BASES = """
knowledgeBases:
  hr:
    name: 人資規章
    description: 請假與福利
  it:
    name: 資訊手冊
"""


def write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def add_documents(root: pathlib.Path) -> None:
    write(root / "knowledge" / "general.md", "特休公告：所有人都看得到。")
    write(root / "knowledge" / "hr" / "leave.md", "特休計算：滿半年三天。")
    write(root / "knowledge" / "it" / "vpn.md", "特休期間 VPN 一樣可以用。")


def project_with(tmp_path: pathlib.Path, config: str, agent: str = ""):
    write(tmp_path / "harness.yaml", "name: demo\n" + config)
    write(
        tmp_path / "agents" / "main.md",
        f"---\nname: main\n{agent}---\n你是助手。\n",
    )
    return tmp_path


def docx(path: pathlib.Path) -> pathlib.Path:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = (
        f'<w:document xmlns:w="{w}"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        "<w:r><w:t>請假規定</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>特休滿半年三天。</w:t></w:r></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>年資</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>天數</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", body)
    return path


def pptx(path: pathlib.Path) -> pathlib.Path:
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    with zipfile.ZipFile(path, "w") as archive:
        for number, text in ((2, "第二張"), (10, "第十張"), (1, "封面")):
            archive.writestr(
                f"ppt/slides/slide{number}.xml",
                f'<p:sld xmlns:p="p" xmlns:a="{a}"><a:p><a:r><a:t>{text}'
                "</a:t></a:r></a:p></p:sld>",
            )
    return path


def xlsx(path: pathlib.Path) -> pathlib.Path:
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rels = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{s}" xmlns:r="{r}"><sheets>'
            '<sheet name="費率" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="{rels}"><Relationship Id="rId1" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{s}"><si><t>方案</t></si><si><t>年費</t></si></sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{s}"><sheetData>'
            '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
            '<row><c t="inlineStr"><is><t>白金卡</t></is></c><c><v>1200</v>'
            "</c></row></sheetData></worksheet>",
        )
    return path


class TestParsers:
    def test_docx_keeps_headings_and_tables(self, tmp_path):
        [section] = knowledge_lib.parse_docx(docx(tmp_path / "a.docx"))
        chunks = knowledge_lib.split_text(section.text, "a.docx", (), 800)

        assert chunks[0].location == "請假規定"
        assert "特休滿半年三天。" in chunks[0].text
        assert "年資 | 天數" in chunks[0].text

    def test_pptx_is_one_section_per_slide_in_order(self, tmp_path):
        sections = knowledge_lib.parse_pptx(pptx(tmp_path / "a.pptx"))

        assert [s.text for s in sections] == ["封面", "第二張", "第十張"]
        assert sections[2].location == "第 3 張投影片"

    def test_xlsx_rows_and_sheet_names(self, tmp_path):
        [sheet] = knowledge_lib.parse_xlsx(xlsx(tmp_path / "a.xlsx"))

        assert sheet.location == "費率"
        assert sheet.text == "方案 | 年費\n白金卡 | 1200"

    async def test_parsers_can_be_replaced(self, tmp_path):
        write(tmp_path / "a.csv", "ignored")

        def csv_parser(path):
            return [knowledge_lib.Section("改用自己的解析器", "整份")]

        kb = harness.Knowledge(tmp_path, parsers={".csv": csv_parser})

        [hit] = await kb.search("解析器")

        assert hit.chunk.citation == "a.csv › 整份"

    async def test_office_files_are_searchable(self, tmp_path):
        docx(tmp_path / "rules.docx")
        xlsx(tmp_path / "fees.xlsx")
        kb = harness.Knowledge(tmp_path)

        hits = await kb.search("白金卡 年費")

        assert hits[0].chunk.citation == "fees.xlsx › 費率"


class TestSearchBases:
    def bases(self, tmp_path):
        found = []
        for base_id, name, text in (
            ("hr", "人資規章", "特休計算：滿半年三天。"),
            ("it", "資訊手冊", "VPN 設定：特休期間也能連線。"),
        ):
            write(tmp_path / base_id / "doc.md", text)
            found.append(
                knowledge_lib.KnowledgeBase(
                    base_id, name, harness.Knowledge(tmp_path / base_id)
                )
            )
        return found

    async def test_results_are_merged_and_cite_the_base(self, tmp_path):
        tool = knowledge_lib.search_tool(self.bases(tmp_path))

        context = datatypes.RunContext("main", tmp_path, "s")

        text = await tool.func(context, query="特休")

        assert "來源：人資規章 / doc.md" in text
        assert "來源：資訊手冊 / doc.md" in text
        assert "人資規章" in tool.description

    async def test_single_base_keeps_the_old_citation(self, tmp_path):
        [hr, _] = self.bases(tmp_path)

        [hit] = await knowledge_lib.search_bases([hr], "特休")

        assert hit.citation == "doc.md"

    async def test_same_reranker_sorts_by_rerank_score(self, tmp_path):
        bases = self.bases(tmp_path)

        async def rerank(query, documents):
            return [0.9 if "VPN" in doc else 0.1 for doc in documents]

        for base in bases:
            base.knowledge.reranker = "same"
            base.knowledge._rerank_fn = rerank

        hits = await knowledge_lib.search_bases(bases, "特休")

        assert [hit.base for hit in hits] == ["資訊手冊", "人資規章"]

    def test_narrow_tool_drops_closed_bases_and_labels(self, tmp_path):
        tool = knowledge_lib.search_tool(self.bases(tmp_path), ["hr", "it"])

        narrowed = knowledge_lib.narrow_tool(tool, {"it"}, {"it", "x"})
        gone = knowledge_lib.narrow_tool(tool, set(), None)

        assert [base.id for base in narrowed.bases] == ["it"]
        assert narrowed.labels == ("it",)
        assert gone is None
        assert knowledge_lib.narrow_tool(tool) is tool


class TestProjectConfig:
    def test_old_knowledge_folder_is_the_default_base(self, tmp_path):
        root = project_with(tmp_path, "", "knowledge: true\n")
        add_documents(root)

        project = project_lib.load_project(root)

        assert list(project.knowledge_bases) == ["default"]
        assert project.knowledge is project.knowledge_bases["default"].knowledge
        assert project.knowledge_bases["default"].name == "預設知識庫"
        assert project.agent_knowledge["main"].bases == ("default",)

    def test_sub_bases_are_excluded_from_the_default(self, tmp_path):
        root = project_with(
            tmp_path,
            "knowledge:\n  name: 共用文件\n  chunkSize: 300\n" + BASES,
            "knowledge: [hr, it]\n",
        )
        add_documents(root)

        project = project_lib.load_project(root)
        bases = project.knowledge_bases

        assert list(bases) == ["default", "hr", "it"]
        assert bases["default"].name == "共用文件"
        assert bases["default"].knowledge.files() == ["general.md"]
        assert bases["hr"].knowledge.files() == ["leave.md"]
        # 沒寫的檢索設定沿用 knowledge 區塊。
        assert bases["hr"].knowledge.chunk_size == 300
        assert bases["hr"].description == "請假與福利"
        assert project.agent_knowledge["main"].bases == ("hr", "it")

    def test_agent_knowledge_forms(self, tmp_path):
        root = project_with(
            tmp_path,
            BASES,
            "knowledge:\n  bases: [it]\n  labels: [x]\n  topK: 3\n",
        )
        add_documents(root)

        project = project_lib.load_project(root)
        [tool] = [
            item
            for item in project.agents["main"].tools
            if isinstance(item, knowledge_lib.KnowledgeTool)
        ]

        assert [base.id for base in tool.bases] == ["it"]
        assert tool.labels == ("x",) and tool.top_k == 3

    @pytest.mark.parametrize(
        ("config", "agent", "message"),
        [
            ("knowledgeBases:\n  Bad Id: {}\n", "", "識別碼"),
            ("knowledgeBases:\n  default: {}\n", "", "保留"),
            ("knowledgeBases:\n  hr: {foo: 1}\n", "", "不認得"),
            ("knowledgeBases:\n  none: {}\n", "", "找不到"),
            (BASES, "knowledge: [nope]\n", "不存在的知識庫"),
        ],
    )
    def test_bad_settings_are_explained(self, tmp_path, config, agent, message):
        root = project_with(tmp_path, config, agent)
        add_documents(root)

        with pytest.raises(ValueError, match=message):
            project_lib.load_project(root)


async def run_search(s, person, options=None) -> str:
    body = {"text": "特休"}
    if options is not None:
        body["options"] = options
    _, created = await person.api("POST", "/sessions", body)
    await person.wait_idle(created["id"])
    return s.model.requests[-1]["messages"][-1]["content"]


def search_script(times: int = 1):
    return [
        conftest.tool_response(("search_knowledge", {"query": "特休"})),
        conftest.text_response("好"),
    ] * times


class TestChat:
    async def test_groups_open_bases_and_chat_can_pick_them(self, site):
        s = await site(
            agent=KNOWLEDGE_AGENT,
            extra=BASES,
            prepare=add_documents,
            script=search_script(3),
        )
        boss = await s.login("boss", password_of("boss"))
        alice = await s.login("alice", password_of("alice"))
        await boss.api(
            "PATCH",
            "/admin/groups/everyone",
            {"knowledgeBases": ["default", "it"]},
        )

        _, project = await alice.api("GET", "/project")
        default_only = await run_search(s, alice)
        picked = await run_search(s, alice, {"knowledge": ["default", "it"]})
        status, error = await alice.api(
            "POST",
            "/sessions",
            {"text": "x", "options": {"knowledge": ["hr"]}},
        )

        assert [b["id"] for b in project["knowledgeBases"]] == [
            "default",
            "it",
        ]
        assert project["agents"][0]["knowledge"] == ["default"]
        assert "general.md" in default_only and "vpn.md" not in default_only
        assert "預設知識庫 / general.md" in picked
        assert "資訊手冊 / vpn.md" in picked
        assert "leave.md" not in picked
        assert status == 400 and "hr" in error["error"]

    async def test_closed_base_is_removed_from_the_agent(self, site):
        s = await site(
            agent=KNOWLEDGE_AGENT,
            extra=BASES,
            prepare=add_documents,
            script=[conftest.text_response("好")],
        )
        boss = await s.login("boss", password_of("boss"))
        alice = await s.login("alice", password_of("alice"))
        await boss.api(
            "PATCH", "/admin/groups/everyone", {"knowledgeBases": ["hr"]}
        )

        _, created = await alice.api("POST", "/sessions", {"text": "嗨"})
        await alice.wait_idle(created["id"])
        tools = [t["function"]["name"] for t in s.model.requests[-1]["tools"]]

        assert "search_knowledge" not in tools


class TestAdmin:
    async def upload(self, s, person, base, name, data, query=""):
        form = aiohttp.FormData()
        form.add_field("file", data, filename=name)
        response = await s.client.post(
            f"/api/knowledge/{base}/documents{query}",
            data=form,
            headers={**person.headers, "X-Harness-Upload": "1"},
        )
        return response.status, await response.json()

    async def test_admin_creates_base_uploads_and_searches(self, site):
        s = await site(agent=KNOWLEDGE_AGENT, prepare=add_documents)
        boss = await s.login("boss", password_of("boss"))

        status, _ = await boss.api(
            "POST",
            "/knowledge",
            {"id": "legal", "name": "法遵", "description": "法規"},
        )
        uploaded, saved = await self.upload(
            s, boss, "legal", "洗錢 防制.md", "洗錢防制：大額交易要申報。"
        )
        duplicate, _ = await self.upload(
            s, boss, "legal", "洗錢 防制.md", "again"
        )
        replaced, _ = await self.upload(
            s,
            boss,
            "legal",
            "new.md",
            "洗錢防制：更新後的版本。",
            "?replace=洗錢_防制.md",
        )
        _, docs = await boss.api("GET", "/knowledge/legal/documents")
        _, found = await boss.api(
            "POST", "/knowledge/legal/search", {"query": "洗錢"}
        )
        _, listed = await boss.api("GET", "/knowledge")
        config = (s.root / "harness.yaml").read_text("utf-8")

        assert status == 201 and uploaded == 201
        assert saved["files"][0]["path"] == "洗錢_防制.md"
        assert duplicate == 409 and replaced == 201
        assert [d["path"] for d in docs["documents"]] == ["洗錢_防制.md"]
        assert docs["documents"][0]["chunks"] == 1
        assert "更新後" in found["hits"][0]["text"]
        assert [b["id"] for b in listed["bases"]] == ["default", "legal"]
        assert "knowledgeBases:\n  legal:\n    name: 法遵" in config
        assert (s.root / "knowledge" / "legal" / "洗錢_防制.md").exists()

    async def test_admin_edits_settings_and_deletes_base(self, site):
        s = await site(extra=BASES, prepare=add_documents)
        boss = await s.login("boss", password_of("boss"))

        edited, _ = await boss.api(
            "PATCH", "/knowledge/hr", {"name": "人資", "chunkSize": 300}
        )
        bad, error = await boss.api("PATCH", "/knowledge/hr", {"chunkSize": 10})
        default, _ = await boss.api(
            "PATCH", "/knowledge/default", {"description": "共用"}
        )
        deleted, _ = await boss.api("DELETE", "/knowledge/it")
        keep_default, _ = await boss.api("DELETE", "/knowledge/default")
        _, listed = await boss.api("GET", "/knowledge")
        hr = next(b for b in listed["bases"] if b["id"] == "hr")

        assert edited == 200 and default == 200 and deleted == 200
        assert bad == 400 and "chunk_size" in error["error"]
        assert keep_default == 400
        assert hr["name"] == "人資" and hr["retrieval"]["chunkSize"] == 300
        assert [b["id"] for b in listed["bases"]] == ["default", "hr"]
        # 拿掉知識庫不刪文件。
        assert (s.root / "knowledge" / "it" / "vpn.md").exists()

    async def test_bases_used_by_agents_cannot_be_deleted(self, site):
        s = await site(
            extra=BASES,
            agent=AGENT.replace(
                "description: 測試用的助手\n",
                "description: 測試用的助手\nknowledge: [hr]\n",
            ),
            prepare=add_documents,
        )
        boss = await s.login("boss", password_of("boss"))

        status, error = await boss.api("DELETE", "/knowledge/hr")

        assert status == 400 and "main" in error["error"]

    async def test_maintainers_manage_only_their_bases(self, site):
        s = await site(extra=BASES, prepare=add_documents)
        boss = await s.login("boss", password_of("boss"))
        alice = await s.login("alice", password_of("alice"))
        bob = await s.login("bob", password_of("bob"))
        await boss.api(
            "PATCH",
            "/knowledge/hr",
            {"maintainers": [user_id(s.root, "alice")]},
        )

        _, alice_project = await alice.api("GET", "/project")
        _, bob_project = await bob.api("GET", "/project")
        _, listed = await alice.api("GET", "/knowledge")
        own, _ = await self.upload(s, alice, "hr", "a.md", "內容")
        other, _ = await self.upload(s, alice, "it", "a.md", "內容")
        removed, _ = await alice.api(
            "DELETE", "/knowledge/hr/documents?path=a.md"
        )
        settings, _ = await alice.api("PATCH", "/knowledge/hr", {"name": "x"})
        create, _ = await alice.api("POST", "/knowledge", {"id": "x"})
        bob_list, _ = await bob.api("GET", "/knowledge")

        assert alice_project["manageKnowledge"] is True
        assert bob_project["manageKnowledge"] is False
        assert [b["id"] for b in listed["bases"]] == ["hr"]
        assert "maintainers" not in listed["bases"][0]
        assert own == 201 and removed == 200
        assert other == 403 and settings == 403 and create == 403
        assert bob_list == 403

    async def test_uploads_stay_inside_the_base(self, site):
        s = await site(extra=BASES, prepare=add_documents)
        boss = await s.login("boss", password_of("boss"))

        escape, _ = await self.upload(
            s, boss, "default", "x.md", "內容", "?dir=../../agents"
        )
        other_base, _ = await self.upload(
            s, boss, "default", "x.md", "內容", "?dir=hr"
        )
        nested, saved = await self.upload(
            s, boss, "default", "x.md", "內容", "?dir=規章/2025"
        )
        missing, _ = await boss.api(
            "DELETE", "/knowledge/default/documents?path=../harness.yaml"
        )

        assert escape == 400
        assert other_base == 400
        assert nested == 201 and saved["files"][0]["path"] == "規章/2025/x.md"
        assert missing == 404
        assert (s.root / "harness.yaml").exists()
