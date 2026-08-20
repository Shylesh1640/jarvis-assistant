"""Tests for Phase 8 :: email drafts.

Covers the EmailDraftRow/EmailDraftRepo persistence, the /email-drafts routes
(local CRUD, session isolation, recipient validation, confirm-gated delete
and send, structured "not configured" when no provider), the LangChain tools,
and risk/registry wiring. Uses a mock email provider — no network.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.api.main import app
from jarvis.email import EMAIL_PROVIDERS
from jarvis.email.base import register_provider
from jarvis.guardrails import risk as risk_module
from jarvis.persistence import create_all, repos
from jarvis.persistence.engine import reset_engine_for_tests
from jarvis.tools import registry


class MockEmailProvider:
    sent: list[dict] = []

    def __init__(self, settings=None) -> None:
        self._settings = settings

    @classmethod
    def reset(cls) -> None:
        cls.sent = []

    def health_check(self) -> dict:
        return {"ok": True, "detail": "mock email"}

    def send(self, *, subject, recipients, body=None, from_address=None):
        self.sent.append(
            {"subject": subject, "recipients": recipients, "body": body, "from": from_address}
        )
        return f"msg-{len(self.sent)}"


@pytest.fixture
def fresh_db(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.settings.postgres_dsn", "")
    monkeypatch.setattr("jarvis.config.settings.settings.sqlite_path", ":memory:")
    reset_engine_for_tests()
    create_all()
    yield
    reset_engine_for_tests()


@pytest.fixture
def email_on(monkeypatch):
    register_provider("mock", MockEmailProvider)
    MockEmailProvider.reset()
    monkeypatch.setattr("jarvis.config.settings.settings.email_enabled", True)
    monkeypatch.setattr("jarvis.config.settings.settings.email_provider", "mock")
    monkeypatch.setattr("jarvis.config.settings.settings.email_default_from", "me@example.com")
    yield
    EMAIL_PROVIDERS.pop("mock", None)


@pytest.fixture
def client(fresh_db):
    routes.chat._sessions.clear()
    routes.chat._pending_approvals.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Repo
# ---------------------------------------------------------------------------


def test_draft_repo_crud_and_scoping(fresh_db):
    repos.email_drafts.create("d1", "s1", subject="Hi", recipients=["a@b.com"])
    repos.email_drafts.create("d2", "s2", subject="Other", recipients=["c@d.com"])

    assert repos.email_drafts.get("s1", "d1").subject == "Hi"
    assert repos.email_drafts.get("s2", "d1") is None
    assert [r.draft_id for r in repos.email_drafts.list_for_session("s1")] == ["d1"]

    updated = repos.email_drafts.update("s1", "d1", subject="Hello", recipients=["a@b.com", "e@f.com"])
    assert updated.subject == "Hello"
    assert updated.recipients == ["a@b.com", "e@f.com"]

    sent = repos.email_drafts.mark_sent("s1", "d1")
    assert sent.status == "sent"
    assert sent.sent_at is not None

    assert repos.email_drafts.delete("s1", "d1") is True
    assert repos.email_drafts.delete("s1", "d1") is False
    assert repos.email_drafts.get("s1", "d1") is None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_draft_route_roundtrip(client):
    created = client.post(
        "/email-drafts",
        json={"subject": "Weekly update", "recipients": ["team@example.com"], "body": "Hello"},
    ).json()
    assert created["status"] == "draft"
    did = created["draft_id"]

    got = client.get(f"/email-drafts/{did}").json()
    assert got["subject"] == "Weekly update"

    listed = client.get("/email-drafts").json()
    assert listed["count"] == 1

    updated = client.patch(f"/email-drafts/{did}", json={"subject": "Updated"}).json()
    assert updated["subject"] == "Updated"

    assert client.delete(f"/email-drafts/{did}").status_code == 400  # needs confirm
    assert client.delete(f"/email-drafts/{did}", params={"confirm": 1}).status_code == 200
    assert client.get(f"/email-drafts/{did}").status_code == 404


def test_draft_route_validation_and_isolation(client):
    r = client.post("/email-drafts", json={"subject": "", "recipients": ["a@b.com"]})
    assert r.status_code == 422
    r = client.post("/email-drafts", json={"subject": "x", "recipients": []})
    assert r.status_code == 422
    r = client.post("/email-drafts", json={"subject": "x", "recipients": ["not-an-email"]})
    assert r.status_code == 422

    client.post("/email-drafts", json={"session_id": "sA", "subject": "A", "recipients": ["a@b.com"]})
    client.post("/email-drafts", json={"session_id": "sB", "subject": "B", "recipients": ["a@b.com"]})
    listed_a = client.get("/email-drafts", params={"session_id": "sA"}).json()
    assert [i["subject"] for i in listed_a["items"]] == ["A"]


def test_draft_send_not_configured(client):
    created = client.post(
        "/email-drafts", json={"subject": "x", "recipients": ["a@b.com"]}
    ).json()
    did = created["draft_id"]
    r = client.post(f"/email-drafts/{did}/send", params={"confirm": 1})
    assert r.status_code == 503
    assert r.json()["error"] == "email_not_configured"
    # draft is untouched
    assert client.get(f"/email-drafts/{did}").json()["status"] == "draft"


def test_draft_send_roundtrip(client, email_on):
    created = client.post(
        "/email-drafts",
        json={"subject": "Ships today", "recipients": ["ship@example.com"], "body": "Body"},
    ).json()
    did = created["draft_id"]

    r = client.post(f"/email-drafts/{did}/send")
    assert r.status_code == 400  # confirm required

    r = client.post(f"/email-drafts/{did}/send", params={"confirm": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "sent"
    assert body["message_id"] == "msg-1"
    assert MockEmailProvider.sent[0]["subject"] == "Ships today"
    assert client.get(f"/email-drafts/{did}").json()["status"] == "sent"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_draft_tools_roundtrip(fresh_db):
    from jarvis.tools.general.email import (
        create_email_draft,
        delete_email_draft,
        list_email_drafts,
        send_email_draft,
        update_email_draft,
    )

    out = create_email_draft.invoke(
        {"subject": "Draft me", "recipients": ["a@b.com"], "body": "hi"}
    )
    assert "Created email draft" in out
    did = out.split(" ")[3].rstrip(":")

    assert "Draft me" in list_email_drafts.invoke({"session_id": "default"})
    assert "Updated" in update_email_draft.invoke({"draft_id": did, "subject": "Renamed"})
    assert "Renamed" in list_email_drafts.invoke({})

    # No provider configured here -> structured not-configured, no network.
    out = send_email_draft.invoke({"draft_id": did})
    assert "not configured" in out

    assert "Deleted" in delete_email_draft.invoke({"draft_id": did})
    assert "not found" in delete_email_draft.invoke({"draft_id": did})


def test_draft_tool_send_with_provider(fresh_db, email_on):
    from jarvis.tools.general.email import (
        create_email_draft,
        send_email_draft,
    )

    out = create_email_draft.invoke(
        {"subject": "Sends", "recipients": ["a@b.com"], "session_id": "s1"}
    )
    did = out.split(" ")[3].rstrip(":")
    result = send_email_draft.invoke({"draft_id": did, "session_id": "s1"})
    assert "Sent email draft" in result
    assert MockEmailProvider.sent[0]["subject"] == "Sends"
    # Wrong session cannot see/send the draft.
    assert "not found" in send_email_draft.invoke({"draft_id": did, "session_id": "s2"})


def test_draft_tool_validation(fresh_db):
    from jarvis.tools.general.email import create_email_draft

    assert "Error" in create_email_draft.invoke({"subject": "", "recipients": ["a@b.com"]})
    assert "Error" in create_email_draft.invoke({"subject": "x", "recipients": ["nope"]})
    assert "Error" in create_email_draft.invoke({"subject": "x", "recipients": []})


def test_draft_tool_risk_and_registry(fresh_db):
    assert risk_module.check_tool_risk("list_email_drafts", {}) == "low"
    assert risk_module.check_tool_risk("create_email_draft", {}) == "medium"
    assert risk_module.check_tool_risk("update_email_draft", {}) == "medium"
    assert risk_module.check_tool_risk("delete_email_draft", {}) == "high"
    assert risk_module.check_tool_risk("send_email_draft", {}) == "high"

    general = {t.name for t in registry.GENERAL_TOOLS}
    assert "list_email_drafts" in general
    gated = {t.name for t in registry.APPROVAL_GATED_TOOLS}
    assert {
        "create_email_draft",
        "update_email_draft",
        "delete_email_draft",
        "send_email_draft",
    } <= gated