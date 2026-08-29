"""File-based storage for performance metrics (Phase 13).

Append-only JSONL files per day under ``./reports/performance/``.
Supports read/query functions and retention cleanup.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_DEFAULT_DIR = "./reports/performance"


def _day_path(day: str, base_dir: str | None = None) -> Path:
    base = Path(base_dir or _DEFAULT_DIR)
    return base / f"{day}.jsonl"


def ensure_dir(base_dir: str | None = None) -> Path:
    base = Path(base_dir or _DEFAULT_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base


def append_metric(record: dict[str, Any], base_dir: str | None = None) -> str:
    """Append a single metric record to today's JSONL file.

    Returns the path written to. A ``timestamp`` field is added if absent.
    """
    ensure_dir(base_dir)
    now = datetime.now(timezone.utc)
    if "timestamp" not in record:
        record["timestamp"] = now.isoformat()
    day = now.strftime("%Y-%m-%d")
    path = _day_path(day, base_dir)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return str(path)


def read_day(day: str, base_dir: str | None = None) -> list[dict[str, Any]]:
    """Read all records for a given day (``YYYY-MM-DD``)."""
    path = _day_path(day, base_dir)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def query(
    *,
    strategy: str | None = None,
    task_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    base_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Query stored metrics with optional filters."""
    base = ensure_dir(base_dir)
    files = sorted(base.glob("*.jsonl"))
    records: list[dict[str, Any]] = []
    for f in files:
        day_str = f.stem
        try:
            day_dt = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if since and day_dt < since.replace(hour=0, minute=0, second=0, microsecond=0):
            continue
        if until and day_dt > until.replace(hour=23, minute=59, second=59, microsecond=999999):
            continue
        for rec in read_day(day_str, base_dir):
            if strategy and rec.get("strategy") != strategy:
                continue
            if task_type and rec.get("task_type") != task_type:
                continue
            if since:
                rec_ts = rec.get("timestamp")
                if rec_ts:
                    try:
                        rec_dt = datetime.fromisoformat(rec_ts)
                        if rec_dt < since:
                            continue
                    except ValueError:
                        pass
            if until:
                rec_ts = rec.get("timestamp")
                if rec_ts:
                    try:
                        rec_dt = datetime.fromisoformat(rec_ts)
                        if rec_dt > until:
                            continue
                    except ValueError:
                        pass
            records.append(rec)
    return records


def cleanup(retention_days: int, base_dir: str | None = None) -> int:
    """Remove JSONL files older than ``retention_days``.

    Returns the number of files removed.
    """
    if retention_days <= 0:
        return 0
    base = ensure_dir(base_dir)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for f in base.glob("*.jsonl"):
        day_str = f.stem
        try:
            day_dt = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day_dt < cutoff:
            f.unlink()
            removed += 1
    return removed


def list_days(base_dir: str | None = None) -> list[str]:
    """Return all day strings (``YYYY-MM-DD``) with data, oldest first."""
    base = ensure_dir(base_dir)
    days: list[str] = []
    for f in base.glob("*.jsonl"):
        stem = f.stem
        try:
            datetime.strptime(stem, "%Y-%m-%d")
        except ValueError:
            continue
        days.append(stem)
    return sorted(days)
