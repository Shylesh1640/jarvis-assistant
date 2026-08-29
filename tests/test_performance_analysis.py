"""Tests for Phase 13 performance analysis framework."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.performance.analysis import (
    AccuracyMetrics,
    EfficiencyMetrics,
    PerformanceRecord,
    ReasoningQualityMetrics,
    UserSatisfactionMetrics,
    analyze_by_strategy,
    compare_deep_vs_standard,
    compare_strategies,
    export_csv,
    export_json,
    generate_report,
    is_enabled,
    record_performance,
    report_markdown,
    redact,
    retention_days,
)
from jarvis.performance.storage import cleanup, ensure_dir, query


@pytest.fixture
def tmp_perf_dir(tmp_path):
    return str(tmp_path / "perf")


class TestRedaction:
    def test_redacts_api_key(self):
        assert "sk-abc123XYZ789" not in redact("key sk-abc123XYZ789 secret")

    def test_redacts_bearer(self):
        assert "bearer abcdef" not in redact("header bearer abcdefghijklmnop").lower()

    def test_redacts_nested(self):
        out = redact({"api": "sk-1234567890abcdef", "items": ["bearer abcdefghijklmnop"]})
        assert "sk-1234567890abcdef" not in json.dumps(out)
        assert "abcdefghijklmnop" not in json.dumps(out)

    def test_preserves_non_secrets(self):
        text = "hello world"
        assert redact(text) == text


class TestStorage:
    def test_ensure_dir(self, tmp_perf_dir):
        p = ensure_dir(tmp_perf_dir)
        assert p.exists()

    def test_append_and_query(self, tmp_perf_dir):
        record = PerformanceRecord(
            strategy="cot",
            task_type="general",
            accuracy=AccuracyMetrics(correctness=0.9),
            reasoning_quality=ReasoningQualityMetrics(logical_consistency=0.8),
            efficiency=EfficiencyMetrics(latency_ms=100.0),
            user_satisfaction=UserSatisfactionMetrics(thumbs_up=1, thumbs_down=0),
        )
        path = record_performance(record, base_dir=tmp_perf_dir)
        assert Path(path).exists()
        results = query(base_dir=tmp_perf_dir)
        assert len(results) == 1
        assert results[0]["strategy"] == "cot"

    def test_cleanup(self, tmp_perf_dir):
        ensure_dir(tmp_perf_dir)
        removed = cleanup(0, base_dir=tmp_perf_dir)
        assert removed >= 0


class TestAnalysis:
    def test_is_enabled_default(self):
        assert is_enabled() is True

    def test_retention_days_default(self):
        assert retention_days() == 90

    def test_generate_report_empty(self, tmp_perf_dir):
        report = generate_report(base_dir=tmp_perf_dir)
        assert report.total_records == 0
        assert report.generated_at != ""

    def test_analyze_by_strategy_empty(self, tmp_perf_dir):
        result = analyze_by_strategy(base_dir=tmp_perf_dir)
        assert result == {}

    def test_compare_strategies_insufficient_data(self, tmp_perf_dir):
        comparison = compare_strategies(["cot", "tot"], base_dir=tmp_perf_dir)
        assert comparison.best_strategy == ""

    def test_compare_deep_vs_standard_empty(self, tmp_perf_dir):
        result = compare_deep_vs_standard(base_dir=tmp_perf_dir)
        assert result["deep_thinking"]["count"] == 0
        assert result["standard"]["count"] == 0

    def test_export_json(self, tmp_perf_dir):
        report = generate_report(base_dir=tmp_perf_dir)
        dest = str(Path(tmp_perf_dir) / "report.json")
        export_json(report, dest)
        assert Path(dest).exists()
        with open(dest, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert "generated_at" in data

    def test_export_csv(self, tmp_perf_dir):
        report = generate_report(base_dir=tmp_perf_dir)
        dest = str(Path(tmp_perf_dir) / "report.csv")
        export_csv(report, dest)
        assert Path(dest).exists()

    def test_report_markdown_empty(self, tmp_perf_dir):
        report = generate_report(base_dir=tmp_perf_dir)
        md = report_markdown(report)
        assert "Performance Analysis Report" in md

    def test_no_secrets_in_exports(self, tmp_perf_dir):
        record = PerformanceRecord(
            strategy="test",
            task_type="general",
            efficiency=EfficiencyMetrics(cost_usd=0.01),
            metadata={"api_key": "sk-secret123"},
        )
        record_performance(record, base_dir=tmp_perf_dir)
        report = generate_report(base_dir=tmp_perf_dir)
        dest = str(Path(tmp_perf_dir) / "report.json")
        export_json(report, dest)
        with open(dest, "r", encoding="utf-8") as fh:
            raw = fh.read()
        assert "sk-secret123" not in raw
        assert "secret" not in raw


class TestDisabledBehavior:
    def test_disabled_settings_short_circuit(self, monkeypatch):
        monkeypatch.setenv("PERFORMANCE_ANALYSIS_ENABLED", "false")
        # Re-import to pick up env change
        import importlib
        from jarvis.performance import analysis as perf_mod
        importlib.reload(perf_mod)
        assert perf_mod.is_enabled() is False
