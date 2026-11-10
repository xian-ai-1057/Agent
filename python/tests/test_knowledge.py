"""知識庫的測試，embedding 一律用假函式，不連網。"""

import os

import pytest

import harness
from harness import knowledge
from harness import scaffold
from tests import conftest

LEAVE = """\
---
labels: [hr]
---
# 請假規定

## 特休
到職滿六個月給三天特休，滿一年給七天。

## 病假
一年內病假未超過三十天，工資折半發給。
"""

PRODUCT = """\
# 產品規格

MaiPhone X 電池容量 5000 mAh，支援 65W 快充。
"""


@pytest.fixture
def kb_dir(tmp_path):
    root = tmp_path / "kb"
    (root / "hr").mkdir(parents=True)
    (root / "hr" / "leave.md").write_text(LEAVE, encoding="utf-8")
    (root / "product.md").write_text(PRODUCT, encoding="utf-8")
    return root


def _sources(hits):
    return [hit.chunk.citation for hit in hits]


class FakeEmbedder:
    """把文字轉成「是否包含某些詞」的向量，並記錄被呼叫的內容。"""

    def __init__(self, vocabulary):
        self.vocabulary = vocabulary
        self.calls = []

    async def __call__(self, texts):
        self.calls.append(list(texts))
        return [
            [1.0 if word in text else 0.0 for word in self.vocabulary] + [0.1]
            for text in texts
        ]


class TestTokenizeAndSplit:
    """斷詞與切片。"""

    def test_tokenize_splits_ascii_words_and_cjk_bigrams(self):
        assert knowledge.tokenize("MaiPhone 電池") == [
            "maiphone",
            "電",
            "池",
            "電池",
        ]

    def test_tokenize_keeps_dotted_and_dashed_words(self):
        assert knowledge.tokenize("v1.2 gpt-5") == ["v1.2", "gpt-5"]

    def test_split_text_records_heading_path_and_skips_bare_headings(self):
        chunks = knowledge.split_text(
            "# A\n\n## B\n內文\n\n### C\n細節", "doc.md", (), 800
        )

        assert [chunk.citation for chunk in chunks] == [
            "doc.md › A › B",
            "doc.md › A › B › C",
        ]

    def test_split_text_merges_paragraphs_up_to_chunk_size(self):
        text = "\n\n".join(["一" * 40] * 5)

        chunks = knowledge.split_text(text, "doc", (), 100)

        assert [len(chunk.text) for chunk in chunks] == [82, 82, 40]

    def test_split_text_hard_splits_long_paragraph_with_overlap(self):
        chunks = knowledge.split_text("字" * 250, "doc", (), 100)

        assert [len(chunk.text) for chunk in chunks] == [100, 100, 74]


class TestSearch:
    """搜尋與存取標籤。"""

    async def test_keyword_search_ranks_matching_chunk_first(self, kb_dir):
        kb = harness.Knowledge(kb_dir)

        hits = await kb.search("特休有幾天")

        assert _sources(hits)[0] == "hr/leave.md › 請假規定 › 特休"

    async def test_search_returns_nothing_without_matches(self, kb_dir):
        kb = harness.Knowledge(kb_dir)

        assert await kb.search("zzz") == []

    async def test_labelled_documents_need_matching_label(self, kb_dir):
        kb = harness.Knowledge(kb_dir)

        assert await kb.search("特休", labels=[]) == []
        assert await kb.search("特休", labels=["finance"]) == []
        assert await kb.search("特休", labels=["hr"])

    async def test_unlabelled_documents_are_visible_to_everyone(self, kb_dir):
        kb = harness.Knowledge(kb_dir)

        hits = await kb.search("電池", labels=[])

        assert _sources(hits) == ["product.md › 產品規格"]

    async def test_labels_yaml_assigns_labels_by_path(self, kb_dir):
        (kb_dir / "labels.yaml").write_text(
            '"product.md": [sales]\n', encoding="utf-8"
        )
        kb = harness.Knowledge(kb_dir)

        assert await kb.search("電池", labels=["hr"]) == []
        assert await kb.search("電池", labels=["sales"])

    async def test_index_is_rebuilt_when_files_change(self, kb_dir):
        kb = harness.Knowledge(kb_dir)
        assert await kb.search("保固") == []
        path = kb_dir / "warranty.md"
        path.write_text("保固兩年", encoding="utf-8")

        assert _sources(await kb.search("保固")) == ["warranty.md"]

    async def test_unsupported_files_are_reported(self, kb_dir):
        (kb_dir / "data.xlsx").write_bytes(b"x")
        (kb_dir / ".hidden.md").write_text("秘密", encoding="utf-8")
        kb = harness.Knowledge(kb_dir)

        stats = await kb.stats()

        assert stats["sources"] == 2
        assert stats["warnings"] == ["data.xlsx：不支援的格式，已略過"]

    async def test_add_text_is_searchable(self):
        kb = harness.Knowledge()
        kb.add_text("退貨請在七天內申請", source="FAQ#3", labels=["cs"])

        hits = await kb.search("退貨", labels=["cs"])

        assert _sources(hits) == ["FAQ#3"]

    async def test_embeddings_find_semantic_matches(self, kb_dir):
        # 「休假」與文件用字「特休」只有一個字相同，靠語意向量拉到第一。
        embedder = FakeEmbedder(["特休", "休假", "電池"])

        async def embed(texts):
            vectors = await embedder(texts)
            return [[v[0] + v[1], v[0] + v[1], v[2], v[3]] for v in vectors]

        kb = harness.Knowledge(kb_dir, embed_fn=embed)

        hits = await kb.search("休假")

        assert _sources(hits)[0] == "hr/leave.md › 請假規定 › 特休"
        assert (await kb.stats())["embedding"] is True

    async def test_embeddings_are_cached_by_content(self, kb_dir, tmp_path):
        embedder = FakeEmbedder(["特休"])
        cache = tmp_path / "cache"
        kb = harness.Knowledge(kb_dir, embed_fn=embedder, cache_dir=cache)
        await kb.stats()
        chunk_count = len(embedder.calls[0])

        (kb_dir / "new.md").write_text("新文件", encoding="utf-8")
        fresh = harness.Knowledge(kb_dir, embed_fn=embedder, cache_dir=cache)
        await fresh.stats()

        assert chunk_count == 3
        assert embedder.calls[1] == ["新文件"]


class TestSearchTool:
    """search_knowledge 工具。"""

    async def test_tool_returns_hits_with_citations(self, kb_dir, fake_model):
        kb = harness.Knowledge(kb_dir)
        agent = harness.Agent(name="a", tools=[kb.as_tool()])
        model = fake_model(
            [
                conftest.tool_response(("search_knowledge", {"query": "電池"})),
                conftest.text_response("5000 mAh"),
            ]
        )

        await harness.run(agent, "電池多大？", model_fn=model)

        content = model.requests[1]["messages"][-1]["content"]
        assert content.startswith("[1] 來源：product.md › 產品規格\n")
        assert "5000 mAh" in content

    async def test_tool_reports_when_nothing_found(self, kb_dir, context):
        tool = harness.Knowledge(kb_dir).as_tool(labels=[])

        output = await harness.tools.invoke(tool, {"query": "特休"}, context)

        assert output == "知識庫裡沒有找到相關的內容。"

    async def test_label_filter_uses_run_context(self, kb_dir, context):
        kb = harness.Knowledge(
            kb_dir, label_filter=lambda ctx: ctx.deps["departments"]
        )
        tool = kb.as_tool()

        context.deps = {"departments": ["sales"]}
        denied = await harness.tools.invoke(tool, {"query": "特休"}, context)
        context.deps = {"departments": ["hr"]}
        allowed = await harness.tools.invoke(tool, {"query": "特休"}, context)

        assert "沒有找到" in denied
        assert "特休" in allowed

    async def test_agent_labels_and_filter_are_intersected(
        self, kb_dir, context
    ):
        kb = harness.Knowledge(kb_dir, label_filter=lambda ctx: ["hr", "x"])
        tool = kb.as_tool(labels=["x"])

        output = await harness.tools.invoke(tool, {"query": "特休"}, context)

        assert "沒有找到" in output


class TestKnowledgeInProject:
    """從專案設定掛上知識庫。"""

    @pytest.fixture
    def project_dir(self, tmp_path, kb_dir):
        root = tmp_path / "proj"
        scaffold.create_project(root)
        os.rename(kb_dir, root / "knowledge")
        return root

    def _write_agent(self, root, frontmatter):
        (root / "agents" / "faq.md").write_text(
            f"---\nname: faq\n{frontmatter}\n---\n你是助手\n", encoding="utf-8"
        )

    def _tool(self, root):
        agent = harness.load_project(root).agents["faq"]
        return {item.name: item for item in agent.tools}["search_knowledge"]

    def test_knowledge_folder_is_detected(self, project_dir):
        loaded = harness.load_project(project_dir)

        assert loaded.knowledge is not None
        assert loaded.knowledge.cache_dir == project_dir / ".harness/knowledge"

    def test_agent_needs_knowledge_key_to_search(self, project_dir):
        agent = harness.load_project(project_dir).agents["main"]

        assert "search_knowledge" not in [item.name for item in agent.tools]

    async def test_agent_knowledge_labels_restrict_search(
        self, project_dir, context
    ):
        self._write_agent(project_dir, "knowledge: {labels: [sales]}")

        output = await harness.tools.invoke(
            self._tool(project_dir), {"query": "特休"}, context
        )

        assert "沒有找到" in output

    async def test_agent_knowledge_true_searches_everything(
        self, project_dir, context
    ):
        self._write_agent(project_dir, "knowledge: true")

        output = await harness.tools.invoke(
            self._tool(project_dir), {"query": "特休"}, context
        )

        assert "hr/leave.md" in output

    def test_knowledge_without_folder_is_an_error(self, tmp_path):
        root = tmp_path / "empty"
        scaffold.create_project(root)
        self._write_agent(root, "knowledge: true")

        with pytest.raises(ValueError, match="沒有知識庫"):
            harness.load_project(root)

    def test_harness_yaml_settings_are_applied(self, project_dir):
        config = project_dir / "harness.yaml"
        config.write_text(
            config.read_text(encoding="utf-8")
            + "knowledge:\n  embeddingModel: openai/emb\n  chunkSize: 300\n",
            encoding="utf-8",
        )

        kb = harness.load_project(project_dir).knowledge

        assert kb.embedding_model == "openai/emb"
        assert kb.chunk_size == 300
