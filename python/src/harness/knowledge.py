"""知識庫：讓 agent 從文件裡找答案並附上出處（RAG）。

流程：
    1. 讀取資料夾裡的文件，交給依副檔名選的解析器（parser）轉成文字：
       .md、.txt、.docx、.pptx、.xlsx，裝了 pypdf 時也支援 .pdf。解析
       是可以替換的一步（Knowledge(parsers=...)），之後要換成更好的
       文件解析服務時，只要換掉這一步。
    2. 依段落切成片段，記下來源檔案、所在標題或頁碼。
    3. 搜尋時用關鍵字比對（BM25，不需要任何模型）；有設定 embedding
       模型時再加上語意檢索，兩者的排名合併（hybrid search）。
    4. 有設定相似度門檻時，丟掉與問題不夠相似的片段。
    5. 有設定 reranker 時，用它重新評估前幾名候選的相關度並重新排序。
    6. 以 search_knowledge 工具提供給 agent，結果附上來源讓 agent 引用。

一個專案可以有好幾個知識庫（KnowledgeBase：識別碼、名稱、說明加上
一個 Knowledge），各自有資料夾與檢索設定。search_tool 把好幾個知識庫
包成一個 search_knowledge 工具：分別搜尋後合併排序，結果標出來自哪個
知識庫。

檔案有變動時，下次搜尋會自動重建索引。embedding 存在向量資料庫裡
（預設是內建的 SQLite 檔案，見 harness.vectorstore），以片段內容識別，
沒變的片段不會重新計算。

存取範圍用標籤控制：文件可以標上標籤（例如 hr、finance），有標籤的
文件只有被允許該標籤的 agent 或使用者搜尋得到；沒有標籤的文件所有人
都搜尋得到。標籤的寫法：
    - Markdown 檔開頭的 frontmatter：labels: [hr]
    - 知識庫資料夾裡的 labels.yaml，以檔案路徑樣式指定：
          "hr/*": [hr]
          "finance/*.pdf": [finance]
"""

import asyncio
import dataclasses
import fnmatch
import hashlib
import json
import math
import pathlib
import re
import weakref
import zipfile
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Sequence
from typing import Any
from xml.etree import ElementTree

import yaml

from harness import datatypes
from harness import llm
from harness import tools as tools_lib
from harness import vectorstore

TEXT_SUFFIXES = (".md", ".markdown", ".txt")
PDF_SUFFIX = ".pdf"
OFFICE_SUFFIXES = (".docx", ".pptx", ".xlsx")
LABELS_FILE = "labels.yaml"
TOOL_NAME = "search_knowledge"
VECTOR_FILE = "vectors.sqlite3"
# 舊版把 embedding 存成 JSON 的檔名，讀到時搬進向量資料庫。
LEGACY_CACHE_FILE = "embeddings.json"

# BM25 的標準參數。
_K1 = 1.5
_B = 0.75
# Reciprocal Rank Fusion 的平滑常數，常用值。
_RRF_K = 60
# 語意檢索至少取這麼多名參與排名合併。
_VECTOR_POOL = 50
# Office 檔是 zip：每個 XML 最多讀這麼多位元組，避免壓縮炸彈。
_OFFICE_PART_LIMIT = 50 * 1024 * 1024
# 試算表每個工作表最多讀幾列。
_SHEET_ROW_LIMIT = 20_000

_WORD = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"|[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]+"
)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
RerankFn = Callable[[str, list[str]], Awaitable[list[float]]]
LabelFilter = Callable[[datatypes.RunContext], list[str] | None]


@dataclasses.dataclass
class Section:
    """解析器從文件讀出的一段文字，之後再切成片段。

    Attributes:
        text: 文字內容；Markdown 標題會記成片段的位置。
        location: 在文件裡的位置（例如「第 3 頁」），沒有則為空字串。
        labels: 文件自己帶的存取標籤（例如 Markdown 的 frontmatter）。
    """

    text: str
    location: str = ""
    labels: tuple[str, ...] = ()


# 解析器：讀一個檔案，回傳它的文字段落。讀不了時丟出 OSError、
# UnicodeDecodeError、ImportError 或 ValueError，該檔案會被略過並記在
# warnings。
Parser = Callable[[pathlib.Path], list[Section]]


@dataclasses.dataclass
class Chunk:
    """知識庫裡的一個片段。

    Attributes:
        source: 來源檔案（相對於知識庫資料夾）或 add_text 給的名稱。
        location: 在來源裡的位置（所在標題或頁碼），沒有則為空字串。
        text: 片段內容。
        labels: 存取標籤，空的表示所有人都搜尋得到。
    """

    source: str
    location: str
    text: str
    labels: tuple[str, ...] = ()

    @property
    def citation(self) -> str:
        """給人看的出處，例如 hr/leave.md › 特休。"""
        if not self.location:
            return self.source
        return f"{self.source} › {self.location}"


@dataclasses.dataclass
class Hit:
    """一筆搜尋結果。

    Attributes:
        chunk: 找到的片段。
        score: 排序依據，越大越相關。有 reranker 時等於 rerank_score；
            否則是排名合併的分數，只用來排序，不同查詢之間不能比較。
        similarity: 與查詢的語意相似度（餘弦，-1 到 1）；沒有使用
            embedding 或沒有計算時為 None。
        rerank_score: reranker 給的相關度；沒有使用 reranker 時為 None。
        base: 來自哪個知識庫（顯示名稱）；同時搜尋好幾個知識庫時才有，
            會出現在出處前面。
    """

    chunk: Chunk
    score: float
    similarity: float | None = None
    rerank_score: float | None = None
    base: str = ""

    @property
    def citation(self) -> str:
        """出處；有知識庫名稱時放在前面，例如「人資規章 / leave.md › 特休」。"""
        if not self.base:
            return self.chunk.citation
        return f"{self.base} / {self.chunk.citation}"


class Knowledge:
    """一個知識庫。

    Example:
        kb = Knowledge(
            "knowledge",
            embedding_model="openai/text-embedding-3-small",
            reranker="cohere/rerank-v3.5",
            min_similarity=0.3,
        )
        agent = harness.Agent(name="faq", tools=[kb.as_tool()])

    Attributes:
        path: 文件資料夾，None 表示只用 add_text 加入的內容。
        embedding_model: litellm 的 embedding 模型名稱，None 表示只用
            關鍵字檢索。
        chunk_size: 每個片段的目標字數。
        cache_dir: 存放向量資料庫的資料夾，None 表示不存檔。
        vector_store: 存放 embedding 的向量資料庫。
        reranker: litellm 的 rerank 模型名稱，None 表示不重新排序。
        rerank_candidates: 交給 reranker 重新排序的候選數。
        min_similarity: 語意相似度門檻，低於它的片段不回傳；需要
            embedding。
        min_rerank_score: reranker 相關度門檻，低於它的片段不回傳；
            需要 reranker。
        label_filter: 依執行環境決定可搜尋標籤的函式（例如依
            context.deps 裡的使用者部門）。回傳 None 表示不限制。
        parsers: 依副檔名（小寫、含點）選的解析器；預設是
            DEFAULT_PARSERS，傳入的會蓋過同副檔名的預設解析器。
        exclude: 不讀取的子資料夾（例如放在這個知識庫資料夾裡的其他
            知識庫）。
        warnings: 建立索引時略過的檔案與原因。
    """

    def __init__(
        self,
        path: str | pathlib.Path | None = None,
        *,
        embedding_model: str | None = None,
        chunk_size: int = 800,
        cache_dir: str | pathlib.Path | None = None,
        vector_store: vectorstore.VectorStore | None = None,
        reranker: str | None = None,
        rerank_candidates: int = 20,
        min_similarity: float | None = None,
        min_rerank_score: float | None = None,
        label_filter: LabelFilter | None = None,
        embed_fn: EmbedFn | None = None,
        rerank_fn: RerankFn | None = None,
        parsers: dict[str, Parser] | None = None,
        exclude: Collection[str | pathlib.Path] = (),
    ) -> None:
        if chunk_size < 100:
            raise ValueError("chunk_size 至少要 100")
        if rerank_candidates < 1:
            raise ValueError("rerank_candidates 至少要 1")
        if min_similarity is not None:
            if not (embedding_model or embed_fn):
                raise ValueError(
                    "min_similarity 需要語意檢索，請同時設定 embedding_model"
                )
            if not -1 <= min_similarity <= 1:
                raise ValueError("min_similarity 必須介於 -1 與 1 之間")
        if min_rerank_score is not None and not (reranker or rerank_fn):
            raise ValueError(
                "min_rerank_score 需要 reranker，請同時設定 reranker"
            )
        self.path = pathlib.Path(path) if path is not None else None
        self.embedding_model = embedding_model
        self.chunk_size = chunk_size
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else None
        if vector_store is None:
            location = self.cache_dir / VECTOR_FILE if self.cache_dir else None
            vector_store = vectorstore.SqliteVectorStore(location)
        self.vector_store = vector_store
        self.reranker = reranker
        self.rerank_candidates = rerank_candidates
        self.min_similarity = min_similarity
        self.min_rerank_score = min_rerank_score
        self.label_filter = label_filter
        self.parsers = {**DEFAULT_PARSERS, **(parsers or {})}
        self.exclude = tuple(pathlib.Path(item) for item in exclude)
        self.warnings: list[str] = []
        self._embed_fn = embed_fn
        self._rerank_fn = rerank_fn
        self._extra: list[Chunk] = []
        self._signature: tuple[Any, ...] | None = None
        self._chunks: list[Chunk] = []
        self._tokens: list[dict[str, int]] = []
        self._doc_freq: dict[str, int] = {}
        self._avg_length = 0.0
        # 每個片段在向量資料庫裡的鍵；沒有使用 embedding 時是空的。
        self._keys: list[str] = []
        # as_tool 建立的工具與它的設定（標籤、筆數），narrowed 依此重建。
        self._made: dict[int, tuple[tools_lib.Tool, list[str] | None, int]] = {}
        self._locks: weakref.WeakKeyDictionary[Any, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )

    def add_text(
        self, text: str, source: str, labels: list[str] | None = None
    ) -> None:
        """直接加入一段文字（例如從資料庫讀出的 FAQ）。

        Args:
            text: 內容。
            source: 來源名稱，會出現在搜尋結果的出處。
            labels: 存取標籤。
        """
        label_tuple = tuple(labels or ())
        self._extra += split_text(text, source, label_tuple, self.chunk_size)
        self._signature = None

    async def search(
        self,
        query: str,
        top_k: int = 5,
        labels: list[str] | None = None,
    ) -> list[Hit]:
        """搜尋知識庫。

        Args:
            query: 要找的內容，用自然語言描述即可。
            top_k: 最多回傳幾筆。
            labels: 允許的標籤；None 表示不限制。有標籤的文件只有在
                標籤與這裡有交集時才會被找到。

        Returns:
            依相關度排序的結果；有設定門檻時只包含達到門檻的片段，
            可能少於 top_k 筆或是空的。
        """
        await self._ensure_index()
        allowed = None if labels is None else set(labels)
        visible = [
            index
            for index, chunk in enumerate(self._chunks)
            if allowed is None
            or not chunk.labels
            or allowed.intersection(chunk.labels)
        ]
        if not visible or not query.strip() or top_k < 1:
            return []
        reranking = bool(self.reranker or self._rerank_fn)
        pool = max(top_k, self.rerank_candidates if reranking else top_k)

        rankings = [self._keyword_ranking(query, visible)]
        similarity: dict[int, float] = {}
        query_vector: list[float] | None = None
        if self._keys:
            [query_vector] = await self._embed([query])
            similarity = await self._similarities(
                query_vector, visible, max(_VECTOR_POOL, pool * 2)
            )
            rankings.append(
                sorted(similarity, key=lambda i: similarity[i], reverse=True)
            )
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, index in enumerate(ranking):
                fused[index] = fused.get(index, 0.0) + 1 / (_RRF_K + rank + 1)
        order = sorted(fused, key=lambda index: fused[index], reverse=True)

        selected: list[int] = []
        for start in range(0, len(order), pool):
            batch = order[start : start + pool]
            if self.min_similarity is not None and query_vector is not None:
                unknown = [index for index in batch if index not in similarity]
                if unknown:
                    similarity.update(
                        await self._similarities(
                            query_vector, unknown, len(unknown)
                        )
                    )
                batch = [
                    index
                    for index in batch
                    if similarity.get(index, -1.0) >= self.min_similarity
                ]
            selected += batch
            if len(selected) >= pool:
                break

        hits = [
            Hit(self._chunks[index], fused[index], similarity.get(index))
            for index in selected[:pool]
        ]
        if reranking and hits:
            hits = await self._rerank(query, hits)
        return hits[:top_k]

    def as_tool(
        self, labels: list[str] | None = None, top_k: int = 5
    ) -> tools_lib.Tool:
        """把知識庫包成 agent 可以呼叫的 search_knowledge 工具。

        Args:
            labels: 這個 agent 可以搜尋的標籤；None 表示不限制。和
                label_filter 同時設定時，兩者都允許的標籤才搜尋得到。
            top_k: 每次搜尋最多回傳幾筆。

        Returns:
            search_knowledge 工具。
        """

        made = self._search_tool(labels, top_k)
        self._made[id(made)] = (made, labels, top_k)
        return made

    def narrowed(
        self, item: tools_lib.Tool, labels: list[str] | None
    ) -> tools_lib.Tool | None:
        """把 as_tool 建立的工具再限定在 labels 裡（和原本的標籤取交集）。

        網頁版依使用者所屬的群組限制可以搜尋的標籤時使用。

        Args:
            item: 原本的 search_knowledge 工具。
            labels: 這個人可以搜尋的標籤；None 表示不再限制。

        Returns:
            新的工具；item 不是這個知識庫建立的時為 None。
        """
        found = self._made.get(id(item))
        if found is None or found[0] is not item:
            return None
        if labels is None:
            return item
        _, fixed, top_k = found
        # 不經過 as_tool：每一輪都會重建，不能留在 _made 裡越積越多。
        return self._search_tool(_combine_labels(fixed, labels), top_k)

    def _search_tool(
        self, labels: list[str] | None, top_k: int
    ) -> tools_lib.Tool:
        async def search_knowledge(
            context: datatypes.RunContext, query: str
        ) -> str:
            """搜尋知識庫，取得與問題相關的文件片段與出處。

            回答公司內部規定、產品資訊等問題前先搜尋；回答時只根據
            找到的內容，並註明出處（方括號裡的來源）。找不到時直接說
            知識庫裡沒有相關資料，不要自己編造。

            Args:
                query: 要找的內容，寫成完整的問題或關鍵字，例如
                    「特休假怎麼計算」。
            """
            allowed = _combine_labels(labels, self._dynamic_labels(context))
            hits = await self.search(query, top_k=top_k, labels=allowed)
            return format_hits(hits)

        return tools_lib.tool(search_knowledge, name=TOOL_NAME)

    async def labels(self) -> list[str]:
        """知識庫裡用到的所有標籤（排序過）。"""
        await self._ensure_index()
        return sorted(
            {label for chunk in self._chunks for label in chunk.labels}
        )

    async def stats(self) -> dict[str, Any]:
        """建立索引並回傳概況，方便確認文件有沒有被讀到。

        Returns:
            檔案數、片段數、是否使用 embedding 與 reranker、門檻設定與
            略過的檔案。
        """
        await self._ensure_index()
        return {
            "sources": len({chunk.source for chunk in self._chunks}),
            "chunks": len(self._chunks),
            "embedding": bool(self._keys),
            "reranker": bool(self.reranker or self._rerank_fn),
            "min_similarity": self.min_similarity,
            "min_rerank_score": self.min_rerank_score,
            "warnings": list(self.warnings),
        }

    def _dynamic_labels(
        self, context: datatypes.RunContext
    ) -> list[str] | None:
        """依執行環境取得可搜尋的標籤。"""
        if self.label_filter is None:
            return None
        return self.label_filter(context)

    async def _ensure_index(self) -> None:
        """檔案有變動（或從未建立）時重建索引。"""
        loop = asyncio.get_running_loop()
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            signature = self._current_signature()
            if signature == self._signature:
                return
            self.warnings = []
            chunks = [*self._read_folder(), *self._extra]
            self._build_keyword_index(chunks)
            keys: list[str] = []
            if self.embedding_model or self._embed_fn:
                model = self.embedding_model or ""
                keys = [_cache_key(model, chunk.text) for chunk in chunks]
                await self._store_vectors(keys, chunks)
            self._keys = keys
            self._chunks = chunks
            self._signature = signature

    def files(self) -> list[str]:
        """知識庫資料夾裡的文件（相對路徑，含不支援的格式）。

        不含隱藏檔、labels.yaml 與 exclude 的子資料夾，不需要建立索引。
        """
        if self.path is None:
            return []
        return [
            path.relative_to(self.path).as_posix() for path in self._files()
        ]

    async def documents(self) -> dict[str, dict[str, Any]]:
        """建立索引並回傳每份文件的片段數與標籤（以相對路徑為鍵）。"""
        await self._ensure_index()
        found: dict[str, dict[str, Any]] = {}
        for chunk in self._chunks:
            item = found.setdefault(chunk.source, {"chunks": 0, "labels": []})
            item["chunks"] += 1
            for label in chunk.labels:
                if label not in item["labels"]:
                    item["labels"].append(label)
        return found

    def _current_signature(self) -> tuple[Any, ...]:
        """用檔案的修改時間與大小判斷內容有沒有變。"""
        files = []
        for path in self._files():
            stat = path.stat()
            files.append((str(path), stat.st_mtime_ns, stat.st_size))
        labels_path = self._labels_path()
        if labels_path is not None and labels_path.exists():
            stat = labels_path.stat()
            files.append(("labels", stat.st_mtime_ns, stat.st_size))
        return (tuple(files), len(self._extra))

    def _files(self) -> list[pathlib.Path]:
        """知識庫資料夾裡的所有檔案（不含隱藏檔、labels.yaml 與 exclude
        的子資料夾）。"""
        if self.path is None or not self.path.exists():
            return []
        excluded = [self._inside(item) for item in self.exclude]
        found = []
        for path in self.path.rglob("*"):
            relative = path.relative_to(self.path)
            if (
                not path.is_file()
                or relative.as_posix() == LABELS_FILE
                or any(part.startswith(".") for part in relative.parts)
                or any(
                    item is not None and relative.is_relative_to(item)
                    for item in excluded
                )
            ):
                continue
            found.append(path)
        return sorted(found)

    def _inside(self, folder: pathlib.Path) -> pathlib.Path | None:
        """exclude 的資料夾相對於知識庫資料夾的位置；不在裡面時為 None。"""
        assert self.path is not None
        if not folder.is_absolute():
            return folder
        try:
            return folder.resolve().relative_to(self.path.resolve())
        except ValueError:
            return None

    def _labels_path(self) -> pathlib.Path | None:
        if self.path is None:
            return None
        return self.path / LABELS_FILE

    def _read_folder(self) -> list[Chunk]:
        """讀取資料夾裡所有支援的檔案並切成片段。"""
        rules = self._label_rules()
        chunks: list[Chunk] = []
        for path in self._files():
            source = path.relative_to(self.path).as_posix()
            parser = self.parsers.get(path.suffix.lower())
            if parser is None:
                self.warnings.append(f"{source}：不支援的格式，已略過")
                continue
            try:
                sections = parser(path)
            except (
                OSError,
                UnicodeDecodeError,
                ImportError,
                ValueError,
                zipfile.BadZipFile,
                ElementTree.ParseError,
            ) as error:
                self.warnings.append(f"{source}：{error}")
                continue
            chunks += self._split(sections, source, _labels_for(source, rules))
        return chunks

    def _split(
        self, sections: list[Section], source: str, labels: list[str]
    ) -> list[Chunk]:
        """把解析出的段落切成片段，標上 labels.yaml 與文件自己的標籤。"""
        chunks = []
        for section in sections:
            merged = tuple(sorted({*labels, *section.labels}))
            chunks += split_text(
                section.text,
                source,
                merged,
                self.chunk_size,
                location=section.location,
            )
        return chunks

    def _label_rules(self) -> dict[str, list[str]]:
        """讀取 labels.yaml。"""
        path = self._labels_path()
        if path is None or not path.exists():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            self.warnings.append(
                f"{LABELS_FILE}：格式必須是「路徑樣式: 標籤列表」"
            )
            return {}
        return {
            str(pattern): [str(label) for label in (labels or [])]
            for pattern, labels in data.items()
        }

    def _build_keyword_index(self, chunks: list[Chunk]) -> None:
        """建立 BM25 需要的詞頻統計。"""
        self._tokens = []
        self._doc_freq = {}
        for chunk in chunks:
            counts: dict[str, int] = {}
            for token in tokenize(
                f"{chunk.source} {chunk.location} {chunk.text}"
            ):
                counts[token] = counts.get(token, 0) + 1
            self._tokens.append(counts)
            for token in counts:
                self._doc_freq[token] = self._doc_freq.get(token, 0) + 1
        total = sum(sum(counts.values()) for counts in self._tokens)
        self._avg_length = total / len(chunks) if chunks else 0.0

    def _keyword_ranking(self, query: str, visible: list[int]) -> list[int]:
        """用 BM25 排序，只回傳至少有一個詞命中的片段。"""
        terms = set(tokenize(query))
        total = len(self._tokens)
        scores: dict[int, float] = {}
        for index in visible:
            counts = self._tokens[index]
            length = sum(counts.values()) or 1
            score = 0.0
            for term in terms:
                freq = counts.get(term, 0)
                if not freq:
                    continue
                docs = self._doc_freq.get(term, 0)
                idf = math.log(1 + (total - docs + 0.5) / (docs + 0.5))
                norm = _K1 * (1 - _B + _B * length / (self._avg_length or 1))
                score += idf * freq * (_K1 + 1) / (freq + norm)
            if score > 0:
                scores[index] = score
        return sorted(scores, key=lambda index: scores[index], reverse=True)

    async def _similarities(
        self, query_vector: list[float], indices: list[int], limit: int
    ) -> dict[int, float]:
        """向向量資料庫查詢這些片段與查詢的相似度，只回傳前 limit 名。"""
        by_key: dict[str, list[int]] = {}
        for index in indices:
            by_key.setdefault(self._keys[index], []).append(index)
        pairs = await self.vector_store.search(query_vector, by_key, limit)
        return {
            index: score
            for key, score in pairs
            for index in by_key.get(key, [])
        }

    async def _rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        """用 reranker 重新評估候選，依相關度排序並套用門檻。"""
        documents = [f"{hit.chunk.citation}\n{hit.chunk.text}" for hit in hits]
        if self._rerank_fn is not None:
            scores = await self._rerank_fn(query, documents)
        else:
            scores = await llm.rerank(self.reranker or "", query, documents)
        if len(scores) != len(hits):
            raise ValueError(
                f"reranker 回傳了 {len(scores)} 個分數，應該是 {len(hits)} 個"
            )
        for hit, score in zip(hits, scores, strict=True):
            hit.rerank_score = float(score)
            hit.score = float(score)
        hits.sort(key=lambda hit: hit.score, reverse=True)
        if self.min_rerank_score is None:
            return hits
        return [hit for hit in hits if hit.score >= self.min_rerank_score]

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """呼叫 embedding 模型（或測試時注入的函式）。"""
        if self._embed_fn is not None:
            return await self._embed_fn(texts)
        return await llm.embed(self.embedding_model, texts)

    async def _store_vectors(
        self, keys: list[str], chunks: list[Chunk]
    ) -> None:
        """把片段的 embedding 存進向量資料庫，已經有的不重新計算。"""
        by_key = dict(zip(keys, (chunk.text for chunk in chunks), strict=True))
        await self._import_legacy_cache(by_key)
        missing = sorted(await self.vector_store.missing(by_key))
        if missing:
            vectors = await self._embed([by_key[key] for key in missing])
            if len(vectors) != len(missing):
                raise ValueError(
                    f"embedding 模型回傳了 {len(vectors)} 個向量，"
                    f"應該是 {len(missing)} 個"
                )
            await self.vector_store.add(
                dict(zip(missing, vectors, strict=True))
            )
        await self.vector_store.retain(by_key)

    async def _import_legacy_cache(self, wanted: dict[str, str]) -> None:
        """把舊版存成 JSON 的 embedding 搬進向量資料庫，再刪除 JSON 檔。"""
        if self.cache_dir is None:
            return
        path = self.cache_dir / LEGACY_CACHE_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            vectors = {key: data[key] for key in wanted if key in data}
            await self.vector_store.add(vectors)
        path.unlink(missing_ok=True)


@dataclasses.dataclass
class KnowledgeBase:
    """專案裡的一個知識庫：識別碼、給人看的名稱與說明，加上檢索用的
    Knowledge。

    Attributes:
        id: 識別碼（英數字、- 與 _），也是預設的資料夾名稱。
        name: 顯示名稱，會出現在搜尋結果的出處前面。
        description: 說明，給 agent 判斷要不要搜尋，也顯示在網頁上。
        knowledge: 這個知識庫的文件與檢索設定。
    """

    id: str
    name: str
    knowledge: "Knowledge"
    description: str = ""


@dataclasses.dataclass
class KnowledgeTool(tools_lib.Tool):
    """search_tool 建立的 search_knowledge 工具，記得自己搜尋哪些知識庫。

    網頁版依使用者所屬的群組與對話勾選的知識庫，用 narrow_tool 重新
    建立範圍較小的工具。

    Attributes:
        bases: 搜尋的知識庫。
        labels: 可以搜尋的標籤；None 表示不限制。
        top_k: 每次搜尋最多回傳幾筆。
    """

    bases: tuple[KnowledgeBase, ...] = ()
    labels: tuple[str, ...] | None = None
    top_k: int = 5


def search_tool(
    bases: Sequence[KnowledgeBase],
    labels: list[str] | None = None,
    top_k: int = 5,
) -> KnowledgeTool:
    """把一個或好幾個知識庫包成 agent 可以呼叫的 search_knowledge 工具。

    好幾個知識庫時分別搜尋再合併排序（見 search_bases），每筆結果的
    出處前面加上知識庫名稱；工具說明會列出每個知識庫的名稱與說明，
    讓 agent 知道裡面有什麼。

    Args:
        bases: 要搜尋的知識庫，至少一個。
        labels: 可以搜尋的標籤；None 表示不限制。和知識庫的
            label_filter 同時設定時，兩者都允許的標籤才搜尋得到。
        top_k: 每次搜尋最多回傳幾筆。

    Returns:
        search_knowledge 工具。

    Raises:
        ValueError: 沒有給任何知識庫。
    """
    if not bases:
        raise ValueError("search_tool 至少要有一個知識庫")
    chosen = tuple(bases)
    fixed = None if labels is None else tuple(labels)

    async def search_knowledge(
        context: datatypes.RunContext, query: str
    ) -> str:
        """搜尋知識庫，取得與問題相關的文件片段與出處。

        回答公司內部規定、產品資訊等問題前先搜尋；回答時只根據
        找到的內容，並註明出處（方括號裡的來源）。找不到時直接說
        知識庫裡沒有相關資料，不要自己編造。

        Args:
            query: 要找的內容，寫成完整的問題或關鍵字，例如
                「特休假怎麼計算」。
        """
        hits = await search_bases(
            chosen,
            query,
            top_k=top_k,
            labels=None if fixed is None else list(fixed),
            context=context,
        )
        return format_hits(hits)

    made = tools_lib.tool(search_knowledge, name=TOOL_NAME)
    description = made.description
    if len(chosen) > 1 or chosen[0].description:
        listed = "\n".join(
            f"- {base.name or base.id}"
            + (f"：{base.description}" if base.description else "")
            for base in chosen
        )
        description += f"\n\n會搜尋這些知識庫：\n{listed}"
    return KnowledgeTool(
        name=made.name,
        description=description,
        parameters=made.parameters,
        func=made.func,
        needs_approval=False,
        args_model=made.args_model,
        context_param=made.context_param,
        bases=chosen,
        labels=fixed,
        top_k=top_k,
    )


def narrow_tool(
    item: tools_lib.Tool,
    bases: Collection[str] | None = None,
    labels: Collection[str] | None = None,
    replace_bases: Sequence[KnowledgeBase] | None = None,
) -> tools_lib.Tool | None:
    """把 search_tool 建立的工具換成範圍較小的版本。

    Args:
        item: 原本的工具；不是 KnowledgeTool 時原樣回傳。
        bases: 只留這些識別碼的知識庫；None 表示不限制。
        labels: 和原本的標籤取交集；None 表示不再限制。
        replace_bases: 改搜尋這些知識庫（例如使用者在對話裡勾選的），
            再套用 bases 的限制；None 表示沿用原本的。

    Returns:
        新的工具；一個知識庫都不剩時為 None（呼叫端應該拿掉這個工具）。
    """
    if not isinstance(item, KnowledgeTool):
        return item
    chosen = list(item.bases if replace_bases is None else replace_bases)
    if bases is not None:
        allowed = set(bases)
        chosen = [base for base in chosen if base.id in allowed]
    if not chosen:
        return None
    fixed = None if item.labels is None else list(item.labels)
    merged = _combine_labels(fixed, None if labels is None else sorted(labels))
    unchanged = (
        replace_bases is None
        and len(chosen) == len(item.bases)
        and merged == fixed
    )
    if unchanged:
        return item
    return search_tool(chosen, merged, item.top_k)


async def search_bases(
    bases: Sequence[KnowledgeBase],
    query: str,
    top_k: int = 5,
    labels: list[str] | None = None,
    context: datatypes.RunContext | None = None,
) -> list[Hit]:
    """同時搜尋好幾個知識庫，合併成一份依相關度排序的結果。

    各知識庫的分數彼此不能比較（切片、檢索方式不同），所以合併時：

    - 每個知識庫都用同一個 reranker 時，依 rerank 分數排序；
    - 否則依各自的名次輪流取（Reciprocal Rank Fusion），名次相同時
      前面的知識庫優先。

    Args:
        bases: 要搜尋的知識庫。
        query: 要找的內容。
        top_k: 最多回傳幾筆。
        labels: 允許的標籤；None 表示不限制。
        context: 執行環境，用來套用各知識庫的 label_filter。

    Returns:
        合併後的結果；好幾個知識庫時每筆標上知識庫名稱（Hit.base）。
    """
    if not bases:
        return []

    async def one(base: KnowledgeBase) -> list[Hit]:
        allowed = labels
        if context is not None:
            allowed = _combine_labels(
                labels, base.knowledge._dynamic_labels(context)
            )
        return await base.knowledge.search(query, top_k=top_k, labels=allowed)

    results = await asyncio.gather(*(one(base) for base in bases))
    if len(bases) == 1:
        return results[0]
    for base, hits in zip(bases, results, strict=True):
        for hit in hits:
            hit.base = base.name or base.id
    rerankers = {base.knowledge.reranker for base in bases}
    hits = [hit for found in results for hit in found]
    same_reranker = len(rerankers) == 1 and None not in rerankers
    if same_reranker and all(hit.rerank_score is not None for hit in hits):
        hits.sort(key=lambda hit: hit.rerank_score or 0.0, reverse=True)
        return hits[:top_k]
    ranked = [
        (1 / (_RRF_K + rank + 1), -order, hit)
        for order, found in enumerate(results)
        for rank, hit in enumerate(found)
    ]
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [hit for _, _, hit in ranked[:top_k]]


def tokenize(text: str) -> list[str]:
    """把文字切成檢索用的詞。

    英文與數字以單字為單位；中日韓文字沒有空白分詞，所以取單字與相鄰
    兩字（bigram），不需要另外的斷詞套件。

    Args:
        text: 要切的文字。

    Returns:
        詞的列表（可能重複）。
    """
    tokens: list[str] = []
    for match in _WORD.finditer(text.lower()):
        word = match.group(0)
        if word[0].isascii():
            tokens.append(word)
            continue
        tokens.extend(word)
        tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


def split_text(
    text: str,
    source: str,
    labels: tuple[str, ...],
    chunk_size: int,
    location: str = "",
) -> list[Chunk]:
    """依段落把文字切成片段。

    段落會盡量合併到接近 chunk_size，不會從段落中間切開；單一段落
    超過 chunk_size 時才依長度硬切。Markdown 標題會記錄成片段的位置。

    Args:
        text: 要切的文字。
        source: 來源名稱。
        labels: 存取標籤。
        chunk_size: 每個片段的目標字數。
        location: 預設的位置（例如 PDF 的頁碼）；遇到 Markdown 標題
            後改用標題路徑。

    Returns:
        片段列表。
    """
    chunks: list[Chunk] = []
    buffer: list[str] = []
    heading = location
    # 目前所在的各層標題，位置寫成「第一層 › 第二層」。
    path: dict[int, str] = {}
    # 只有標題、沒有內文的緩衝不輸出（例如標題後面緊接著子標題）。
    has_body = False

    def flush() -> None:
        nonlocal has_body
        content = "\n\n".join(buffer).strip()
        if content and has_body:
            chunks.append(Chunk(source, heading, content, labels))
        buffer.clear()
        has_body = False

    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        match = _HEADING.match(lines[0])
        if match:
            flush()
            level = len(match.group(1))
            path = {k: v for k, v in path.items() if k < level}
            path[level] = match.group(2)
            heading = " › ".join(path[k] for k in sorted(path))
        for piece in _hard_split(block, chunk_size):
            if buffer and len("\n\n".join([*buffer, piece])) > chunk_size:
                flush()
            buffer.append(piece)
            if not (match and len(lines) == 1):
                has_body = True
    flush()
    return chunks


def format_hits(hits: list[Hit], show_scores: bool = False) -> str:
    """把搜尋結果轉成給模型看的文字。

    Args:
        hits: 搜尋結果。
        show_scores: 是否在出處後面附上相似度與 rerank 分數（給人調整
            門檻時參考，不給模型看）。

    Returns:
        每筆結果附上編號與出處的文字。
    """
    if not hits:
        return "知識庫裡沒有找到相關的內容。"
    parts = []
    for number, hit in enumerate(hits, start=1):
        header = f"[{number}] 來源：{hit.citation}"
        if show_scores:
            header += _describe_scores(hit)
        parts.append(f"{header}\n{hit.chunk.text}")
    return "\n\n---\n\n".join(parts)


def _describe_scores(hit: Hit) -> str:
    """例如「（相似度 0.82、rerank 0.91）」；沒有分數時是空字串。"""
    scores = []
    if hit.similarity is not None:
        scores.append(f"相似度 {hit.similarity:.3f}")
    if hit.rerank_score is not None:
        scores.append(f"rerank {hit.rerank_score:.3f}")
    return f"（{'、'.join(scores)}）" if scores else ""


def _hard_split(block: str, size: int) -> list[str]:
    """把超過長度的段落依長度切開，前後保留一小段重疊。"""
    if len(block) <= size:
        return [block]
    overlap = size // 8
    step = size - overlap
    return [block[start : start + size] for start in range(0, len(block), step)]


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """拆出 Markdown 開頭的 YAML 設定。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, parts[2]


def _labels_for(source: str, rules: dict[str, list[str]]) -> list[str]:
    """依 labels.yaml 的路徑樣式找出檔案的標籤。"""
    found: list[str] = []
    for pattern, labels in rules.items():
        if fnmatch.fnmatchcase(source, pattern):
            found += labels
    return found


def parse_markdown(path: pathlib.Path) -> list[Section]:
    """Markdown 與純文字：開頭的 frontmatter 可以寫 labels: [hr]。"""
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    labels = tuple(str(label) for label in meta.get("labels") or [])
    return [Section(body, labels=labels)]


def parse_pdf(path: pathlib.Path) -> list[Section]:
    """PDF：一頁一段，位置是頁碼。需要 pypdf。"""
    try:
        import pypdf
    except ImportError as error:
        raise ImportError(
            "讀取 PDF 需要 pypdf，請執行 pip install pypdf"
        ) from error
    reader = pypdf.PdfReader(str(path))
    return [
        Section(page.extract_text() or "", f"第 {number} 頁")
        for number, page in enumerate(reader.pages, start=1)
    ]


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PACKAGE_R = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def parse_docx(path: pathlib.Path) -> list[Section]:
    """Word：段落照順序讀出，標題樣式轉成 Markdown 標題（成為片段的
    位置），表格每一列寫成「欄 | 欄」。只用標準函式庫。"""
    with zipfile.ZipFile(path) as archive:
        root = _office_xml(archive, "word/document.xml")
    body = root.find(f"{_W}body")
    lines: list[str] = []
    for element in body if body is not None else []:
        if element.tag == f"{_W}p":
            text = _docx_text(element)
            level = _heading_level(element)
            if text and level:
                text = f"{'#' * level} {text}"
            lines.append(text)
        elif element.tag == f"{_W}tbl":
            for row in element.iter(f"{_W}tr"):
                cells = [_docx_text(cell) for cell in row.iter(f"{_W}tc")]
                lines.append(" | ".join(cells))
            lines.append("")
    # 段落之間空一行，split_text 才會把它們當成不同段落。
    return [Section("\n\n".join(line for line in lines if line.strip()))]


def parse_pptx(path: pathlib.Path) -> list[Section]:
    """PowerPoint：一張投影片一段，位置是投影片編號。"""
    with zipfile.ZipFile(path) as archive:
        names = [
            name
            for name in archive.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        ]
        names.sort(key=lambda name: int(re.sub(r"\D", "", name)))
        sections = []
        for number, name in enumerate(names, start=1):
            root = _office_xml(archive, name)
            paragraphs = [
                "".join(item.text or "" for item in paragraph.iter(f"{_A}t"))
                for paragraph in root.iter(f"{_A}p")
            ]
            text = "\n".join(line for line in paragraphs if line.strip())
            if text:
                sections.append(Section(text, f"第 {number} 張投影片"))
    return sections


def parse_xlsx(path: pathlib.Path) -> list[Section]:
    """Excel：一個工作表一段，每一列寫成以 | 分隔的一行，位置是工作表
    名稱。公式只讀存好的結果；每個工作表最多讀 _SHEET_ROW_LIMIT 列。"""
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = _office_xml(archive, "xl/sharedStrings.xml")
            shared = [
                "".join(item.text or "" for item in entry.iter(f"{_S}t"))
                for entry in strings.iter(f"{_S}si")
            ]
        sections = []
        for name, target in _xlsx_sheets(archive):
            sheet = _office_xml(archive, target)
            rows = []
            for row in sheet.iter(f"{_S}row"):
                if len(rows) >= _SHEET_ROW_LIMIT:
                    break
                cells = [_xlsx_cell(cell, shared) for cell in row]
                if any(cells):
                    rows.append(" | ".join(cells).rstrip(" |"))
            if rows:
                sections.append(Section("\n".join(rows), name))
    return sections


def _office_xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    """讀出 Office 檔裡的一個 XML；太大時拒絕（避免壓縮炸彈）。"""
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise ValueError(f"檔案內容不完整，缺少 {name}") from error
    if info.file_size > _OFFICE_PART_LIMIT:
        raise ValueError("檔案內容太大，已略過")
    with archive.open(info) as handle:
        data = handle.read(_OFFICE_PART_LIMIT + 1)
    if len(data) > _OFFICE_PART_LIMIT:
        raise ValueError("檔案內容太大，已略過")
    return ElementTree.fromstring(data)


def _docx_text(element: ElementTree.Element) -> str:
    """Word 元素裡的文字（含換行與定位字元）。"""
    parts = []
    for item in element.iter():
        if item.tag == f"{_W}t":
            parts.append(item.text or "")
        elif item.tag == f"{_W}tab":
            parts.append("\t")
        elif item.tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _heading_level(paragraph: ElementTree.Element) -> int:
    """段落是標題時回傳層級（1 到 6），否則 0。"""
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    value = style.get(f"{_W}val", "") if style is not None else ""
    match = re.fullmatch(r"(?i)(?:heading|標題)\s*(\d)", value)
    if match:
        return min(max(int(match.group(1)), 1), 6)
    return 1 if value.lower() == "title" else 0


def _xlsx_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """工作表的名稱與在 zip 裡的位置，照活頁簿裡的順序。"""
    workbook = _office_xml(archive, "xl/workbook.xml")
    targets: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in archive.namelist():
        rels = _office_xml(archive, "xl/_rels/workbook.xml.rels")
        for rel in rels.iter(f"{_PACKAGE_R}Relationship"):
            target = rel.get("Target", "").lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            targets[rel.get("Id", "")] = target
    found = []
    for number, sheet in enumerate(workbook.iter(f"{_S}sheet"), start=1):
        target = targets.get(
            sheet.get(f"{_R}id", ""), f"xl/worksheets/sheet{number}.xml"
        )
        if target in archive.namelist():
            found.append((sheet.get("name") or f"工作表 {number}", target))
    return found


def _xlsx_cell(cell: ElementTree.Element, shared: list[str]) -> str:
    """一個儲存格的值（共用字串、內嵌字串或數字）。"""
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(item.text or "" for item in cell.iter(f"{_S}t")).strip()
    value = cell.find(f"{_S}v")
    text = (value.text or "") if value is not None else ""
    if kind == "s" and text.isdigit() and int(text) < len(shared):
        return shared[int(text)].strip()
    return text.strip()


# 預設的解析器。要換掉某種格式（例如改用外部的文件解析服務），把
# {副檔名: 解析器} 交給 Knowledge(parsers=...)。
DEFAULT_PARSERS: dict[str, Parser] = {
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".txt": parse_markdown,
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".pptx": parse_pptx,
    ".xlsx": parse_xlsx,
}


def _combine_labels(
    fixed: list[str] | None, dynamic: list[str] | None
) -> list[str] | None:
    """合併 agent 設定的標籤與執行時決定的標籤（取交集）。"""
    if fixed is None:
        return dynamic
    if dynamic is None:
        return fixed
    return [label for label in fixed if label in dynamic]


def _cache_key(model: str, text: str) -> str:
    """embedding 快取的鍵：模型名稱加內容的雜湊。"""
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()
