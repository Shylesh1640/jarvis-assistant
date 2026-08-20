"""Tests for Phase 7 deployment profiles (local / single_host / production)."""
from __future__ import annotations

import pytest

from jarvis.config import deployment as dep
from jarvis.config.deployment import (
    deployment_capability_report,
    normalize_profile,
    public_exposure_safe,
    validate_deployment,
)
from jarvis.config.settings import Settings


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
# profile normalization
# ---------------------------------------------------------------------------


def test_normalize_profile():
    assert normalize_profile("local") == "local"
    assert normalize_profile("single_host") == "single_host"
    assert normalize_profile("single-host") == "single_host"
    assert normalize_profile("production") == "production"
    assert normalize_profile("bogus") == "local"
    assert normalize_profile(None) == "local"


def test_dep_profiles_constant():
    assert set(dep.DEPLOYMENT_PROFILES) == {"local", "single_host", "production"}


# ---------------------------------------------------------------------------
# local defaults (safe)
# ---------------------------------------------------------------------------


def test_local_defaults_safe():
    warnings = validate_deployment(_settings())
    assert warnings == []


def test_local_rejects_public_host():
    warnings = validate_deployment(_settings(jarvis_host="0.0.0.0"))
    assert any("not loopback-only" in w for w in warnings)


def test_local_rejects_wildcard_cors():
    warnings = validate_deployment(_settings(jarvis_allowed_origins="*"))
    assert any("permissive CORS" in w for w in warnings)


def test_local_rejects_wildcard_trusted_hosts():
    warnings = validate_deployment(_settings(jarvis_trusted_hosts="*"))
    assert any("'*'" in w and "TRUSTED_HOSTS" in w for w in warnings)


def test_local_tokens_optional():
    warnings = validate_deployment(_settings(require_session_token=False))
    assert warnings == []


# ---------------------------------------------------------------------------
# single_host
# ---------------------------------------------------------------------------


def _single(**overrides) -> Settings:
    base = dict(
        deployment_profile="single_host",
        jarvis_host="192.168.1.50",
        require_session_token=True,
        jarvis_allowed_origins="http://localhost:8501",
        jarvis_trusted_hosts="jarvis.internal",
        jarvis_behind_reverse_proxy=True,
        jarvis_force_https=True,
        jarvis_expose_traces=False,
        jarvis_backup_enabled=True,
    )
    base.update(overrides)
    return _settings(**base)


def test_single_host_configured_is_safe():
    assert validate_deployment(_single()) == []


def test_single_host_requires_session_tokens():
    warnings = validate_deployment(_single(require_session_token=False))
    assert any("session tokens are required" in w for w in warnings)


def test_single_host_requires_allowed_origins():
    warnings = validate_deployment(_single(jarvis_allowed_origins=""))
    assert any("allowed origins are required" in w for w in warnings)


def test_single_host_forbids_wildcard_cors():
    warnings = validate_deployment(_single(jarvis_allowed_origins="*"))
    assert any("wildcard CORS" in w for w in warnings)


def test_single_host_requires_trusted_hosts():
    warnings = validate_deployment(_single(jarvis_trusted_hosts=""))
    assert any("trusted hosts are required" in w for w in warnings)


def test_single_host_exposed_without_proxy_warns():
    warnings = validate_deployment(
        _single(jarvis_behind_reverse_proxy=False, jarvis_force_https=False)
    )
    assert any("reverse proxy" in w for w in warnings)


# ---------------------------------------------------------------------------
# production
# ---------------------------------------------------------------------------


def _prod(**overrides) -> Settings:
    base = dict(
        deployment_profile="production",
        jarvis_host="0.0.0.0",
        require_session_token=True,
        jarvis_allowed_origins="https://assistant.example.com",
        jarvis_trusted_hosts="assistant.example.com",
        jarvis_behind_reverse_proxy=True,
        jarvis_force_https=True,
        jarvis_debug=False,
        jarvis_expose_traces=False,
        jarvis_backup_enabled=True,
        backup_dir="./backups",
        openrouter_api_key="sk-test",
        cloud_daily_budget_usd=1.0,
    )
    base.update(overrides)
    return _settings(**base)


def test_production_configured_is_safe():
    assert validate_deployment(_prod()) == []


def test_production_rejects_loopback_host():
    warnings = validate_deployment(_prod(jarvis_host="127.0.0.1"))
    assert any("loopback" in w for w in warnings)


def test_production_requires_https():
    warnings = validate_deployment(_prod(jarvis_force_https=False))
    assert any("HTTPS must be enforced" in w for w in warnings)


def test_production_rejects_debug_mode():
    warnings = validate_deployment(_prod(jarvis_debug=True))
    assert any("debug mode must be disabled" in w for w in warnings)


def test_production_rejects_trace_exposure():
    warnings = validate_deployment(_prod(jarvis_expose_traces=True))
    assert any("trace" in w for w in warnings)


def test_production_requires_backups():
    warnings = validate_deployment(_prod(jarvis_backup_enabled=False))
    assert any("backups are required" in w for w in warnings)


def test_production_requires_cloud_budget_when_cloud_configured():
    warnings = validate_deployment(_prod(cloud_daily_budget_usd=0.0, cloud_max_request_cost_usd=0.0, cloud_max_session_cost_usd=0.0))
    assert any("cloud budget" in w for w in warnings)


def test_production_public_host_without_proxy_warns():
    warnings = validate_deployment(
        _prod(jarvis_behind_reverse_proxy=False)
    )
    assert any("reverse proxy" in w for w in warnings)


def test_production_no_cloud_no_budget_warning():
    warnings = validate_deployment(_prod(openrouter_api_key=""))
    assert not any("cloud budget" in w for w in warnings)


# ---------------------------------------------------------------------------
# capability report
# ---------------------------------------------------------------------------


def test_capability_report_production_shape():
    report = deployment_capability_report(_prod())
    assert report["deployment_profile"] == "production"
    assert report["session_tokens_required"] is True
    assert report["debug_enabled"] is False
    assert report["cloud_budget_enforced"] is True
    assert report["database_backup_configured"] is True
    assert report["public_exposure_safe"] is True
    assert report["warnings"] == []


def test_capability_report_local_shape():
    report = deployment_capability_report(_settings())
    assert report["deployment_profile"] == "local"
    assert report["session_tokens_required"] is False
    assert report["debug_enabled"] is False
    assert report["cloud_budget_enforced"] is False
    assert report["database_backup_configured"] is False
    assert report["public_exposure_safe"] is True


def test_capability_report_never_exposes_secrets():
    import json

    report = deployment_capability_report(
        _prod(openrouter_api_key="sk-secret-abcdefghijklmnop")
    )
    assert "sk-secret-abcdefghijklmnop" not in json.dumps(report)
    assert "openrouter_api_key" not in json.dumps(report)


def test_public_exposure_safe():
    assert public_exposure_safe(_settings(jarvis_host="127.0.0.1")) is True
    assert public_exposure_safe(_settings(jarvis_host="localhost")) is True
    assert (
        public_exposure_safe(_settings(jarvis_host="0.0.0.0", jarvis_behind_reverse_proxy=True))
        is True
    )
    assert (
        public_exposure_safe(_settings(jarvis_host="0.0.0.0", jarvis_behind_reverse_proxy=False))
        is False
    )


# ---------------------------------------------------------------------------
# /runtime exposure
# ---------------------------------------------------------------------------


def test_runtime_snapshot_includes_deployment(monkeypatch):
    from jarvis.models.runtime_diagnostics import get_runtime_snapshot

    monkeypatch.setattr(
        "jarvis.config.settings.settings.deployment_profile", "single_host"
    )
    snap = get_runtime_snapshot()
    block = snap.get("deployment")
    assert block is not None
    assert block["deployment_profile"] == "single_host"
    assert set(block) >= {
        "deployment_profile",
        "runtime_mode",
        "session_tokens_required",
        "debug_enabled",
        "cloud_budget_enforced",
        "database_backup_configured",
        "public_exposure_safe",
        "warnings",
    }
    monkeypatch.setattr(
        "jarvis.config.settings.settings.deployment_profile", "local"
    )