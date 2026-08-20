"""Tests for Phase 7 network security (CORS, trusted hosts, headers, /ready)."""
from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from jarvis.api import security as sec
from jarvis.api.main import app
from jarvis.config.settings import Settings
from jarvis.persistence import create_all
from jarvis.persistence.engine import reset_engine_for_tests


def _settings(**overrides) -> Settings:
    defaults = dict(
        deployment_profile="local",
        jarvis_host="127.0.0.1",
        jarvis_port=8000,
        jarvis_allowed_origins="",
        jarvis_trusted_hosts="localhost,127.0.0.1",
        jarvis_behind_reverse_proxy=False,
        jarvis_force_https=False,
        jarvis_debug=False,
        jarvis_expose_traces=True,
        jarvis_backup_enabled=False,
        backup_dir="./backups",
        require_session_token=False,
        openrouter_api_key="",
        cloud_daily_budget_usd=0.0,
        cloud_max_request_cost_usd=0.0,
        cloud_max_session_cost_usd=0.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# CORS rules
# ---------------------------------------------------------------------------


def test_local_cors_default_empty():
    s = _settings()
    assert sec.cors_origins(s) == []
    assert sec.cors_enabled(s) is False


def test_cors_origins_parsed():
    s = _settings(jarvis_allowed_origins="http://a.test, https://b.test")
    assert sec.cors_origins(s) == ["http://a.test", "https://b.test"]
    assert sec.cors_enabled(s) is True


def test_production_rejects_wildcard_cors():
    from jarvis.config.deployment import validate_deployment

    s = _settings(
        deployment_profile="production",
        jarvis_allowed_origins="*",
        require_session_token=True,
        jarvis_force_https=True,
        jarvis_backup_enabled=True,
        jarvis_expose_traces=False,
    )
    warnings = validate_deployment(s)
    assert any("wildcard CORS" in w for w in warnings)


def test_production_requires_allowed_origins():
    from jarvis.config.deployment import validate_deployment

    s = _settings(
        deployment_profile="production",
        jarvis_allowed_origins="",
        require_session_token=True,
        jarvis_force_https=True,
        jarvis_backup_enabled=True,
        jarvis_expose_traces=False,
    )
    warnings = validate_deployment(s)
    assert any("allowed origins are required" in w for w in warnings)


def test_cors_middleware_allows_explicit_origin():
    s = _settings(
        jarvis_allowed_origins="http://localhost:8501",
        deployment_profile="single_host",
        require_session_token=True,
    )
    fresh = FastAPI()

    @fresh.get("/ping")
    def ping():
        return {"ok": True}

    sec.install_security_stack(fresh, s)
    client = TestClient(fresh, base_url="http://localhost")
    r = client.get("/ping", headers={"Origin": "http://localhost:8501"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8501"


def test_cors_middleware_blocks_unlisted_origin():
    s = _settings(
        jarvis_allowed_origins="http://localhost:8501",
        deployment_profile="single_host",
        require_session_token=True,
    )
    fresh = FastAPI()

    @fresh.get("/ping")
    def ping():
        return {"ok": True}

    sec.install_security_stack(fresh, s)
    client = TestClient(fresh, base_url="http://localhost")
    r = client.get("/ping", headers={"Origin": "http://evil.test"})
    assert r.headers.get("access-control-allow-origin") is None


# ---------------------------------------------------------------------------
# trusted hosts
# ---------------------------------------------------------------------------


def test_trusted_hosts_default_restricted():
    s = _settings()
    hosts = sec.trusted_hosts(s)
    assert "localhost" in hosts
    assert "127.0.0.1" in hosts
    assert "*" not in hosts


def test_trusted_host_unknown_rejected():
    reset_engine_for_tests()
    create_all()
    client = TestClient(app, base_url="http://evil.example.com")
    r = client.get("/health")
    assert r.status_code == 400


def test_trusted_host_localhost_allowed():
    reset_engine_for_tests()
    create_all()
    client = TestClient(app, base_url="http://localhost")
    r = client.get("/health")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# security headers
# ---------------------------------------------------------------------------


def test_security_headers_present():
    reset_engine_for_tests()
    create_all()
    client = TestClient(app)
    r = client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "default-src 'none'" in r.headers.get("content-security-policy", "")
    assert "strict-transport-security" not in r.headers


def test_hsts_only_when_forced():
    s = _settings(jarvis_force_https=True)
    assert "Strict-Transport-Security" in sec.security_headers(s)
    s2 = _settings(jarvis_force_https=False)
    assert "Strict-Transport-Security" not in sec.security_headers(s2)


# ---------------------------------------------------------------------------
# reverse proxy awareness
# ---------------------------------------------------------------------------


def test_proxy_middleware_honours_forwarded_for():
    s = _settings(jarvis_behind_reverse_proxy=True)
    fresh = FastAPI()

    @fresh.get("/whoami")
    def whoami(request: Request):
        return {"ip": request.client.host if request.client else "none"}

    sec.install_security_stack(fresh, s)
    client = TestClient(fresh, base_url="http://localhost")
    r = client.get("/whoami", headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    assert r.json()["ip"] == "203.0.113.9"


def test_proxy_middleware_disabled_by_default():
    s = _settings(jarvis_behind_reverse_proxy=False)
    fresh = FastAPI()

    @fresh.get("/whoami")
    def whoami(request: Request):
        return {"ip": request.client.host if request.client else "none"}

    sec.install_security_stack(fresh, s)
    client = TestClient(fresh, base_url="http://localhost")
    r = client.get("/whoami", headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.json()["ip"] != "203.0.113.9"


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------


def test_ready_not_ready_when_ollama_down(monkeypatch):
    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr(
        "jarvis.models.runtime_diagnostics.check_ollama_reachable",
        lambda *a, **k: (False, ["Ollama unreachable"]),
    )
    client = TestClient(app)
    r = client.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert "database" in body["checks"]
    assert "deployment" in body["checks"]
    assert "ollama" in body["checks"]
    assert body["checks"]["ollama"]["ok"] is False


def test_ready_ready_when_all_ok(monkeypatch):
    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr(
        "jarvis.models.runtime_diagnostics.check_ollama_reachable",
        lambda *a, **k: (True, []),
    )
    client = TestClient(app)
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["deployment"]["ok"] is True
    assert body["checks"]["ollama"]["ok"] is True


def test_ready_no_secret_leakage(monkeypatch):
    reset_engine_for_tests()
    create_all()
    monkeypatch.setattr(
        "jarvis.models.runtime_diagnostics.check_ollama_reachable",
        lambda *a, **k: (False, ["Ollama unreachable"]),
    )
    client = TestClient(app)
    dumped = json.dumps(client.get("/ready").json()).lower()
    assert "openrouter_api_key" not in dumped
    assert "sk-" not in dumped
    assert "password" not in dumped