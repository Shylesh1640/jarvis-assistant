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
    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_runtime_mode_summary":
            fn = node
            break
    assert fn is not None, "_runtime_mode_summary not found in streamlit_app.py"
    module = types.ModuleType("streamlit_app")
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "streamlit_app.py", "exec"), module.__dict__)
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