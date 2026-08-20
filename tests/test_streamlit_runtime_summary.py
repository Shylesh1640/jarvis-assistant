"""Tests for the Streamlit runtime-mode summary helper.

Loads only ``_runtime_mode_summary`` from streamlit_app.py via ``ast`` (the
same pattern as test_export_and_adaptive.py) so Streamlit is never imported.
"""
from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def runtime_summary_module() -> types.ModuleType:
    src = Path("streamlit_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_runtime_mode_summary", "_gpu_policy_summary")]
    assert any(n.name == "_runtime_mode_summary" for n in fns), "_runtime_mode_summary not found in streamlit_app.py"
    module = types.ModuleType("streamlit_app")
    exec(compile(ast.Module(body=fns, type_ignores=[]), "streamlit_app.py", "exec"), module.__dict__)
    return module


def _snap(**overrides):
    base = {
        "runtime": {
            "runtime_mode": "local",
            "database_backend": "sqlite",
            "vector_store_backend": "chroma_embedded",
            "task_backend": "in_process",
            "docker_required": False,
            "docker_detected": False,
            "warnings": [],
        },
        "docker": {"daemon_reachable": False, "containers": []},
        "wsl": {"wsl2_enabled": True, "default_distro": "Ubuntu", "config_keys": {"memory": True}},
    }
    base.update(overrides)
    return base


def test_summary_unavailable_when_no_runtime(runtime_summary_module):
    out = runtime_summary_module._runtime_mode_summary(None)
    assert out["available"] is False
    out = runtime_summary_module._runtime_mode_summary({"runtime": None})
    assert out["available"] is False


def test_summary_local_mode(runtime_summary_module):
    out = runtime_summary_module._runtime_mode_summary(_snap())
    assert out["available"] is True
    assert out["mode"] == "local"
    assert out["database_backend"] == "sqlite"
    assert out["docker_required"] is False


def test_summary_docker_mode(runtime_summary_module):
    out = runtime_summary_module._runtime_mode_summary(
        _snap(
            runtime={
                "runtime_mode": "docker",
                "database_backend": "postgresql",
                "vector_store_backend": "chroma_embedded",
                "task_backend": "in_process",
                "docker_required": True,
                "docker_detected": False,
                "warnings": ["daemon down"],
            }
        )
    )
    assert out["mode"] == "docker"
    assert out["docker_required"] is True
    assert out["docker_detected"] is False
    assert out["warnings"] == ["daemon down"]


def test_summary_docker_containers_counted(runtime_summary_module):
    out = runtime_summary_module._runtime_mode_summary(
        _snap(docker={"daemon_reachable": True, "containers": [{"name": "a"}, {"name": "b"}]})
    )
    assert out["docker_detected"] is True
    assert out["docker_containers"] == 2


def test_summary_wsl_and_config_keys_present_only(runtime_summary_module):
    out = runtime_summary_module._runtime_mode_summary(
        _snap(wsl={
            "wsl2_enabled": True,
            "default_distro": "Ubuntu",
            "config_keys": {"memory": True, "processors": False, "swap": False, "autoMemoryReclaim": False},
        })
    )
    assert out["wsl2_enabled"] is True
    assert out["wsl_default_distro"] == "Ubuntu"
    # Only keys that are present — and values are never returned.
    assert out["wsl_config_keys"] == ["memory"]
    assert "8GB" not in str(out)
    assert "processors" not in out["wsl_config_keys"]


# ---------------------------------------------------------------------------
# GPU policy summary helper
# ---------------------------------------------------------------------------


def test_gpu_policy_summary_unavailable_when_missing(runtime_summary_module):
    out = runtime_summary_module._gpu_policy_summary(None)
    assert out["available"] is False
    out = runtime_summary_module._gpu_policy_summary({})
    assert out["available"] is False


def test_gpu_policy_summary_reports_policy(runtime_summary_module):
    out = runtime_summary_module._gpu_policy_summary(
        {
            "gpu_policy": {
                "policy": "require_gpu",
                "allow_cpu_fallback": False,
                "max_vram_percent": 95.0,
                "min_free_vram_mb": 512,
                "strong_model_allow_partial_offload": False,
                "runtime_check_enabled": True,
            }
        }
    )
    assert out["available"] is True
    assert out["policy"] == "require_gpu"
    joined = "\n".join(out["lines"])
    assert "CPU fallback: blocked" in joined
    assert "min free: 512 MB" in joined