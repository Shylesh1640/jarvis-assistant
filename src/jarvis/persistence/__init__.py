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
    EmailDraftRow,
    FeedbackRow,
    MessageRow,
    SessionRow,
    SummaryRow,
    TaskRow,
    TodoRow,
)
from jarvis.persistence.repo import (
    ApprovalRepo,
    EmailDraftRepo,
    FeedbackRepo,
    MessageRepo,
    SessionRepo,
    SummaryRepo,
    TaskRepo,
    TodoRepo,
    repos,
)

__all__ = [
    "Base",
    "SessionLocal",
    "create_all",
    "engine_from_settings",
    "get_session",
    "ApprovalRow",
    "EmailDraftRow",
    "FeedbackRow",
    "MessageRow",
    "SessionRow",
    "SummaryRow",
    "TaskRow",
    "TodoRow",
    "ApprovalRepo",
    "EmailDraftRepo",
    "FeedbackRepo",
    "MessageRepo",
    "SessionRepo",
    "SummaryRepo",
    "TaskRepo",
    "TodoRepo",
    "repos",
]
