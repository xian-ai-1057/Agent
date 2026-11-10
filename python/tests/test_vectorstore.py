"""內建向量資料庫的測試。"""

import sqlite3

import pytest

from harness import vectorstore


@pytest.fixture(params=["numpy", "python"])
def compute(request, monkeypatch):
    """兩種計算方式都要測：有 numpy 與純 Python。"""
    if request.param == "python":
        monkeypatch.setattr(vectorstore, "numpy", None)
    elif vectorstore.numpy is None:
        pytest.skip("沒有安裝 numpy")
    return request.param


async def test_search_ranks_by_cosine_similarity(compute):
    store = vectorstore.SqliteVectorStore()
    await store.add({"a": [1, 0], "b": [1, 1], "c": [0, 5]})

    results = await store.search([2, 0], ["a", "b", "c"], limit=2)

    assert [key for key, _ in results] == ["a", "b"]
    assert results[0][1] == pytest.approx(1.0)
    assert results[1][1] == pytest.approx(0.7071, abs=1e-3)


async def test_search_only_looks_at_given_keys(compute):
    store = vectorstore.SqliteVectorStore()
    await store.add({"a": [1, 0], "b": [0, 1]})

    results = await store.search([1, 0], ["b", "unknown"], limit=5)

    assert [key for key, _ in results] == ["b"]


async def test_search_with_nothing_to_compare_is_empty(compute):
    store = vectorstore.SqliteVectorStore()
    await store.add({"a": [1, 0]})

    assert await store.search([1, 0], [], limit=5) == []
    assert await store.search([1, 0], ["a"], limit=0) == []


async def test_vectors_with_different_dimensions_still_work(compute):
    store = vectorstore.SqliteVectorStore()
    await store.add({"short": [1, 0], "long": [1, 0, 0]})

    results = await store.search([1, 0, 0], ["long"], limit=1)

    assert results == [("long", pytest.approx(1.0))]


async def test_zero_vector_has_zero_similarity(compute):
    store = vectorstore.SqliteVectorStore()
    await store.add({"zero": [0, 0]})

    assert await store.search([1, 0], ["zero"], limit=1) == [("zero", 0.0)]


async def test_vectors_persist_in_sqlite_file(tmp_path):
    path = tmp_path / "db" / "vectors.sqlite3"
    store = vectorstore.SqliteVectorStore(path)
    await store.add({"a": [3, 4], "b": [1, 0]})

    reopened = vectorstore.SqliteVectorStore(path)

    assert len(reopened) == 2
    assert await reopened.missing(["a", "b", "c"]) == {"c"}
    [(key, score)] = await reopened.search([3, 4], ["a"], limit=1)
    assert (key, score) == ("a", pytest.approx(1.0))


async def test_retain_deletes_other_vectors_from_file(tmp_path):
    path = tmp_path / "vectors.sqlite3"
    store = vectorstore.SqliteVectorStore(path)
    await store.add({"a": [1], "b": [1], "c": [1]})

    await store.retain(["b"])

    assert len(store) == 1
    with sqlite3.connect(path) as connection:
        keys = [row[0] for row in connection.execute("SELECT key FROM vectors")]
    assert keys == ["b"]


async def test_add_overwrites_existing_key(compute):
    store = vectorstore.SqliteVectorStore()
    await store.add({"a": [1, 0]})
    await store.search([1, 0], ["a"], limit=1)  # 建立 numpy 矩陣

    await store.add({"a": [0, 1]})

    assert await store.search([0, 1], ["a"], limit=1) == [
        ("a", pytest.approx(1.0))
    ]
