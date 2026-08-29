"""Tests for Phase 13 A/B testing of reasoning strategies."""
from __future__ import annotations


import pytest

from jarvis.ab_testing.manager import (
    ABTestManager,
    MetricEvent,
)
from jarvis.ab_testing.storage import append_record, cleanup, ensure_dir, query


@pytest.fixture
def tmp_ab_dir(tmp_path):
    return str(tmp_path / "ab")


class TestStorage:
    def test_ensure_dir(self, tmp_ab_dir):
        p = ensure_dir(tmp_ab_dir)
        assert p.exists()

    def test_append_and_query(self, tmp_ab_dir):
        append_record({"name": "test", "variant": "A"}, base_dir=tmp_ab_dir)
        results = query(base_dir=tmp_ab_dir)
        assert len(results) == 1
        assert results[0]["name"] == "test"

    def test_cleanup(self, tmp_ab_dir):
        removed = cleanup(0, base_dir=tmp_ab_dir)
        assert removed >= 0


class TestCreateReasoningTest:
    def test_creates_test(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        test = mgr.create_reasoning_test(
            name="cot_vs_tot",
            variant_a="cot",
            variant_b="tot",
        )
        assert test.name == "cot_vs_tot"
        assert test.variant_a == "cot"
        assert test.variant_b == "tot"

    def test_duplicate_name_raises(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        mgr.create_reasoning_test("dup", "a", "b")
        with pytest.raises(ValueError):
            mgr.create_reasoning_test("dup", "a", "b")

    def test_same_variants_raises(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        with pytest.raises(ValueError):
            mgr.create_reasoning_test("bad", "a", "a")


class TestTrafficSplitting:
    def test_consistent_assignment(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        mgr.create_reasoning_test("split_test", "cot", "tot")
        a1 = mgr.assign_variant("split_test", "session-1")
        a2 = mgr.assign_variant("split_test", "session-1")
        assert a1 == a2

    def test_stratified_splitting(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        mgr.create_reasoning_test(
            "strat_test", "cot", "tot", task_types=["general"], stratify_by_task_type=True
        )
        assignment = mgr.assign_variant("strat_test", "session-x", task_type="general")
        assert assignment in ("cot", "tot")

    def test_no_test_returns_none(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        with pytest.raises(ValueError):
            mgr.assign_variant("nonexistent", "s1")


class TestMetricCollection:
    def test_records_metric(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        mgr.create_reasoning_test("metric_test", "cot", "tot")
        event = MetricEvent(
            name="metric_test",
            session_id="s1",
            variant="cot",
            accuracy=0.9,
            relevance=0.8,
            satisfaction=0.7,
            latency_ms=100.0,
            tokens=50,
        )
        mgr.record_metric(event)
        results = query(kind="metric", base_dir=tmp_ab_dir)
        assert len(results) == 1
        assert results[0]["variant"] == "cot"
        assert results[0]["accuracy"] == 0.9

    def test_disabled_behavior(self, monkeypatch, tmp_ab_dir):
        monkeypatch.setattr("jarvis.config.settings.settings.ab_testing_reasoning_enabled", False)
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        assert mgr.enabled is False
        # assign_variant returns control variant when disabled
        mgr.create_reasoning_test("disabled_test", "a", "b")
        assert mgr.assign_variant("disabled_test", "s1") == "a"


class TestStatisticalAnalysis:
    def test_identifies_winner_with_sufficient_samples(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir, min_samples_per_variant=5)
        mgr.create_reasoning_test("stat_test", "cot", "tot")
        for i in range(5):
            mgr.record_metric(MetricEvent(
                name="stat_test", session_id=f"s{i}", variant="cot",
                accuracy=0.9, relevance=0.9, satisfaction=0.9,
                latency_ms=100.0, tokens=50,
            ))
        for i in range(5):
            mgr.record_metric(MetricEvent(
                name="stat_test", session_id=f"s{i+5}", variant="tot",
                accuracy=0.5, relevance=0.5, satisfaction=0.5,
                latency_ms=200.0, tokens=80,
            ))
        report = mgr.analyze("stat_test")
        assert report.winner == "cot"

    def test_insufficient_samples(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir, min_samples_per_variant=50)
        mgr.create_reasoning_test("low_samples", "cot", "tot")
        mgr.record_metric(MetricEvent(
            name="low_samples", session_id="s1", variant="cot",
            accuracy=0.9, relevance=0.9, satisfaction=0.9,
            latency_ms=100.0, tokens=50,
        ))
        report = mgr.analyze("low_samples")
        assert report.winner is None


class TestActiveTests:
    def test_lists_active_tests(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        mgr.create_reasoning_test("active_test", "a", "b")
        tests = mgr.list_active_tests()
        assert any(t.name == "active_test" for t in tests)

    def test_promote_test(self, tmp_ab_dir):
        mgr = ABTestManager(base_dir=tmp_ab_dir)
        mgr.create_reasoning_test("promo_test", "a", "b")
        result = mgr.promote("promo_test", "A")
        assert result.status == "completed"
        assert result.promoted_variant == "A"
