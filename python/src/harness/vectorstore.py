"""知識庫的向量資料庫：保存片段的 embedding，並找出與查詢最相似的片段。

內建的 SqliteVectorStore 不需要另外安裝或架設任何服務：向量存在一個
SQLite 檔案裡，搜尋時在記憶體中計算餘弦相似度。有安裝 numpy 時用它
加速，沒有的話用純 Python 計算（幾千個片段以內都很快）。

要改用外部的向量資料庫（pgvector、Qdrant 等）時，實作 VectorStore 的
四個方法，交給 Knowledge(vector_store=...) 即可。
"""

import array
import math
import pathlib
import sqlite3
import threading
from collections.abc import Collection
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Protocol

try:
    import numpy
except ImportError:  # numpy 是選用的，沒有時改用純 Python 計算。
    numpy = None  # type: ignore[assignment]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    key TEXT PRIMARY KEY,
    dim INTEGER NOT NULL,
    data BLOB NOT NULL
)
"""
_DELETE_BATCH = 500


class VectorStore(Protocol):
    """向量資料庫需要提供的操作。

    向量以字串鍵識別。Knowledge 用「embedding 模型 + 片段內容」的雜湊
    當鍵，所以同樣內容的片段不會重複計算 embedding。
    """

    async def missing(self, keys: Collection[str]) -> set[str]:
        """回傳還沒有存入向量的鍵。"""
        ...

    async def add(self, vectors: Mapping[str, Sequence[float]]) -> None:
        """存入向量；鍵已經存在時覆寫。"""
        ...

    async def retain(self, keys: Collection[str]) -> None:
        """只保留這些鍵，刪除其他已經用不到的向量。"""
        ...

    async def search(
        self, query: Sequence[float], keys: Collection[str], limit: int
    ) -> list[tuple[str, float]]:
        """在指定的鍵裡找出與 query 最相似的向量。

        Args:
            query: 查詢的向量。
            keys: 只在這些鍵裡搜尋（呼叫端用它套用存取標籤）。
            limit: 最多回傳幾筆。

        Returns:
            (鍵, 餘弦相似度) 的列表，依相似度由高到低排序。
        """
        ...


class SqliteVectorStore:
    """內建的向量資料庫，存在一個 SQLite 檔案裡。

    向量正規化後以 float32 存檔；第一次使用時整批讀進記憶體，之後的
    搜尋不再讀檔。

    Example:
        store = SqliteVectorStore("data/kb/vectors.sqlite3")
        kb = harness.Knowledge("knowledge", embedding_model="...",
                               vector_store=store)

    Attributes:
        path: SQLite 檔案位置；None 表示只存在記憶體，程式結束就消失。
    """

    def __init__(self, path: str | pathlib.Path | None = None) -> None:
        self.path = pathlib.Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._vectors: dict[str, array.array] | None = None
        # numpy 加速用：所有向量疊成的矩陣與每個鍵所在的列。
        self._matrix: Any = None
        self._rows: dict[str, int] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._loaded())

    async def missing(self, keys: Collection[str]) -> set[str]:
        with self._lock:
            stored = self._loaded()
            return {key for key in keys if key not in stored}

    async def add(self, vectors: Mapping[str, Sequence[float]]) -> None:
        if not vectors:
            return
        normalized = {
            key: _normalize(vector) for key, vector in vectors.items()
        }
        with self._lock:
            self._loaded().update(normalized)
            self._matrix = None
            with self._connect() as connection:
                if connection is not None:
                    connection.executemany(
                        "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?)",
                        [
                            (key, len(vector), vector.tobytes())
                            for key, vector in normalized.items()
                        ],
                    )

    async def retain(self, keys: Collection[str]) -> None:
        keep = set(keys)
        with self._lock:
            stored = self._loaded()
            stale = [key for key in stored if key not in keep]
            if not stale:
                return
            for key in stale:
                del stored[key]
            self._matrix = None
            with self._connect() as connection:
                if connection is not None:
                    for start in range(0, len(stale), _DELETE_BATCH):
                        batch = stale[start : start + _DELETE_BATCH]
                        marks = ",".join("?" * len(batch))
                        connection.execute(
                            f"DELETE FROM vectors WHERE key IN ({marks})", batch
                        )

    async def search(
        self, query: Sequence[float], keys: Collection[str], limit: int
    ) -> list[tuple[str, float]]:
        if limit <= 0:
            return []
        target = _normalize(query)
        with self._lock:
            stored = self._loaded()
            candidates = [key for key in dict.fromkeys(keys) if key in stored]
            if not candidates:
                return []
            scores = None
            if numpy is not None:
                scores = self._numpy_scores(target, candidates)
            if scores is None:
                scores = [_dot(target, stored[key]) for key in candidates]
        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [(key, float(score)) for key, score in ranked[:limit]]

    def _numpy_scores(
        self, target: array.array, candidates: list[str]
    ) -> list[float] | None:
        """用 numpy 一次算出所有候選的相似度（呼叫端持有鎖）。

        存了不同維度的向量（例如換過 embedding 模型、舊的還沒清掉）時
        疊不成矩陣，回傳 None 讓呼叫端改用純 Python 計算。
        """
        if self._matrix is None:
            stored = self._loaded()
            if len({len(vector) for vector in stored.values()}) > 1:
                return None
            self._rows = {key: row for row, key in enumerate(stored)}
            self._matrix = numpy.array(
                [vector.tolist() for vector in stored.values()],
                dtype=numpy.float32,
            )
        if self._matrix.shape[1] != len(target):
            return None
        rows = numpy.fromiter(
            (self._rows[key] for key in candidates),
            dtype=numpy.int64,
            count=len(candidates),
        )
        query = numpy.frombuffer(target, dtype=numpy.float32)
        return (self._matrix[rows] @ query).tolist()

    def _loaded(self) -> dict[str, array.array]:
        """第一次使用時從檔案讀入所有向量（呼叫端持有鎖）。"""
        if self._vectors is not None:
            return self._vectors
        self._vectors = {}
        with self._connect() as connection:
            if connection is not None:
                rows = connection.execute("SELECT key, data FROM vectors")
                for key, data in rows:
                    vector = array.array("f")
                    vector.frombytes(data)
                    self._vectors[key] = vector
        return self._vectors

    def _connect(self) -> "_Connection":
        return _Connection(self.path)


class _Connection:
    """開啟 SQLite 連線的 context manager；沒有檔案位置時給 None。

    每次操作都開新的連線，所以不同執行緒與事件迴圈都能使用同一個
    store。離開時提交並關閉連線。
    """

    def __init__(self, path: pathlib.Path | None) -> None:
        self._path = path
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection | None:
        if self._path is None:
            return None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path)
        self._connection.execute(_SCHEMA)
        return self._connection

    def __exit__(self, error_type: Any, *_: Any) -> None:
        if self._connection is None:
            return
        if error_type is None:
            self._connection.commit()
        self._connection.close()


def _normalize(vector: Sequence[float]) -> array.array:
    """轉成長度為 1 的 float32 向量，之後內積就是餘弦相似度。"""
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return array.array("f", (0.0 for _ in vector))
    return array.array("f", (value / norm for value in vector))


def _dot(left: array.array, right: array.array) -> float:
    """兩個已正規化向量的內積（純 Python 版本）。"""
    return sum(a * b for a, b in zip(left, right, strict=False))
