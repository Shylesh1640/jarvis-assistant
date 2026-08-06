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


def test_ingest_folder_scans(monkeypatch, client, tmp_path):
    target = tmp_path / "docs"
    target.mkdir()
    (target / "a.txt").write_text("alpha", encoding="utf-8")
    (target / "b.md").write_text("beta", encoding="utf-8")
    (target / "ignore.bin").write_bytes(b"\x00")

    ids: list = []

    def _ingest(docs):
        ids.extend([d.metadata["source"] for d in docs])
        return [f"id-{i}" for i in range(len(docs))]

    monkeypatch.setattr("jarvis.api.routes.documents.ingest_documents", _ingest)
    r = client.post("/documents/ingest-folder", params={"folder": str(target)})
    assert r.status_code == 200
    data = r.json()
    assert data["files"] == 2
    assert set(data["ids"]) == {"id-0", "id-1"}
    # .bin skipped
    assert all(not s.endswith(".bin") for s in ids)


def test_ingest_folder_missing_returns_404(client):
    r = client.post(
        "/documents/ingest-folder", params={"folder": "/no/such/place"}
    )
    assert r.status_code == 404
