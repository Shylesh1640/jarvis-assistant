"""Tests for the Phase 5 document management helpers (document_manager).

These exercise the Chroma-facing logic (pagination, source grouping, delete)
against a fake collection so no real vector store is needed.
"""
from __future__ import annotations

import pytest

from jarvis.memory import document_manager as dm


class _FakeCollection:
    """Minimal Chroma-like collection with pageable ``get`` + ``delete``."""

    def __init__(self, records: list[dict]) -> None:
        # records: [{"id", "document", "metadata"}]
        self.records = records

    def get(self, *, where=None, include=None, limit=None, offset=0, ids=None):
        recs = self.records
        if ids is not None:
            recs = [r for r in recs if r["id"] in ids]
        if where is not None:
            key, val = next(iter(where.items()))
            recs = [r for r in recs if (r["metadata"] or {}).get(key) == val]
        recs = recs[offset : (offset + limit) if limit else None]
        out = {"ids": [r["id"] for r in recs], "documents": [r["document"] for r in recs]}
        out["metadatas"] = [r["metadata"] for r in recs]
        return out

    def delete(self, *, ids=None, where=None):
        recs = self.records
        if ids is not None:
            self.records = [r for r in recs if r["id"] not in ids]
        elif where is not None:
            key, val = next(iter(where.items()))
            self.records = [r for r in recs if (r["metadata"] or {}).get(key) != val]


def _records():
    return [
        {"id": "c1", "document": "one", "metadata": {"source": "a.txt", "timestamp": "t1", "kind": "docs"}},
        {"id": "c2", "document": "two", "metadata": {"source": "a.txt", "timestamp": "t2", "kind": "docs"}},
        {"id": "c3", "document": "three", "metadata": {"source": "b.md", "timestamp": "t3", "kind": "docs"}},
        {"id": "m1", "document": "mem", "metadata": {"source": "session:s1", "kind": "memory"}},
    ]


@pytest.fixture
def fake_col(monkeypatch):
    col = _FakeCollection(_records())
    monkeypatch.setattr(dm, "get_collection", lambda: col)
    return col


def test_get_all_paginates(monkeypatch):
    # Page size 2 over 4 records -> two loops, all ids returned.
    monkeypatch.setattr(dm, "_PAGE_SIZE", 2)
    col = _FakeCollection(_records())
    monkeypatch.setattr(dm, "get_collection", lambda: col)
    data = dm._get_all(col)
    assert len(data["ids"]) == 4


def test_list_documents_groups_by_source(fake_col):
    docs = dm.list_documents()
    by_name = {d["source"]: d for d in docs}
    assert by_name["a.txt"]["chunk_count"] == 2
    assert by_name["a.txt"]["timestamp"] == "t2"  # newest wins
    assert by_name["b.md"]["chunk_count"] == 1
    # memory chunks are not part of the document corpus listing
    assert "session:s1" not in by_name


def test_get_document_returns_chunks(fake_col):
    doc = dm.get_document("a.txt")
    assert doc["chunk_count"] == 2
    assert doc["chunks"][0]["chunk_id"] == "c1"
    assert doc["chunks"][0]["text"] == "one"


def test_get_document_missing(fake_col):
    assert dm.get_document("zzz") is None


def test_delete_document(fake_col):
    assert dm.delete_document("a.txt") == 2
    assert [r["id"] for r in fake_col.records] == ["c3", "m1"]


def test_clear_documents_only_docs_kind(fake_col):
    assert dm.clear_documents() == 3
    assert [r["id"] for r in fake_col.records] == ["m1"]


def test_reindex_missing_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        dm.reindex_documents(str(tmp_path / "missing"))


def test_reindex_skips_unwanted_and_counts(tmp_path, monkeypatch):
    target = tmp_path / "docs"
    target.mkdir()
    (target / "a.txt").write_text("alpha", encoding="utf-8")
    (target / "b.md").write_text("beta", encoding="utf-8")
    (target / "skip.bin").write_bytes(b"\x00")

    calls: list[str] = []

    def fake_ingest_file(path, *, metadata=None):
        calls.append(path.name)
        return [f"id-{path.name}"]

    monkeypatch.setattr(dm, "ingest_file", fake_ingest_file)
    out = dm.reindex_documents(str(target))
    assert out["files"] == 2
    assert out["chunks"] == 2
    assert out["skipped"] == 0
    assert sorted(calls) == ["a.txt", "b.md"]