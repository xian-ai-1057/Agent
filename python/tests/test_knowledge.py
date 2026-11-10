"""知識庫的測試，embedding 一律用假函式，不連網。"""

import json
import os

import pytest

import harness
from harness import knowledge
from harness import llm
from harness import scaffold
from harness import vectorstore
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


class TestVectorStore:
    """embedding 存進向量資料庫。"""

    async def test_vectors_are_stored_in_sqlite_under_cache_dir(
        self, kb_dir, tmp_path
    ):
        cache = tmp_path / "cache"
        kb = harness.Knowledge(
            kb_dir, embed_fn=FakeEmbedder(["特休"]), cache_dir=cache
        )
        await kb.stats()

        reopened = vectorstore.SqliteVectorStore(cache / knowledge.VECTOR_FILE)
        assert len(reopened) == 3

    async def test_removed_files_are_pruned_from_store(self, kb_dir):
        store = vectorstore.SqliteVectorStore()
        kb = harness.Knowledge(
            kb_dir, embed_fn=FakeEmbedder(["特休"]), vector_store=store
        )
        await kb.stats()

        (kb_dir / "product.md").unlink()
        await kb.stats()

        assert len(store) == 2

    async def test_legacy_json_cache_is_imported_and_removed(
        self, kb_dir, tmp_path
    ):
        cache = tmp_path / "cache"
        cache.mkdir()
        text = "MaiPhone X 電池容量 5000 mAh，支援 65W 快充。"
        legacy = {knowledge._cache_key("", f"# 產品規格\n\n{text}"): [9, 9]}
        (cache / knowledge.LEGACY_CACHE_FILE).write_text(
            json.dumps(legacy), encoding="utf-8"
        )
        embedder = FakeEmbedder(["特休"])
        kb = harness.Knowledge(kb_dir, embed_fn=embedder, cache_dir=cache)

        await kb.stats()

        assert len(embedder.calls[0]) == 2
        assert not (cache / knowledge.LEGACY_CACHE_FILE).exists()

    async def test_wrong_number_of_vectors_is_an_error(self, kb_dir):
        async def broken(texts):
            return [[1.0]]

        kb = harness.Knowledge(kb_dir, embed_fn=broken)

        with pytest.raises(ValueError, match="回傳了 1 個向量"):
            await kb.stats()


class TestSimilarityThreshold:
    """相似度門檻。"""

    def _kb(self, kb_dir, threshold):
        return harness.Knowledge(
            kb_dir,
            embed_fn=FakeEmbedder(["特休", "病假", "電池"]),
            min_similarity=threshold,
        )

    async def test_dissimilar_keyword_matches_are_dropped(self, kb_dir):
        # 「假」也出現在特休那段的標題裡，關鍵字會找到它，但語意不相似。
        without = await self._kb(kb_dir, None).search("病假")
        hits = await self._kb(kb_dir, 0.5).search("病假")

        assert len(without) == 3
        assert _sources(hits) == ["hr/leave.md › 請假規定 › 病假"]
        assert hits[0].similarity == pytest.approx(1.0)

    async def test_nothing_similar_enough_returns_nothing(self, kb_dir):
        hits = await self._kb(kb_dir, 0.99).search("保固")

        assert hits == []

    async def test_keyword_only_candidates_get_similarity_checked(
        self, kb_dir, monkeypatch
    ):
        # 語意檢索只取前兩名，「特休」那段只靠關鍵字進入候選，
        # 門檻判斷前要另外查它的相似度。
        monkeypatch.setattr(knowledge, "_VECTOR_POOL", 0)
        kb = harness.Knowledge(
            kb_dir,
            embed_fn=FakeEmbedder(["特休", "電池"]),
            min_similarity=0.005,
        )

        hits = await kb.search("電池 規定", top_k=3)

        by_source = {hit.chunk.citation: hit.similarity for hit in hits}
        assert by_source["hr/leave.md › 請假規定 › 特休"] == pytest.approx(
            0.0099, abs=1e-3
        )

    def test_threshold_needs_embedding(self, kb_dir):
        with pytest.raises(ValueError, match="embedding_model"):
            harness.Knowledge(kb_dir, min_similarity=0.5)

    def test_threshold_must_be_a_cosine(self, kb_dir):
        with pytest.raises(ValueError, match="-1 與 1"):
            harness.Knowledge(
                kb_dir, embed_fn=FakeEmbedder([]), min_similarity=2
            )


class TestReranker:
    """用 reranker 重新排序。"""

    @staticmethod
    def _reranker(favourite):
        calls = []

        async def rerank(query, documents):
            calls.append((query, list(documents)))
            return [0.9 if favourite in doc else 0.1 for doc in documents]

        rerank.calls = calls
        return rerank

    async def test_reranker_reorders_candidates(self, kb_dir):
        rerank = self._reranker("工資折半")
        kb = harness.Knowledge(kb_dir, rerank_fn=rerank)

        hits = await kb.search("特休 假", top_k=1)

        assert _sources(hits) == ["hr/leave.md › 請假規定 › 病假"]
        assert hits[0].rerank_score == pytest.approx(0.9)
        assert hits[0].score == pytest.approx(0.9)
        query, documents = rerank.calls[0]
        assert query == "特休 假"
        assert documents[0].startswith("hr/leave.md › 請假規定 › 特休\n")

    async def test_reranker_sees_at_most_rerank_candidates(self, kb_dir):
        rerank = self._reranker("工資折半")
        kb = harness.Knowledge(kb_dir, rerank_fn=rerank, rerank_candidates=2)

        await kb.search("假 電池", top_k=1)

        assert len(rerank.calls[0][1]) == 2

    async def test_min_rerank_score_drops_low_scores(self, kb_dir):
        kb = harness.Knowledge(
            kb_dir, rerank_fn=self._reranker("工資折半"), min_rerank_score=0.5
        )

        hits = await kb.search("假 電池", top_k=5)

        assert _sources(hits) == ["hr/leave.md › 請假規定 › 病假"]

    async def test_reranker_must_score_every_document(self, kb_dir):
        async def broken(query, documents):
            return [1.0]

        kb = harness.Knowledge(kb_dir, rerank_fn=broken)

        with pytest.raises(ValueError, match="回傳了 1 個分數"):
            await kb.search("假 電池")

    async def test_reranker_model_goes_through_llm(self, kb_dir, monkeypatch):
        received = []

        async def fake_rerank(model, query, documents):
            received.append(model)
            return [0.5] * len(documents)

        monkeypatch.setattr(llm, "rerank", fake_rerank)
        kb = harness.Knowledge(kb_dir, reranker="cohere/rerank-v3.5")

        hits = await kb.search("電池")

        assert received == ["cohere/rerank-v3.5"]
        assert hits[0].rerank_score == 0.5
        assert (await kb.stats())["reranker"] is True

    def test_min_rerank_score_needs_reranker(self, kb_dir):
        with pytest.raises(ValueError, match="reranker"):
            harness.Knowledge(kb_dir, min_rerank_score=0.5)

    def test_scores_are_shown_for_people_not_for_models(self):
        chunk = knowledge.Chunk("faq.md", "", "內容")
        hit = knowledge.Hit(chunk, 0.9, similarity=0.8, rerank_score=0.9)

        assert "相似度" not in knowledge.format_hits([hit])
        assert knowledge.format_hits([hit], show_scores=True).startswith(
            "[1] 來源：faq.md（相似度 0.800、rerank 0.900）\n"
        )


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

    def test_reranker_and_thresholds_from_harness_yaml(self, project_dir):
        config = project_dir / "harness.yaml"
        config.write_text(
            config.read_text(encoding="utf-8") + "knowledge:\n"
            "  embeddingModel: openai/emb\n"
            "  minSimilarity: 0.3\n"
            "  reranker: cohere/rerank-v3.5\n"
            "  rerankCandidates: 30\n"
            "  minRerankScore: 0.2\n",
            encoding="utf-8",
        )

        kb = harness.load_project(project_dir).knowledge

        assert kb.min_similarity == 0.3
        assert kb.reranker == "cohere/rerank-v3.5"
        assert kb.rerank_candidates == 30
        assert kb.min_rerank_score == 0.2
        assert kb.vector_store.path == (
            project_dir / ".harness/knowledge" / knowledge.VECTOR_FILE
        )

    def test_unknown_knowledge_setting_is_an_error(self, project_dir):
        config = project_dir / "harness.yaml"
        config.write_text(
            config.read_text(encoding="utf-8") + "knowledge:\n  topk: 3\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="不認得的設定"):
            harness.load_project(project_dir)
