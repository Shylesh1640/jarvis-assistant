"""Tests for ``jarvis.memory.store`` ingestion helpers.

We avoid contacting a real Chroma / Ollama by monkeypatching the
collection and the embedding function inside ``store``.  The chunking
helper (``_split_text``) is exercised for real because it's pure-python.
"""

from unittest.mock import MagicMock

import langchain_ollama
import pytest

from jarvis.memory import store as store_mod


@pytest.fixture
def fake_store(monkeypatch):
    """Replace the Chroma collection and embeddings with in-memory fakes."""
    collection = MagicMock()
    collection.add = MagicMock()
    collection.upsert = MagicMock()
    monkeypatch.setattr(store_mod, "get_collection", lambda: collection)

    emb = MagicMock()
    emb.embed_documents = lambda docs: [[0.0] for _ in docs]
    monkeypatch.setattr(store_mod, "get_embedding_function", lambda: emb)
    return collection


def test_ingest_text_single_chunk_uses_supplied_id(fake_store):
    ids = store_mod.ingest_text("short text", doc_id="doc-1")
    assert ids == ["doc-1"]


def test_ingest_text_multi_chunk_gives_unique_ids(fake_store):
    # Inject a tiny chunk size so a moderate text splits into many chunks.
    monkey = pytest.MonkeyPatch()
    monkey.setattr(store_mod, "_CHUNK_SIZE", 10)
    monkey.setattr(store_mod, "_CHUNK_OVERLAP", 2)
    try:
        ids = store_mod.ingest_text(
            "aaaaaa bbbbbb cccccc dddddd eeeeee", doc_id="doc-1"
        )
    finally:
        monkey.undo()

    assert len(ids) > 1, "expected at least 2 chunks"
    # No duplicate ids: Chroma would reject on .add() otherwise.
    assert len(set(ids)) == len(ids)
    # Namespaced from the supplied parent id.
    assert all(i.startswith("doc-1#") for i in ids)


def test_ingest_text_multi_chunk_metadata_carries_chunk_index(fake_store):
    monkey = pytest.MonkeyPatch()
    monkey.setattr(store_mod, "_CHUNK_SIZE", 10)
    monkey.setattr(store_mod, "_CHUNK_OVERLAP", 2)
    try:
        store_mod.ingest_text("aaaaaa bbbbbb cccccc dddddd eeeeee", doc_id="doc-1")
    finally:
        monkey.undo()

    fake_store.add.assert_called_once()
    metas = fake_store.add.call_args.kwargs["metadatas"]
    assert [m["chunk_index"] for m in metas] == list(range(len(metas)))


def test_ingest_text_no_explicit_id_uses_random_uuids(fake_store):
    ids = store_mod.ingest_text("short text")
    assert len(ids) == 1
    # 32-char uuid4 hex string.
    assert len(ids[0]) == 32


def test_ingest_documents_splits_and_upserts(fake_store, monkeypatch):
    # Patch the imports the function uses for type instantiation.
    import langchain_core.documents as lcd

    monkeypatch.setattr(langchain_ollama, "ChatOllama", MagicMock(), raising=False)
    docs = [
        lcd.Document(
            page_content="hello world this is a doc", metadata={"source": "f.txt"}
        )
    ]

    ids = store_mod.ingest_documents(docs)
    fake_store.upsert.assert_called_once()
    assert len(ids) == len(fake_store.upsert.call_args.kwargs["ids"])


def test_ingest_documents_empty_returns_empty(fake_store):
    assert store_mod.ingest_documents([]) == []
    fake_store.upsert.assert_not_called()


def test_split_text_short_returns_single_chunk():
    assert store_mod._split_text("short") == ["short"]


def test_split_text_long_returns_multiple():
    monkey = pytest.MonkeyPatch()
    monkey.setattr(store_mod, "_CHUNK_SIZE", 20)
    monkey.setattr(store_mod, "_CHUNK_OVERLAP", 0)
    try:
        chunks = store_mod._split_text("a" * 100)
        assert len(chunks) >= 2
    finally:
        monkey.undo()
