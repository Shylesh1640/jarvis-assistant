"""Tests for Phase 7 backup tooling."""
from __future__ import annotations

import json
import sqlite3

from jarvis.backup import create_backup, delete_backup, list_backups, verify_backup
from jarvis.config.settings import settings


def _make_sqlite(path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('hello')")
    conn.commit()
    conn.close()


def test_create_and_verify_roundtrip(monkeypatch, tmp_path):
    db = tmp_path / "jarvis.db"
    _make_sqlite(db)
    vstore = tmp_path / "vector_store"
    vstore.mkdir()
    (vstore / "chunk.bin").write_bytes(b"0123456789")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "manual.md").write_text("# Manual", encoding="utf-8")

    monkeypatch.setattr(settings, "sqlite_path", str(db))
    monkeypatch.setattr(settings, "vector_db_path", str(vstore))
    monkeypatch.setattr(settings, "docs_folder", str(docs))
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "backup_include_documents", True)

    result = create_backup(include_documents=True)
    assert result.ok is True, result.message
    assert result.path.startswith(str(tmp_path / "backups"))

    verify = verify_backup(result.path)
    assert verify.ok is True, verify.message
    assert "jarvis.db" in [f.split("\\")[-1] for f in verify.files]

    manifest = json.loads((tmp_path / "backups" / result.path.split("\\")[-1] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["include_documents"] is True
    assert manifest["file_count"] >= 3


def test_manifest_never_contains_secrets(monkeypatch, tmp_path):
    db = tmp_path / "jarvis.db"
    _make_sqlite(db)
    monkeypatch.setattr(settings, "sqlite_path", str(db))
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://user:supersecret@db:5432/jarvis")

    result = create_backup()
    manifest = json.loads((tmp_path / "backups" / result.path.split("\\")[-1] / "manifest.json").read_text(encoding="utf-8"))
    dumped = json.dumps(manifest).lower()
    assert "supersecret" not in dumped
    assert "openrouter" not in dumped
    assert "password" not in dumped
    assert "db:5432" not in dumped
    assert "user:" not in dumped


def test_verify_detects_tampering(monkeypatch, tmp_path):
    db = tmp_path / "jarvis.db"
    _make_sqlite(db)
    monkeypatch.setattr(settings, "sqlite_path", str(db))
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))

    result = create_backup()
    db_file = tmp_path / "backups" / result.path.split("\\")[-1] / "jarvis.db"
    with open(db_file, "ab") as fh:
        fh.write(b"tampered")
    verify = verify_backup(result.path)
    assert verify.ok is False


def test_delete_refuses_outside_root(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))
    (tmp_path / "backups").mkdir()
    outsider = tmp_path / "important_data"
    outsider.mkdir()
    result = delete_backup("important_data", backup_root=str(tmp_path / "backups"))
    assert result.ok is False
    assert outsider.is_dir()

    traversal = delete_backup("../important_data", backup_root=str(tmp_path / "backups"))
    assert traversal.ok is False
    assert "refusing" in traversal.message


def test_delete_and_list(monkeypatch, tmp_path):
    db = tmp_path / "jarvis.db"
    _make_sqlite(db)
    monkeypatch.setattr(settings, "sqlite_path", str(db))
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))

    r1 = create_backup()
    r2 = create_backup()
    entries = list_backups(backup_root=str(tmp_path / "backups"))
    assert len(entries) == 2
    assert entries[0]["name"] == r2.path.split("\\")[-1]
    assert entries[0]["name"] > entries[1]["name"]

    deleted = delete_backup(r1.path.split("\\")[-1], backup_root=str(tmp_path / "backups"))
    assert deleted.ok is True
    assert len(list_backups(backup_root=str(tmp_path / "backups"))) == 1


def test_postgres_without_pg_dump_warns(monkeypatch, tmp_path):
    import shutil

    monkeypatch.setattr(settings, "postgres_dsn", "postgresql+psycopg://u:p@db:5432/jarvis")
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "pg_dump" else shutil.which(name))

    result = create_backup()
    assert result.ok is False
    assert any("pg_dump" in w for w in result.warnings)
    assert not (tmp_path / "backups" / result.path.split("\\")[-1] / "jarvis.db").exists()


def test_cli_create_verify_list_delete(monkeypatch, tmp_path, capsys):
    from jarvis.cli.backup import main

    db = tmp_path / "jarvis.db"
    _make_sqlite(db)
    monkeypatch.setattr(settings, "sqlite_path", str(db))
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))

    assert main(["create", "--dir", str(tmp_path / "backups")]) == 0
    captured = capsys.readouterr().out
    assert "Backup at:" in captured

    assert main(["verify", "--dir", str(tmp_path / "backups")]) == 0
    captured = capsys.readouterr().out
    assert "backup OK" in captured

    assert main(["list", "--dir", str(tmp_path / "backups")]) == 0
    captured = capsys.readouterr().out
    assert "backup_" in captured

    entries = list_backups(backup_root=str(tmp_path / "backups"))
    assert main(["delete", "--dir", str(tmp_path / "backups"), entries[0]["name"]]) == 0
    assert list_backups(backup_root=str(tmp_path / "backups")) == []