"""A/B testing framework for reasoning strategies (Phase 13).

Tests one reasoning strategy against another (e.g. deep thinking vs standard
mode) by splitting traffic between two variants, collecting metrics per
request, and running a simple two-sample z-test to decide a winner.

Everything is file-based (``./reports/ab_testing/``) and has no dependency on
any external service or cloud client. Dataclasses describe the data shapes.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from jarvis.ab_testing.storage import (
    append_record,
    cleanup,
    read_config,
    read_configs,
    query,
)
from jarvis.config.settings import settings


VARIANT_A = "A"
VARIANT_B = "B"
DEFAULT_SUCCESS_METRICS = ("accuracy", "relevance", "satisfaction")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ABTestConfig:
    name: str
    variant_a: str
    variant_b: str
    traffic_split: float = 50.0
    success_metrics: list[str] = field(default_factory=lambda: list(DEFAULT_SUCCESS_METRICS))
    task_types: list[str] = field(default_factory=list)
    stratify_by_task_type: bool = False
    status: str = "active"
    promoted_variant: str | None = None
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "config",
            "name": self.name,
            "variant_a": self.variant_a,
            "variant_b": self.variant_b,
            "traffic_split": self.traffic_split,
            "success_metrics": list(self.success_metrics),
            "task_types": list(self.task_types),
            "stratify_by_task_type": self.stratify_by_task_type,
            "status": self.status,
            "promoted_variant": self.promoted_variant,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ABTestConfig":
        return cls(
            name=data["name"],
            variant_a=data["variant_a"],
            variant_b=data["variant_b"],
            traffic_split=float(data.get("traffic_split", 50.0)),
            success_metrics=list(data.get("success_metrics", DEFAULT_SUCCESS_METRICS)),
            task_types=list(data.get("task_types", [])),
            stratify_by_task_type=bool(data.get("stratify_by_task_type", False)),
            status=data.get("status", "active"),
            promoted_variant=data.get("promoted_variant"),
            created_at=data.get("created_at", _now_iso()),
        )


@dataclass
class MetricEvent:
    name: str
    session_id: str
    variant: str
    task_type: str | None = None
    accuracy: float | None = None
    relevance: float | None = None
    satisfaction: float | None = None
    tokens: int | None = None
    latency_ms: float | None = None
    feedback: str | None = None
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "metric",
            "name": self.name,
            "session_id": self.session_id,
            "variant": self.variant,
            "task_type": self.task_type,
            "accuracy": self.accuracy,
            "relevance": self.relevance,
            "satisfaction": self.satisfaction,
            "tokens": self.tokens,
            "latency_ms": self.latency_ms,
            "feedback": self.feedback,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricEvent":
        return cls(
            name=data["name"],
            session_id=data["session_id"],
            variant=data["variant"],
            task_type=data.get("task_type"),
            accuracy=data.get("accuracy"),
            relevance=data.get("relevance"),
            satisfaction=data.get("satisfaction"),
            tokens=data.get("tokens"),
            latency_ms=data.get("latency_ms"),
            feedback=data.get("feedback"),
            timestamp=data.get("timestamp", _now_iso()),
        )


@dataclass
class VariantStats:
    variant: str
    label: str
    n: int
    accuracy: float | None = None
    relevance: float | None = None
    satisfaction: float | None = None
    avg_tokens: float | None = None
    avg_latency_ms: float | None = None
    feedback_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "label": self.label,
            "n": self.n,
            "accuracy": self.accuracy,
            "relevance": self.relevance,
            "satisfaction": self.satisfaction,
            "avg_tokens": self.avg_tokens,
            "avg_latency_ms": self.avg_latency_ms,
            "feedback_counts": dict(self.feedback_counts),
        }


@dataclass
class SignificanceResult:
    metric: str
    variant_a_mean: float
    variant_b_mean: float
    z_score: float
    p_value: float
    significant: bool
    higher_variant: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "variant_a_mean": self.variant_a_mean,
            "variant_b_mean": self.variant_b_mean,
            "z_score": self.z_score,
            "p_value": self.p_value,
            "significant": self.significant,
            "higher_variant": self.higher_variant,
        }


@dataclass
class ABReport:
    name: str
    status: str
    promoted_variant: str | None
    eligible: bool
    min_samples_per_variant: int
    significance_threshold: float
    variant_a: VariantStats
    variant_b: VariantStats
    significance: list[SignificanceResult]
    winner: str | None
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "promoted_variant": self.promoted_variant,
            "eligible": self.eligible,
            "min_samples_per_variant": self.min_samples_per_variant,
            "significance_threshold": self.significance_threshold,
            "variant_a": self.variant_a.to_dict(),
            "variant_b": self.variant_b.to_dict(),
            "significance": [s.to_dict() for s in self.significance],
            "winner": self.winner,
            "recommendation": self.recommendation,
        }


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via the error function (math.erf)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _two_sample_z_test(
    a: list[float], b: list[float]
) -> tuple[float, float]:
    """Two-sample z-test for difference in means (Welch-style SE).

    Returns ``(z_score, p_value)`` (two-tailed). Returns ``(0.0, 1.0)`` when
    there is not enough data to compute a meaningful test.
    """
    n_a = len(a)
    n_b = len(b)
    if n_a < 2 or n_b < 2:
        return 0.0, 1.0

    mean_a = sum(a) / n_a
    mean_b = sum(b) / n_b
    var_a = sum((x - mean_a) ** 2 for x in a) / (n_a - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (n_b - 1)
    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0.0:
        if mean_a == mean_b:
            return 0.0, 1.0
        z = float("inf") if mean_a > mean_b else float("-inf")
        return z, 0.0
    z = (mean_a - mean_b) / se
    # two-tailed p-value
    p = 2.0 * (1.0 - _normal_cdf(abs(z)))
    return z, p


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_SECRET_HINTS = ("secret", "token", "password", "passwd", "api_key", "apikey", "authorization", "key")


def redact(value: Any) -> Any:
    """Recursively redact anything that looks like a secret in a structure.

    Keys whose name hints at a secret have their value replaced with
    ``"<redacted>"``. Non-dict/list values pass through unchanged.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and any(hint in k.lower() for hint in _SECRET_HINTS):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ABTestManager:
    """Create, route, measure and analyse A/B tests for reasoning strategies."""

    def __init__(
        self,
        base_dir: str | None = None,
        *,
        min_samples_per_variant: int | None = None,
        significance_threshold: float | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.min_samples_per_variant = (
            min_samples_per_variant
            if min_samples_per_variant is not None
            else settings.ab_testing_min_samples_per_variant
        )
        self.significance_threshold = (
            significance_threshold
            if significance_threshold is not None
            else settings.ab_testing_significance_threshold
        )

    @property
    def enabled(self) -> bool:
        return settings.ab_testing_reasoning_enabled

    # ----- config -------------------------------------------------------

    def create_reasoning_test(
        self,
        name: str,
        variant_a: str,
        variant_b: str,
        traffic_split: float = 50.0,
        success_metrics: list[str] | None = None,
        task_types: list[str] | None = None,
        stratify_by_task_type: bool = False,
    ) -> ABTestConfig:
        if not name:
            raise ValueError("test name must not be empty")
        if variant_a == variant_b:
            raise ValueError("variant_a and variant_b must differ")
        if not (0.0 <= traffic_split <= 100.0):
            raise ValueError("traffic_split must be between 0 and 100")
        if read_config(name, self.base_dir) is not None:
            raise ValueError(f"test '{name}' already exists")
        metrics = success_metrics or list(DEFAULT_SUCCESS_METRICS)
        config = ABTestConfig(
            name=name,
            variant_a=variant_a,
            variant_b=variant_b,
            traffic_split=float(traffic_split),
            success_metrics=metrics,
            task_types=list(task_types or []),
            stratify_by_task_type=stratify_by_task_type,
            status="active",
        )
        append_record(config.to_dict(), self.base_dir)
        return config

    def get_config(self, name: str) -> ABTestConfig | None:
        data = read_config(name, self.base_dir)
        return ABTestConfig.from_dict(data) if data else None

    def list_active_tests(self) -> list[ABTestConfig]:
        out: list[ABTestConfig] = []
        for data in read_configs(self.base_dir):
            cfg = ABTestConfig.from_dict(data)
            if cfg.status == "active":
                out.append(cfg)
        return out

    def list_tests(self) -> list[ABTestConfig]:
        return [ABTestConfig.from_dict(d) for d in read_configs(self.base_dir)]

    def promote(self, name: str, variant: str) -> ABTestConfig:
        cfg = self.get_config(name)
        if cfg is None:
            raise ValueError(f"test '{name}' not found")
        if variant not in (VARIANT_A, VARIANT_B):
            raise ValueError("variant must be 'A' or 'B'")
        cfg.status = "completed"
        cfg.promoted_variant = variant
        append_record(cfg.to_dict(), self.base_dir)
        return cfg

    # ----- traffic splitting -------------------------------------------

    def _bucket(self, name: str, session_id: str, task_type: str | None) -> int:
        if task_type and self.get_config(name) and self.get_config(name).stratify_by_task_type:
            key = f"{session_id}|{name}|{task_type}"
        else:
            key = f"{session_id}|{name}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % 100

    def assign_variant(
        self, name: str, session_id: str, task_type: str | None = None
    ) -> str:
        """Return the variant label (``variant_a`` or ``variant_b``) for a request.

        Routing is deterministic and consistent for the same ``session_id``
        (hashed). When the test is disabled the control (variant A) is always
        returned. Stratified tests incorporate ``task_type`` into the hash so
        each (session, task_type) bucket is stable while keeping strata balanced.
        """
        cfg = self.get_config(name)
        if cfg is None:
            raise ValueError(f"test '{name}' not found")
        if not self.enabled or cfg.status != "active":
            return cfg.variant_a
        if cfg.task_types and task_type and task_type not in cfg.task_types:
            return cfg.variant_a
        bucket = self._bucket(name, session_id, task_type)
        if bucket < int(cfg.traffic_split):
            return cfg.variant_a
        return cfg.variant_b

    # ----- metrics ------------------------------------------------------

    def record_metric(self, event: MetricEvent) -> None:
        cfg = self.get_config(event.name)
        if cfg is None:
            raise ValueError(f"test '{event.name}' not found")
        if event.variant not in (cfg.variant_a, cfg.variant_b):
            raise ValueError("event.variant must match a configured variant")
        append_record(event.to_dict(), self.base_dir)

    def _metric_records(self, name: str) -> list[dict[str, Any]]:
        return query(kind="metric", name=name, base_dir=self.base_dir)

    # ----- analysis -----------------------------------------------------

    def _variant_stats(
        self, cfg: ABTestConfig, records: list[dict[str, Any]], variant_label: str
    ) -> VariantStats:
        rows = [r for r in records if r.get("variant") == variant_label]
        acc = [float(r["accuracy"]) for r in rows if r.get("accuracy") is not None]
        rel = [float(r["relevance"]) for r in rows if r.get("relevance") is not None]
        sat = [float(r["satisfaction"]) for r in rows if r.get("satisfaction") is not None]
        tok = [int(r["tokens"]) for r in rows if r.get("tokens") is not None]
        lat = [float(r["latency_ms"]) for r in rows if r.get("latency_ms") is not None]
        fb: dict[str, int] = {}
        for r in rows:
            f = r.get("feedback")
            if f:
                fb[f] = fb.get(f, 0) + 1

        def _mean(xs: list[float]) -> float | None:
            return sum(xs) / len(xs) if xs else None

        return VariantStats(
            variant=variant_label,
            label=variant_label,
            n=len(rows),
            accuracy=_mean(acc),
            relevance=_mean(rel),
            satisfaction=_mean(sat),
            avg_tokens=_mean([float(t) for t in tok]) if tok else None,
            avg_latency_ms=_mean(lat),
            feedback_counts=fb,
        )

    def analyze(self, name: str) -> ABReport:
        cfg = self.get_config(name)
        if cfg is None:
            raise ValueError(f"test '{name}' not found")
        records = self._metric_records(name)
        stats_a = self._variant_stats(cfg, records, cfg.variant_a)
        stats_b = self._variant_stats(cfg, records, cfg.variant_b)

        eligible = (
            stats_a.n >= self.min_samples_per_variant
            and stats_b.n >= self.min_samples_per_variant
        )

        significance: list[SignificanceResult] = []
        for metric in cfg.success_metrics:
            a_vals = [float(r[metric]) for r in records if r.get(metric) is not None and r.get("variant") == cfg.variant_a]
            b_vals = [float(r[metric]) for r in records if r.get(metric) is not None and r.get("variant") == cfg.variant_b]
            z, p = _two_sample_z_test(a_vals, b_vals)
            higher = (
                cfg.variant_a
                if (sum(a_vals) / len(a_vals) if a_vals else 0) > (sum(b_vals) / len(b_vals) if b_vals else 0)
                else cfg.variant_b
            ) if a_vals or b_vals else None
            significance.append(
                SignificanceResult(
                    metric=metric,
                    variant_a_mean=sum(a_vals) / len(a_vals) if a_vals else 0.0,
                    variant_b_mean=sum(b_vals) / len(b_vals) if b_vals else 0.0,
                    z_score=z,
                    p_value=p,
                    significant=p <= self.significance_threshold,
                    higher_variant=higher,
                )
            )

        winner, recommendation = self._decide(cfg, stats_a, stats_b, significance, eligible)
        return ABReport(
            name=name,
            status=cfg.status,
            promoted_variant=cfg.promoted_variant,
            eligible=eligible,
            min_samples_per_variant=self.min_samples_per_variant,
            significance_threshold=self.significance_threshold,
            variant_a=stats_a,
            variant_b=stats_b,
            significance=significance,
            winner=winner,
            recommendation=recommendation,
        )

    def _decide(
        self,
        cfg: ABTestConfig,
        stats_a: VariantStats,
        stats_b: VariantStats,
        significance: list[SignificanceResult],
        eligible: bool,
    ) -> tuple[str | None, str]:
        if not eligible:
            need = self.min_samples_per_variant
            return None, (
                f"Not enough samples yet (need >= {need} per variant; "
                f"have A={stats_a.n}, B={stats_b.n}). Keep collecting data."
            )
        primary = cfg.success_metrics[0] if cfg.success_metrics else None
        prim = next((s for s in significance if s.metric == primary), None)
        if prim is None:
            return None, "No success metrics configured; cannot determine a winner."
        if not prim.significant:
            return None, (
                f"No statistically significant difference on primary metric "
                f"'{primary}' (p={prim.p_value:.4f} >= {self.significance_threshold}). "
                "Continue the test or revisit the variants."
            )
        winner_label = prim.higher_variant
        margin = abs(prim.variant_a_mean - prim.variant_b_mean)
        return winner_label, (
            f"Promote variant {winner_label}: significantly better on "
            f"'{primary}' (p={prim.p_value:.4f}, margin={margin:.4f})."
        )

    # ----- maintenance --------------------------------------------------

    def cleanup(self, retention_days: int) -> int:
        return cleanup(retention_days, self.base_dir)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="jarvis-ab-test",
        description="A/B test reasoning strategies (create, status, report, promote).",
    )
    parser.add_argument("--create-reasoning", action="store_true",
                        help="Create a reasoning A/B test.")
    parser.add_argument("--name", type=str, help="Test name.")
    parser.add_argument("--variant-a", type=str, help="Control variant label (e.g. standard).")
    parser.add_argument("--variant-b", type=str, help="Treatment variant label (e.g. deep_thinking).")
    parser.add_argument("--traffic-split", type=float, default=50.0,
                        help="Percent of traffic routed to variant A (0-100).")
    parser.add_argument("--metrics", type=str, default="",
                        help="Comma-separated success metrics (default accuracy,relevance,satisfaction).")
    parser.add_argument("--task-types", type=str, default="",
                        help="Comma-separated task types for stratification (optional).")
    parser.add_argument("--stratify", action="store_true",
                        help="Stratify traffic split by task type.")
    parser.add_argument("--status", action="store_true", help="Show status of a test.")
    parser.add_argument("--report", action="store_true", help="Print analysis report for a test.")
    parser.add_argument("--promote", action="store_true", help="Promote a winning variant.")
    parser.add_argument("--variant", choices=("A", "B"), help="Variant to promote (A|B).")
    parser.add_argument("--storage-dir", type=str, default=None,
                        help="Override storage directory (default ./reports/ab_testing).")

    args = parser.parse_args(argv)

    def _require_name() -> str:
        if not args.name:
            print("ERROR: --name is required.", file=sys.stderr)
            return ""
        return args.name

    mgr = ABTestManager(base_dir=args.storage_dir)

    try:
        if args.create_reasoning:
            nm = _require_name()
            if not nm:
                return 2
            if not args.variant_a or not args.variant_b:
                print("ERROR: --variant-a and --variant-b are required.", file=sys.stderr)
                return 2
            metrics = [m.strip() for m in args.metrics.split(",") if m.strip()] or None
            ttypes = [t.strip() for t in args.task_types.split(",") if t.strip()] or None
            cfg = mgr.create_reasoning_test(
                nm, args.variant_a, args.variant_b,
                traffic_split=args.traffic_split,
                success_metrics=metrics, task_types=ttypes,
                stratify_by_task_type=args.stratify,
            )
            print(f"Created A/B test '{cfg.name}' (A={cfg.variant_a}, B={cfg.variant_b}, split={cfg.traffic_split}%).")
            return 0

        if args.status:
            nm = _require_name()
            if not nm:
                return 2
            cfg = mgr.get_config(nm)
            if cfg is None:
                print(f"ERROR: test '{nm}' not found.", file=sys.stderr)
                return 1
            print(json.dumps(redact(cfg.to_dict()), indent=2))
            return 0

        if args.report:
            nm = _require_name()
            if not nm:
                return 2
            report = mgr.analyze(nm)
            print(json.dumps(redact(report.to_dict()), indent=2))
            return 0

        if args.promote:
            nm = _require_name()
            if not nm:
                return 2
            if args.variant not in (VARIANT_A, VARIANT_B):
                print("ERROR: --variant A|B is required to promote.", file=sys.stderr)
                return 2
            cfg = mgr.promote(nm, args.variant)
            print(f"Promoted variant {args.variant} for test '{cfg.name}'.")
            return 0

        parser.print_help()
        return 0
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "VARIANT_A",
    "VARIANT_B",
    "DEFAULT_SUCCESS_METRICS",
    "ABTestConfig",
    "MetricEvent",
    "VariantStats",
    "SignificanceResult",
    "ABReport",
    "ABTestManager",
    "redact",
    "main",
]
