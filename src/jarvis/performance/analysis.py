"""Performance analysis framework for deep thinking and reasoning variations (Phase 13).

Analyses accuracy, reasoning quality, efficiency, and user satisfaction across
different reasoning strategies and task types. Never exposes sensitive data
or full prompt/response content.
"""
from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis.performance import storage

# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bbearer\s+[A-Za-z0-9_\-\.]{12,}\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
]


def redact(value: Any) -> Any:
    """Recursively redact anything that looks like a secret."""
    if isinstance(value, str):
        out = value
        for pattern in _SECRET_PATTERNS:
            out = pattern.sub("[REDACTED]", out)
        return out
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class AccuracyMetrics:
    correctness: float = 0.0
    expected_match: float = 0.0
    human_eval_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReasoningQualityMetrics:
    logical_consistency: float = 0.0
    completeness: float = 0.0
    relevance: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EfficiencyMetrics:
    tokens_reasoning: int = 0
    tokens_answer: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UserSatisfactionMetrics:
    thumbs_up: int = 0
    thumbs_down: int = 0
    feedback_count: int = 0
    perceived_helpfulness: float = 0.0

    @property
    def satisfaction_rate(self) -> float:
        total = self.thumbs_up + self.thumbs_down
        if total == 0:
            return 0.0
        return self.thumbs_up / total

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["satisfaction_rate"] = self.satisfaction_rate
        return d


@dataclass
class PerformanceRecord:
    """A single performance measurement for a reasoning strategy on a task."""
    strategy: str
    task_type: str
    accuracy: AccuracyMetrics = field(default_factory=AccuracyMetrics)
    reasoning_quality: ReasoningQualityMetrics = field(default_factory=ReasoningQualityMetrics)
    efficiency: EfficiencyMetrics = field(default_factory=EfficiencyMetrics)
    user_satisfaction: UserSatisfactionMetrics = field(default_factory=UserSatisfactionMetrics)
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "task_type": self.task_type,
            "accuracy": self.accuracy.to_dict(),
            "reasoning_quality": self.reasoning_quality.to_dict(),
            "efficiency": self.efficiency.to_dict(),
            "user_satisfaction": self.user_satisfaction.to_dict(),
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class StrategyComparison:
    """Comparison of strategies on the same task type."""
    task_type: str
    strategies: list[str] = field(default_factory=list)
    best_strategy: str = ""
    metrics_summary: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PerformanceReport:
    """Aggregated performance report."""
    generated_at: str = ""
    period_days: int = 90
    total_records: int = 0
    by_strategy: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_task_type: dict[str, dict[str, Any]] = field(default_factory=dict)
    comparisons: list[StrategyComparison] = field(default_factory=list)
    deep_vs_standard: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "period_days": self.period_days,
            "total_records": self.total_records,
            "by_strategy": self.by_strategy,
            "by_task_type": self.by_task_type,
            "comparisons": [c.to_dict() for c in self.comparisons],
            "deep_vs_standard": self.deep_vs_standard,
        }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _setting(name: str, default: str) -> str:
    return os.environ.get(name, default)


def is_enabled() -> bool:
    return _setting("PERFORMANCE_ANALYSIS_ENABLED", "true").lower() == "true"


def retention_days() -> int:
    try:
        return int(_setting("PERFORMANCE_ANALYSIS_RETENTION_DAYS", "90"))
    except ValueError:
        return 90


def min_samples() -> int:
    try:
        return int(_setting("PERFORMANCE_ANALYSIS_MIN_SAMPLES", "10"))
    except ValueError:
        return 10


# ---------------------------------------------------------------------------
# Metrics recording
# ---------------------------------------------------------------------------


def record_performance(record: PerformanceRecord, base_dir: str | None = None) -> str:
    """Store a performance record. Returns the path written to."""
    if not record.timestamp:
        record.timestamp = datetime.now(timezone.utc).isoformat()
    return storage.append_metric(redact(record.to_dict()), base_dir)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _aggregate_records(records: list[PerformanceRecord]) -> dict[str, Any]:
    """Aggregate a list of records into summary statistics."""
    if not records:
        return {"count": 0}

    accuracy_correct = [r.accuracy.correctness for r in records]
    accuracy_match = [r.accuracy.expected_match for r in records]
    accuracy_human = [r.accuracy.human_eval_score for r in records]
    consistency = [r.reasoning_quality.logical_consistency for r in records]
    completeness = [r.reasoning_quality.completeness for r in records]
    relevance = [r.reasoning_quality.relevance for r in records]
    tokens_reasoning = [r.efficiency.tokens_reasoning for r in records]
    tokens_answer = [r.efficiency.tokens_answer for r in records]
    latencies = [r.efficiency.latency_ms for r in records]
    costs = [r.efficiency.cost_usd for r in records]
    thumbs_up = sum(r.user_satisfaction.thumbs_up for r in records)
    thumbs_down = sum(r.user_satisfaction.thumbs_down for r in records)
    helpfulness = [r.user_satisfaction.perceived_helpfulness for r in records]

    total_votes = thumbs_up + thumbs_down
    satisfaction_rate = thumbs_up / total_votes if total_votes > 0 else 0.0

    return {
        "count": len(records),
        "accuracy": {
            "correctness": round(_mean(accuracy_correct), 4),
            "expected_match": round(_mean(accuracy_match), 4),
            "human_eval_score": round(_mean(accuracy_human), 4),
        },
        "reasoning_quality": {
            "logical_consistency": round(_mean(consistency), 4),
            "completeness": round(_mean(completeness), 4),
            "relevance": round(_mean(relevance), 4),
        },
        "efficiency": {
            "tokens_reasoning": round(_mean(tokens_reasoning), 1),
            "tokens_answer": round(_mean(tokens_answer), 1),
            "latency_ms": round(_mean(latencies), 1),
            "cost_usd": round(_mean(costs), 6),
        },
        "user_satisfaction": {
            "thumbs_up": thumbs_up,
            "thumbs_down": thumbs_down,
            "satisfaction_rate": round(satisfaction_rate, 4),
            "perceived_helpfulness": round(_mean(helpfulness), 4),
        },
    }


def _dict_to_record(d: dict[str, Any]) -> PerformanceRecord:
    """Safely convert a dict (from storage) to a PerformanceRecord."""
    return PerformanceRecord(
        strategy=d.get("strategy", "unknown"),
        task_type=d.get("task_type", "unknown"),
        accuracy=AccuracyMetrics(**{
            k: float(v) for k, v in d.get("accuracy", {}).items()
        }),
        reasoning_quality=ReasoningQualityMetrics(**{
            k: float(v) for k, v in d.get("reasoning_quality", {}).items()
        }),
        efficiency=EfficiencyMetrics(**{
            k: (int(v) if k.startswith("tokens_") else float(v))
            for k, v in d.get("efficiency", {}).items()
        }),
        user_satisfaction=UserSatisfactionMetrics(**{
            k: (int(v) if k.startswith("thumbs_") or k.endswith("_count") else float(v))
            for k, v in d.get("user_satisfaction", {}).items()
            if k != "satisfaction_rate"
        }),
        timestamp=d.get("timestamp", ""),
        metadata=d.get("metadata", {}),
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze_by_strategy(
    *,
    strategy: str | None = None,
    days: int | None = None,
    base_dir: str | None = None,
) -> dict[str, Any]:
    """Aggregate metrics grouped by strategy."""
    effective_days = days or retention_days()
    since = datetime.now(timezone.utc) - timedelta(days=effective_days)
    records_raw = storage.query(
        strategy=strategy,
        since=since,
        base_dir=base_dir,
    )
    records = [_dict_to_record(r) for r in records_raw]

    by_strategy: dict[str, list[PerformanceRecord]] = {}
    for r in records:
        by_strategy.setdefault(r.strategy, []).append(r)

    return {
        name: _aggregate_records(group)
        for name, group in sorted(by_strategy.items())
    }


def analyze_by_task_type(
    *,
    task_type: str | None = None,
    days: int | None = None,
    base_dir: str | None = None,
) -> dict[str, Any]:
    """Aggregate metrics grouped by task type."""
    effective_days = days or retention_days()
    since = datetime.now(timezone.utc) - timedelta(days=effective_days)
    records_raw = storage.query(
        task_type=task_type,
        since=since,
        base_dir=base_dir,
    )
    records = [_dict_to_record(r) for r in records_raw]

    by_task: dict[str, list[PerformanceRecord]] = {}
    for r in records:
        by_task.setdefault(r.task_type, []).append(r)

    return {
        name: _aggregate_records(group)
        for name, group in sorted(by_task.items())
    }


def compare_strategies(
    strategies: list[str],
    *,
    task_type: str | None = None,
    days: int | None = None,
    base_dir: str | None = None,
) -> StrategyComparison:
    """Compare multiple strategies on the same task type(s)."""
    effective_days = days or retention_days()
    since = datetime.now(timezone.utc) - timedelta(days=effective_days)

    metrics_summary: dict[str, dict[str, float]] = {}
    for strat in strategies:
        records_raw = storage.query(
            strategy=strat,
            task_type=task_type,
            since=since,
            base_dir=base_dir,
        )
        records = [_dict_to_record(r) for r in records_raw]
        if records:
            agg = _aggregate_records(records)
            metrics_summary[strat] = {
                "accuracy": agg["accuracy"]["correctness"],
                "reasoning_quality": agg["reasoning_quality"]["logical_consistency"],
                "efficiency": agg["efficiency"]["latency_ms"],
                "satisfaction": agg["user_satisfaction"]["satisfaction_rate"],
                "count": agg["count"],
            }
        else:
            metrics_summary[strat] = {"count": 0}

    best = ""
    best_score = -1.0
    for strat, metrics in metrics_summary.items():
        if metrics.get("count", 0) < min_samples():
            continue
        score = (
            metrics.get("accuracy", 0.0) * 0.3
            + metrics.get("reasoning_quality", 0.0) * 0.3
            + metrics.get("satisfaction", 0.0) * 0.4
        )
        if score > best_score:
            best_score = score
            best = strat

    return StrategyComparison(
        task_type=task_type or "all",
        strategies=strategies,
        best_strategy=best,
        metrics_summary=metrics_summary,
    )


def compare_deep_vs_standard(
    *,
    days: int | None = None,
    base_dir: str | None = None,
) -> dict[str, Any]:
    """Compare deep thinking mode vs standard mode."""
    effective_days = days or retention_days()
    since = datetime.now(timezone.utc) - timedelta(days=effective_days)

    deep_raw = storage.query(
        strategy="deep_thinking",
        since=since,
        base_dir=base_dir,
    )
    standard_raw = storage.query(
        strategy="standard",
        since=since,
        base_dir=base_dir,
    )

    deep_records = [_dict_to_record(r) for r in deep_raw]
    standard_records = [_dict_to_record(r) for r in standard_raw]

    deep_agg = _aggregate_records(deep_records)
    standard_agg = _aggregate_records(standard_records)

    return {
        "deep_thinking": deep_agg,
        "standard": standard_agg,
        "delta": {
            "accuracy": round(
                deep_agg["accuracy"]["correctness"] - standard_agg["accuracy"]["correctness"], 4
            ) if deep_records and standard_records else 0.0,
            "latency_ms": round(
                deep_agg["efficiency"]["latency_ms"] - standard_agg["efficiency"]["latency_ms"], 1
            ) if deep_records and standard_records else 0.0,
            "satisfaction": round(
                deep_agg["user_satisfaction"]["satisfaction_rate"]
                - standard_agg["user_satisfaction"]["satisfaction_rate"], 4
            ) if deep_records and standard_records else 0.0,
        },
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(
    *,
    strategy: str | None = None,
    task_type: str | None = None,
    days: int | None = None,
    base_dir: str | None = None,
) -> PerformanceReport:
    """Generate a full performance report."""
    effective_days = days or retention_days()
    since = datetime.now(timezone.utc) - timedelta(days=effective_days)

    records_raw = storage.query(
        strategy=strategy,
        task_type=task_type,
        since=since,
        base_dir=base_dir,
    )
    records = [_dict_to_record(r) for r in records_raw]

    by_strategy: dict[str, list[PerformanceRecord]] = {}
    by_task: dict[str, list[PerformanceRecord]] = {}
    for r in records:
        by_strategy.setdefault(r.strategy, []).append(r)
        by_task.setdefault(r.task_type, []).append(r)

    comparisons: list[StrategyComparison] = []
    all_strategies = sorted(by_strategy.keys())
    if len(all_strategies) >= 2:
        for tt in sorted(by_task.keys()):
            strat_in_task = sorted({r.strategy for r in by_task[tt]})
            if len(strat_in_task) >= 2:
                comparisons.append(compare_strategies(
                    strat_in_task,
                    task_type=tt,
                    days=effective_days,
                    base_dir=base_dir,
                ))

    deep_vs_standard = compare_deep_vs_standard(days=effective_days, base_dir=base_dir)

    return PerformanceReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        period_days=effective_days,
        total_records=len(records),
        by_strategy={n: _aggregate_records(g) for n, g in sorted(by_strategy.items())},
        by_task_type={n: _aggregate_records(g) for n, g in sorted(by_task.items())},
        comparisons=comparisons,
        deep_vs_standard=deep_vs_standard,
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_json(report: PerformanceReport, path: str) -> None:
    """Write a report as JSON."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(redact(report.to_dict()), fh, indent=2)


def export_csv(report: PerformanceReport, path: str) -> None:
    """Write per-strategy metrics as CSV."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for strategy, metrics in report.by_strategy.items():
        row: dict[str, Any] = {"group": "strategy", "name": strategy}
        _flatten_metrics(row, metrics)
        rows.append(row)
    for task_type, metrics in report.by_task_type.items():
        row = {"group": "task_type", "name": task_type}
        _flatten_metrics(row, metrics)
        rows.append(row)

    if not rows:
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            fh.write("")
        return

    fieldnames = list(rows[0].keys())
    with open(dest, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _flatten_metrics(row: dict[str, Any], metrics: dict[str, Any]) -> None:
    row["count"] = metrics.get("count", 0)
    for section in ("accuracy", "reasoning_quality", "efficiency", "user_satisfaction"):
        section_data = metrics.get(section, {})
        for k, v in section_data.items():
            row[f"{section}.{k}"] = v


def report_markdown(report: PerformanceReport) -> str:
    """Render a report as Markdown."""
    lines: list[str] = []
    lines.append("# Jarvis Performance Analysis Report")
    lines.append("")
    lines.append(f"- **generated_at**: `{report.generated_at}`")
    lines.append(f"- **period_days**: {report.period_days}")
    lines.append(f"- **total_records**: {report.total_records}")
    lines.append("")

    if report.by_strategy:
        lines.append("## By Strategy")
        lines.append("")
        for name, metrics in report.by_strategy.items():
            lines.append(f"### {name}")
            lines.append("")
            _markdown_metrics(lines, metrics)
            lines.append("")

    if report.by_task_type:
        lines.append("## By Task Type")
        lines.append("")
        for name, metrics in report.by_task_type.items():
            lines.append(f"### {name}")
            lines.append("")
            _markdown_metrics(lines, metrics)
            lines.append("")

    if report.comparisons:
        lines.append("## Strategy Comparisons")
        lines.append("")
        for comp in report.comparisons:
            lines.append(f"### Task Type: {comp.task_type}")
            lines.append("")
            lines.append(f"- **best_strategy**: {comp.best_strategy or 'insufficient data'}")
            lines.append(f"- **strategies_compared**: {', '.join(comp.strategies)}")
            lines.append("")

    if report.deep_vs_standard:
        lines.append("## Deep Thinking vs Standard")
        lines.append("")
        dvs = report.deep_vs_standard
        lines.append(f"- **deep_thinking records**: {dvs.get('deep_thinking', {}).get('count', 0)}")
        lines.append(f"- **standard records**: {dvs.get('standard', {}).get('count', 0)}")
        delta = dvs.get("delta", {})
        lines.append(f"- **accuracy delta**: {delta.get('accuracy', 0):+.4f}")
        lines.append(f"- **latency delta (ms)**: {delta.get('latency_ms', 0):+.1f}")
        lines.append(f"- **satisfaction delta**: {delta.get('satisfaction', 0):+.4f}")
        lines.append("")

    return "\n".join(lines)


def _markdown_metrics(lines: list[str], metrics: dict[str, Any]) -> None:
    lines.append(f"- **count**: {metrics.get('count', 0)}")
    for section in ("accuracy", "reasoning_quality", "efficiency", "user_satisfaction"):
        section_data = metrics.get(section, {})
        if not section_data:
            continue
        lines.append(f"- **{section}**:")
        for k, v in section_data.items():
            lines.append(f"  - {k}: {v}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        prog="jarvis-analyze-performance",
        description="Analyze performance of deep thinking and reasoning strategies.",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Filter by strategy name (e.g. deep_thinking, cot, tot).",
    )
    parser.add_argument(
        "--compare-strategies",
        default=None,
        help="Comma-separated list of strategies to compare.",
    )
    parser.add_argument(
        "--task-type",
        default=None,
        help="Filter by task type (e.g. general, coding, rag).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write JSON report to this path.",
    )
    parser.add_argument(
        "--markdown",
        default=None,
        help="Write Markdown report to this path.",
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="Write CSV report to this path.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Number of days to include (default: from settings).",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Run retention cleanup and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not is_enabled():
        print("Performance analysis is disabled (PERFORMANCE_ANALYSIS_ENABLED=false).")
        return 1

    if args.cleanup:
        removed = storage.cleanup(retention_days())
        print(f"Removed {removed} old performance data file(s).")
        return 0

    days = args.days or retention_days()

    if args.compare_strategies:
        strategies = [s.strip() for s in args.compare_strategies.split(",") if s.strip()]
        comparison = compare_strategies(
            strategies,
            task_type=args.task_type,
            days=days,
        )
        print(f"Strategy comparison (task_type={comparison.task_type}):")
        print(f"  Best: {comparison.best_strategy or 'insufficient data'}")
        for strat, metrics in comparison.metrics_summary.items():
            print(f"  {strat}: {metrics}")
        return 0

    report = generate_report(
        strategy=args.strategy,
        task_type=args.task_type,
        days=days,
    )

    print(f"Performance report ({report.total_records} records, {report.period_days} days):")
    print("")
    if report.by_strategy:
        print("By strategy:")
        for name, metrics in report.by_strategy.items():
            print(f"  {name}: count={metrics.get('count', 0)}")
    if report.by_task_type:
        print("By task type:")
        for name, metrics in report.by_task_type.items():
            print(f"  {name}: count={metrics.get('count', 0)}")

    if args.output:
        export_json(report, args.output)
        print(f"JSON report written to {args.output}")
    if args.markdown:
        dest = Path(args.markdown)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(report_markdown(report))
        print(f"Markdown report written to {args.markdown}")
    if args.csv:
        export_csv(report, args.csv)
        print(f"CSV report written to {args.csv}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
