"""SQLAlchemy persistence for Jarvis.

Tables:
    sessions            one row per conversation (id, user_id, token, timestamps)
    messages            ordered chat turns within a session
    summaries           periodic conversation summaries (also mirrored to Chroma)
    tasks               background-job records for the /tasks endpoint
    approvals           durable pending-approval records (survive restarts)

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
    ApprovalRow,
    FeedbackRow,
    MessageRow,
    SessionRow,
    SummaryRow,
    TaskRow,
)
from jarvis.persistence.repo import (
    ApprovalRepo,
    FeedbackRepo,
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
    "ApprovalRow",
    "FeedbackRow",
    "MessageRow",
    "SessionRow",
    "SummaryRow",
    "TaskRow",
    "ApprovalRepo",
    "FeedbackRepo",
    "MessageRepo",
    "SessionRepo",
    "SummaryRepo",
    "TaskRepo",
    "repos",
]
