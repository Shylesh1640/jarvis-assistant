"""SQLAlchemy persistence for Jarvis.

Tables:
    sessions            one row per conversation (id, created_at, updated_at)
    messages            ordered chat turns within a session
    summaries           periodic conversation summaries (also mirrored to Chroma)
    tasks               background-job records for the /tasks endpoint
    pending_approvals   paused graph state awaiting user approval

The engine is created once from ``settings.postgres_dsn`` (Postgres) or a
local SQLite file when the DSN is empty. Tables are created on first use
via ``Base.metadata.create_all`` so the app is zero-migration for hacking.
"""
from jarvis.persistence.engine import (
    Base,
    SessionLocal,
    create_all,
    engine_from_settings,
    get_session,
)
from jarvis.persistence.models import (
    MessageRow,
    PendingApprovalRow,
    SessionRow,
    SummaryRow,
    TaskRow,
)
from jarvis.persistence.repo import (
    ApprovalRepo,
    MessageRepo,
    SessionRepo,
    SummaryRepo,
    TaskRepo,
    repos,
)

__all__ = [
    "Base",
    "SessionLocal",
    "create_all",
    "engine_from_settings",
    "get_session",
    "MessageRow",
    "PendingApprovalRow",
    "SessionRow",
    "SummaryRow",
    "TaskRow",
    "ApprovalRepo",
    "MessageRepo",
    "SessionRepo",
    "SummaryRepo",
    "TaskRepo",
    "repos",
]
