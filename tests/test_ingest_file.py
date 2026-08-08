"""Tests for multi-format extraction and file ingestion (PDF / DOCX / TXT)."""
from __future__ import annotations

from pathlib import Path

from jarvis.memory import store as store_mod


def test_extract_text_txt(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("hello world", encoding="utf-8")
    text, sections = store_mod.extract_text_from_file(p)
    assert text == "hello world"
    assert sections == [{"page": 1, "section": "file", "text": "hello world"}]


def test_extract_text_md_reads_utf8(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("# Title\nbody text", encoding="utf-8")
    text, _ = store_mod.extract_text_from_file(p)
    assert "# Title" in text


def test_extract_text_pdf_uses_pypdf(tmp_path, monkeypatch):
    p = tmp_path / "book.pdf"
    p.write_bytes(b"%PDF-1.4 fake")

    class FakePage:
        def extract_text(self):
            return "Page one content"

    class FakeReader:
        pages = [FakePage(), FakePage()]

    monkeypatch.setitem(
        __import__("sys").modules,
        "pypdf",
        type("F", (), {"PdfReader": lambda *a: FakeReader()}),
    )
    text, sections = store_mod.extract_text_from_file(p)
    assert "Page one content" in text
    assert len(sections) == 2
    assert sections[0] == {"page": 1, "section": "page-1", "text": "Page one content"}


def test_extract_text_pdf_failure_returns_empty(tmp_path, monkeypatch):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"garbage")

    def boom(*a, **k):
        raise RuntimeError("corrupt")

    monkeypatch.setitem(
        __import__("sys").modules,
        "pypdf",
        type("F", (), {"PdfReader": staticmethod(boom)}),
    )
    text, sections = store_mod.extract_text_from_file(p)
    assert text == ""
    assert sections == []


def test_extract_text_docx_uses_docx2txt(tmp_path, monkeypatch):
    p = tmp_path / "report.docx"
    p.write_bytes(b"PK fake docx")

    fake_docx = type("D", (), {"process": staticmethod(lambda _p: "Document body text")})
    monkeypatch.setitem(__import__("sys").modules, "docx2txt", fake_docx)
    text, sections = store_mod.extract_text_from_file(p)
    assert text == "Document body text"
    assert sections[0]["section"] == "docx-body"


def test_ingest_file_splits_and_kind_defaults(monkeypatch):
    captured = []

    monkeypatch.setattr(
        store_mod,
        "extract_text_from_file",
        lambda p: ("some body text", [{"page": 1, "section": "page-1", "text": "some body text"}]),
    )
    monkeypatch.setattr(store_mod, "ingest_documents", lambda docs: captured.extend(docs) or ["c1"])

    ids = store_mod.ingest_file(Path("x.pdf"), metadata={"kind": "docs"})
    assert ids == ["c1"]
    assert len(captured) == 1
    assert captured[0].metadata["source"] == "x.pdf"
    assert captured[0].metadata["filename"] == "x.pdf"
    assert captured[0].metadata["page"] == 1
    assert captured[0].metadata["section"] == "page-1"
    assert captured[0].metadata["kind"] == "docs"


def test_ingest_file_returns_empty_when_no_text(monkeypatch):
    monkeypatch.setattr(store_mod, "extract_text_from_file", lambda p: ("", []))
    monkeypatch.setattr(store_mod, "ingest_documents", lambda docs: ["never"])
    assert store_mod.ingest_file(Path("empty.txt")) == []


def test_ingest_documents_kind_defaults_to_docs(monkeypatch):
    from unittest.mock import MagicMock

    collection = MagicMock()
    monkeypatch.setattr(store_mod, "get_collection", lambda: collection)
    emb = MagicMock()
    emb.embed_documents = lambda docs: [[0.0] for _ in docs]
    monkeypatch.setattr(store_mod, "get_embedding_function", lambda: emb)

    from langchain_core.documents import Document

    store_mod.ingest_documents([Document(page_content="hello world", metadata={"source": "s.txt"})])
    metas = collection.upsert.call_args.kwargs["metadatas"]
    assert all(m["kind"] == "docs" for m in metas)


def test_ingest_documents_preserves_explicit_kind(monkeypatch):
    from unittest.mock import MagicMock

    collection = MagicMock()
    monkeypatch.setattr(store_mod, "get_collection", lambda: collection)
    emb = MagicMock()
    emb.embed_documents = lambda docs: [[0.0] for _ in docs]
    monkeypatch.setattr(store_mod, "get_embedding_function", lambda: emb)

    from langchain_core.documents import Document

    store_mod.ingest_documents([
        Document(page_content="def foo(): pass", metadata={"source": "a.py", "kind": "code"})
    ])
    metas = collection.upsert.call_args.kwargs["metadatas"]
    assert all(m["kind"] == "code" for m in metas)