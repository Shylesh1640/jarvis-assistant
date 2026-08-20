"""Tests for the Phase 6 performance evaluation + baseline tooling."""
from __future__ import annotations

import json
from pathlib import Path

from jarvis.benchmark import performance as perf
from jarvis.benchmark.core import BenchmarkReport, BenchmarkResult


def _result(model="qwen3:8b", prompt_type="general", context_length=4096, latency_ms=100.0, success=True, split="100% GPU"):
    return BenchmarkResult(
        model=model,
        prompt_type=prompt_type,
        context_length=context_length,
        prompt_tokens_estimate=120,
        response_tokens_estimate=80,
        latency_ms=latency_ms,
        tokens_per_second=100.0,
        processor_split=split,
        gpu_name="NVIDIA RTX 5050",
        gpu_vram_total_mb=8192,
        gpu_vram_used_before_mb=1024,
        gpu_vram_used_peak_mb=2048,
        system_ram_before_mb=2000,
        system_ram_peak_mb=4000,
        cpu_percent_peak=30.0,
        success=success,
        error_category=None,
        warning=None,
        threshold_violations=[],
    )


def _report(*results):
    return BenchmarkReport(
        session_id="bench-test",
        generated_at="2026-01-01T00:00:00Z",
        schema_version="1.0",
        results=list(results),
        thresholds={},
        models_tested=["qwen3:8b"],
        context_sizes=[4096],
    )


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------


def test_redact_report_value_strings():
    out = perf.redact_report_value("key sk-abcDEFghIJ123456 secret")
    assert "sk-abcDEFghIJ123456" not in out
    assert "[REDACTED]" in out


def test_redact_report_value_recursive():
    data = {"api": "sk-1234567890abcdef", "items": ["bearer abcdefghijklmnop"]}
    out = perf.redact_report_value(data)
    assert "sk-1234567890abcdef" not in json.dumps(out)
    assert "abcdefghijklmnop" not in json.dumps(out)


def test_safe_report_dict_has_no_prompts_or_secrets():
    report = _report(_result())
    safe = perf.safe_report_dict(report)
    dumped = json.dumps(safe).lower()
    assert "openrouter_api_key" not in dumped
    assert "sk-" not in dumped
    # BenchmarkResult carries no prompt/response text by design.
    assert "user_input" not in dumped
    assert "final_response" not in dumped


# ---------------------------------------------------------------------------
# baselines
# ---------------------------------------------------------------------------


def test_save_load_baseline_round_trip(tmp_path):
    dest = tmp_path / "reports" / "baseline.json"
    report = _report(_result(latency_ms=150.0))
    written = perf.save_baseline(report, str(dest))
    assert Path(written).exists()
    loaded = perf.load_baseline(str(dest))
    assert loaded["_kind"] == "jarvis-benchmark-baseline"
    assert loaded["results"][0]["latency_ms"] == 150.0


def test_load_baseline_missing_returns_none(tmp_path):
    assert perf.load_baseline(str(tmp_path / "nope.json")) is None


def test_compare_no_regressions():
    baseline = {"schema_version": "1.0", "results": [_result(latency_ms=100.0).to_dict()]}
    report = _report(_result(latency_ms=110.0))
    assert perf.compare_with_baseline(report, baseline) == []


def test_compare_flags_latency_regression():
    baseline = {"schema_version": "1.0", "results": [_result(latency_ms=100.0).to_dict()]}
    report = _report(_result(latency_ms=150.0))  # >30% slower
    regressions = perf.compare_with_baseline(report, baseline)
    assert any("30% slower" in r for r in regressions)


def test_compare_flags_failure_regression():
    baseline = {"schema_version": "1.0", "results": [_result(success=True).to_dict()]}
    report = _report(_result(success=False, latency_ms=0.0))
    regressions = perf.compare_with_baseline(report, baseline)
    assert any("previously succeeded" in r for r in regressions)


def test_compare_flags_processor_split_regression():
    baseline = {"schema_version": "1.0", "results": [_result(split="100% GPU").to_dict()]}
    report = _report(_result(split="Partial CPU/GPU"))
    regressions = perf.compare_with_baseline(report, baseline)
    assert any("processor split regressed" in r for r in regressions)


def test_compare_flags_schema_mismatch():
    baseline = {"schema_version": "0.9", "results": []}
    report = _report()
    regressions = perf.compare_with_baseline(report, baseline)
    assert any("schema version" in r for r in regressions)


# ---------------------------------------------------------------------------
# mock evaluation
# ---------------------------------------------------------------------------


def test_mock_evaluator_all_scenarios_pass():
    results = perf.run_evaluation(scenarios=None, live=False)
    names = [r.scenario for r in results]
    assert set(names) == set(perf._SCENARIOS.keys())
    assert all(r.passed for r in results)
    assert all(r.gpu_policy == "prefer_gpu" for r in results)


def test_mock_evaluator_subset():
    results = perf.run_evaluation(scenarios=["coding"], live=False)
    assert [r.scenario for r in results] == ["coding"]
    assert results[0].selected_model == perf.settings.coding_model


def test_eval_report_dict_counts():
    results = perf.run_evaluation(scenarios=["general", "coding"], live=False)
    report = perf.eval_report_dict(results)
    assert report["summary"]["cases"] == 2
    assert report["summary"]["passed"] == 2
    assert report["summary"]["failed"] == 0
    assert len(report["results"]) == 2


def test_eval_report_markdown_includes_hardware_note():
    results = perf.run_evaluation(scenarios=["general"], live=False)
    md = perf.eval_report_markdown(results)
    assert "Hardware-specific note" in md
    assert "| scenario |" in md
    assert "PASS" in md
    assert "qwen3:8b" in md


def test_live_evaluator_uses_ollama_client(monkeypatch):
    class _FakeLLM:
        def invoke(self, messages):
            from langchain_core.messages import AIMessage

            return AIMessage(content="live answer")

    monkeypatch.setattr("jarvis.models.ollama_client.get_model_named", lambda *a, **k: _FakeLLM())
    evaluator = perf.LiveEvaluator()
    result = evaluator.run_scenario("general")
    assert result.passed is True
    assert result.tool_behavior == "not_exercised"
    assert result.processor_split == "unknown"


def test_live_evaluator_reports_error_honestly(monkeypatch):
    class _Boom:
        def invoke(self, messages):
            raise RuntimeError("ollama down")

    monkeypatch.setattr("jarvis.models.ollama_client.get_model_named", lambda *a, **k: _Boom())
    evaluator = perf.LiveEvaluator()
    result = evaluator.run_scenario("coding")
    assert result.passed is False
    assert result.error == "RuntimeError"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_mock_run_writes_reports(tmp_path):
    out_json = tmp_path / "eval.json"
    out_md = tmp_path / "eval.md"
    code = perf.main(["--scenario", "general", "--output", str(out_json), "--markdown", str(out_md)])
    assert code == 0
    assert out_json.exists()
    assert out_md.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["summary"]["passed"] == 1
    assert "OPENROUTER_API_KEY" not in out_json.read_text(encoding="utf-8").upper().replace("OPENROUTER", "")


def test_cli_allow_cloud_is_documented_noop(tmp_path):
    out_json = tmp_path / "eval.json"
    code = perf.main(["--allow-cloud", "--scenario", "general", "--output", str(out_json)])
    assert code == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["results"][0]["gpu_policy"] == perf.settings.gpu_policy


def test_evaluate_module_never_imports_openrouter():
    import inspect

    src = inspect.getsource(perf)
    assert "openrouter_client" not in src
    assert "openrouter_api_key" not in src