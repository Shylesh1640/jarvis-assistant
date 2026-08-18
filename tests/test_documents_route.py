"""Tests for the document upload + ingest-folder routes."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app


@pytest.fixture
def client(monkeypatch):
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


def test_documents_count_returns_number(client, monkeypatch):
    mock_col = MagicMock()
    mock_col.count.return_value = 7
    from jarvis.api.routes import documents as docs_mod

    monkeypatch.setattr(docs_mod, "get_collection", lambda: mock_col)
    r = client.get("/documents/count")
    assert r.status_code == 200
    assert r.json() == {"count": 7}


def test_upload_text_file(client, monkeypatch):
    ingested: list = []
    monkeypatch.setattr(
        "jarvis.api.routes.documents.ingest_documents",
        lambda docs: ingested.extend([doc.metadata["source"] for doc in docs]) or ["id1"],
    )
    files = [("files", ("notes.txt", b"hello world\n", "text/plain"))]
    r = client.post("/documents/upload", files=files)
    assert r.status_code == 200
    data = r.json()
    assert data["files"] == ["notes.txt"]
    assert data["chunks"] == 1
    assert ingested == ["notes.txt"]


def test_upload_rejects_unsupported_type(client):
    files = [("files", ("evil.exe", b"\x00\x01", "application/octet-stream"))]
    r = client.post("/documents/upload", files=files)
    assert r.status_code == 415


def test_upload_rejects_empty_file(client):
    files = [("files", ("empty.txt", b"", "text/plain"))]
    r = client.post("/documents/upload", files=files)
    assert r.status_code == 400


def test_upload_rejects_no_files(client):
    # FastAPI requires at least one UploadFile in the list, so an empty
    # body fails validation before the handler's own 400 guard.
    r = client.post("/documents/upload", files=[])
    assert r.status_code == 422


def test_upload_rejects_oversized(client, monkeypatch):
    monkeypatch.setattr(
        "jarvis.api.routes.documents._MAX_UPLOAD_BYTES", 4
    )
    files = [("files", ("big.txt", b"abcdef", "text/plain"))]
    r = client.post("/documents/upload", files=files)
    assert r.status_code == 413


def test_upload_rejects_non_utf8_text(client):
    files = [("files", ("bad.txt", b"\xff\xfe\x00\x01", "text/plain"))]
    r = client.post("/documents/upload", files=files)
    assert r.status_code == 422


def test_upload_pdf_routes_to_ingest_file(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    added: list = []

    def fake_ingest_file(path, *, metadata=None):
        added.append((path, metadata))
        return ["pdf-chunk-1"]

    monkeypatch.setattr(docs_mod, "ingest_file", fake_ingest_file)
    files = [("files", ("doc.pdf", b"%PDF-1.4 fake-body", "application/pdf"))]
    r = client.post("/documents/upload", files=files)
    assert r.status_code == 200
    data = r.json()
    assert data["files"] == ["doc.pdf"]
    assert data["ids"] == ["pdf-chunk-1"]
    # binary path was used (temp file + metadata)
    assert added and added[0][1]["source"] == "doc.pdf"


def test_upload_pdf_unreadable_returns_500(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    def fail_ingest(path, *, metadata=None):
        raise RuntimeError("corrupt pdf")

    monkeypatch.setattr(docs_mod, "ingest_file", fail_ingest)
    files = [("files", ("broken.pdf", b"junk", "application/pdf"))]
    r = client.post("/documents/upload", files=files)
    assert r.status_code == 500


def test_ingest_folder_scans(monkeypatch, client, tmp_path):
    target = tmp_path / "docs"
    target.mkdir()
    (target / "a.txt").write_text("alpha", encoding="utf-8")
    (target / "b.md").write_text("beta", encoding="utf-8")
    (target / "ignore.bin").write_bytes(b"\x00")

    ingested_files: list = []

    def _ingest_file(path, *, metadata=None):
        ingested_files.append(str(path))
        return [f"id-{len(ingested_files)}"]

    monkeypatch.setattr("jarvis.api.routes.documents.ingest_file", _ingest_file)
    r = client.post("/documents/ingest-folder", params={"folder": str(target)})
    assert r.status_code == 200
    data = r.json()
    assert data["files"] == 2
    assert len(data["ids"]) == 2
    # .bin skipped
    assert all(".bin" not in f for f in ingested_files)


def test_ingest_folder_missing_returns_404(client):
    r = client.post(
        "/documents/ingest-folder", params={"folder": "/no/such/place"}
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Phase 5 :: document management
# ---------------------------------------------------------------------------


def test_documents_list_groups_by_source(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    fake = [
        {"source": "b.md", "chunk_count": 3, "timestamp": "t2"},
        {"source": "a.txt", "chunk_count": 1, "timestamp": "t1"},
    ]
    monkeypatch.setattr(docs_mod, "list_documents", lambda: fake)
    r = client.get("/documents")
    assert r.status_code == 200
    assert r.json()["documents"] == fake


def test_documents_get_returns_chunks(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    fake = {"source": "a.txt", "chunk_count": 1, "chunks": [{"chunk_id": "c1", "text": "hi"}]}
    monkeypatch.setattr(docs_mod, "get_document", lambda src: fake)
    r = client.get("/documents/a.txt")
    assert r.status_code == 200
    assert r.json()["chunks"][0]["chunk_id"] == "c1"


def test_documents_get_missing_404(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    monkeypatch.setattr(docs_mod, "get_document", lambda src: None)
    r = client.get("/documents/nope.txt")
    assert r.status_code == 404


def test_documents_delete_requires_confirmation(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    monkeypatch.setattr(docs_mod, "delete_document", lambda src: 2)
    r = client.delete("/documents/a.txt")
    assert r.status_code == 400
    assert r.json()["error"] == "confirmation_required"


def test_documents_delete_with_confirmation(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    monkeypatch.setattr(docs_mod, "delete_document", lambda src: 2)
    r = client.delete("/documents/a.txt?confirm=1")
    assert r.status_code == 200
    assert r.json() == {"deleted": "a.txt", "chunks": 2}


def test_documents_delete_missing_404(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    monkeypatch.setattr(docs_mod, "delete_document", lambda src: 0)
    r = client.delete("/documents/nope.txt?confirm=1")
    assert r.status_code == 404


def test_documents_clear_requires_confirmation(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    monkeypatch.setattr(docs_mod, "clear_documents", lambda: 5)
    r = client.delete("/documents")
    assert r.status_code == 400


def test_documents_clear_with_confirmation(client, monkeypatch):
    import jarvis.api.routes.documents as docs_mod

    monkeypatch.setattr(docs_mod, "clear_documents", lambda: 5)
    r = client.delete("/documents?confirm=1")
    assert r.status_code == 200
    assert r.json() == {"cleared": 5}


def test_documents_reindex(client, monkeypatch, tmp_path):
    import jarvis.api.routes.documents as docs_mod

    target = tmp_path / "docs"
    target.mkdir()
    monkeypatch.setattr(
        docs_mod,
        "reindex_documents",
        lambda folder: {"files": 2, "chunks": 4, "skipped": 0},
    )
    r = client.post("/documents/reindex", params={"folder": str(target)})
    assert r.status_code == 200
    assert r.json()["chunks"] == 4


def test_documents_reindex_missing_folder_404(client):
    r = client.post("/documents/reindex", params={"folder": "/no/such/place"})
    assert r.status_code == 404
