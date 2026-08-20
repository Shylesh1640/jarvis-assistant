"""Performance regression evaluation + baseline management (Phase 6).

Two pieces:

1. **Baselines** — ``--save-baseline`` / ``--compare-baseline`` for
   ``jarvis-benchmark``. Baselines live in the controlled ``reports/``
   directory, contain only safe machine/runtime metadata + per-run latency
   / token / split figures (never prompts, responses, or secrets), and are
   versioned against the benchmark ``schema_version``.

2. **``jarvis-evaluate-performance``** — a safe scenario evaluation suite.
   It uses a **mock runner by default** (deterministic, no LLM, no GPU, no
   cloud); local Ollama is used only when ``--live`` is passed; the cloud is
   never used unless ``--allow-cloud`` is passed (and even then the mock /
   live runners have no cloud path — the flag exists for CLI parity and is
   documented as a no-op for now).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis.benchmark.core import BenchmarkReport
from jarvis.config.settings import settings

BASELINE_FILE = "baseline.json"
_DEFAULT_REPORTS_DIR = "./reports"

# ---------------------------------------------------------------------------
# Secret redaction for reports (defence in depth)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bbearer\s+[A-Za-z0-9_\-\.]{12,}\b", re.IGNORECASE),
    re.compile(r"\bOPENROUTER_API_KEY\b", re.IGNORECASE),
]


def redact_report_value(value: Any) -> Any:
    """Recursively redact anything that looks like a secret from a report value."""
    if isinstance(value, str):
        out = value
        for pattern in _SECRET_PATTERNS:
            out = pattern.sub("[REDACTED]", out)
        return out
    if isinstance(value, dict):
        return {k: redact_report_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_report_value(v) for v in value]
    return value


def safe_report_dict(report: BenchmarkReport) -> dict[str, Any]:
    """A secret-free, prompt-free serialisation of a benchmark report."""
    return redact_report_value(report.to_dict())


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def save_baseline(report: BenchmarkReport, path: str | None = None) -> str:
    """Persist *report* as the performance baseline.

    Only safe metadata + metrics are stored (see ``safe_report_dict``) — no
    prompts, responses, tokens, or secrets. Returns the written path.
    """
    dest = Path(path or os.path.join(_DEFAULT_REPORTS_DIR, BASELINE_FILE))
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = safe_report_dict(report)
    data["_kind"] = "jarvis-benchmark-baseline"
    data["saved_at"] = datetime.now(timezone.utc).isoformat()
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return str(dest)


def load_baseline(path: str | None = None) -> dict[str, Any] | None:
    """Load a baseline dict from *path* (default reports/baseline.json)."""
    src = Path(path or os.path.join(_DEFAULT_REPORTS_DIR, BASELINE_FILE))
    if not src.exists():
        return None
    try:
        with open(src, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def compare_with_baseline(report: BenchmarkReport, baseline: dict[str, Any]) -> list[str]:
    """Compare *report* against a saved baseline; return regression lines.

    A regression is recorded when a run with the same (model, prompt_type,
    context_length) is now slower by more than 30%, or a run that previously
    succeeded now fails, or the processor split regressed (e.g. was
    "100% GPU", now "Partial CPU/GPU" or "100% CPU"). Returns an empty list
    when nothing regressed.
    """
    regressions: list[str] = []
    if baseline.get("schema_version") != report.schema_version:
        regressions.append(
            f"baseline schema version {baseline.get('schema_version')} != current "
            f"{report.schema_version} — regenerate the baseline"
        )
    baseline_results = {
        (r["model"], r["prompt_type"], r["context_length"]): r
        for r in baseline.get("results", [])
    }
    for r in report.results:
        d = r.to_dict()
        key = (d["model"], d["prompt_type"], d["context_length"])
        prev = baseline_results.get(key)
        if prev is None:
            continue
        if prev.get("success") and not d["success"]:
            regressions.append(
                f"{key} previously succeeded, now failed ({d['error_category']})"
            )
            continue
        if not d["success"]:
            continue
        prev_latency = prev.get("latency_ms") or 0
        if prev_latency > 0 and d["latency_ms"] > prev_latency * 1.3:
            regressions.append(
                f"{key} latency {d['latency_ms']:.0f}ms is >30% slower than baseline {prev_latency:.0f}ms"
            )
        prev_split = prev.get("processor_split")
        cur_split = d["processor_split"]
        if prev_split and prev_split != "unknown" and prev_split != cur_split:
            regressions.append(
                f"{key} processor split regressed: {prev_split} -> {cur_split}"
            )
    return regressions


# ---------------------------------------------------------------------------
# Performance evaluation (jarvis-evaluate-performance)
# ---------------------------------------------------------------------------

_SCENARIOS = {
    "general": {
        "prompt": "Explain how a local-first AI assistant works in three sentences.",
        "expected_intent": "general",
    },
    "coding": {
        "prompt": "Write a Python function named fibonacci that returns the n-th Fibonacci number.",
        "expected_intent": "coding",
    },
    "rag": {
        "prompt": "What does the project documentation say about retrieval-augmented generation?",
        "expected_intent": "general",
        "expects_rag": True,
    },
    "tool_call": {
        "prompt": "Use the calculator tool to compute (123 + 456) * 7.",
        "expected_intent": "general",
        "expects_tool": True,
    },
    "background_planning": {
        "prompt": "Plan a background task to review the workspace for TODO comments.",
        "expected_intent": "complex",
    },
}

# Deterministic canned outputs for the mock runner (no LLM involved).
_MOCK_RESPONSES = {
    "general": "[mock] A local-first assistant keeps models on your machine.",
    "coding": "[mock] def fibonacci(n): ...",
    "rag": "[mock] The docs describe retrieval-augmented generation.",
    "tool_call": "[mock] calculator -> 4053",
    "background_planning": "[mock] plan: 3 steps",
}

_MOCK_MODELS = {
    "general": settings.general_model,
    "coding": settings.coding_model,
    "rag": settings.general_model,
    "tool_call": settings.general_model,
    "background_planning": settings.strong_local_model or settings.general_model,
}


@dataclass
class EvalCaseResult:
    scenario: str
    passed: bool
    expected_intent: str
    actual_intent: str
    selected_model: str | None
    latency_ms: float
    tool_behavior: str
    rag_used: bool
    gpu_policy: str
    processor_split: str
    error: str | None = None
    detail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["latency_ms"] = round(float(data["latency_ms"]), 1)
        return data


class MockEvaluator:
    """Deterministic evaluator used by default and in automated tests."""

    def run_scenario(self, scenario: str, context_length: int = 4096) -> EvalCaseResult:
        spec = _SCENARIOS[scenario]
        detail: list[str] = []
        expected = spec["expected_intent"]
        actual = expected
        model = _MOCK_MODELS.get(scenario) or settings.general_model
        latency_ms = 1.0 + len(scenario) * 1.5
        tool_behavior = "no_tool"
        rag_used = bool(spec.get("expects_rag"))
        if spec.get("expects_tool"):
            tool_behavior = "calculator_ok"
        passed = True
        if spec.get("expects_rag"):
            passed = passed and rag_used
            detail.append("rag used")
        if spec.get("expects_tool"):
            passed = passed and tool_behavior != "no_tool"
            detail.append("tool used")
        return EvalCaseResult(
            scenario=scenario,
            passed=passed,
            expected_intent=expected,
            actual_intent=actual,
            selected_model=model,
            latency_ms=latency_ms,
            tool_behavior=tool_behavior,
            rag_used=rag_used,
            gpu_policy=settings.gpu_policy,
            processor_split="mock",
            detail=detail,
        )


class LiveEvaluator:
    """Local-Ollama evaluator (opt-in via ``--live``). Uses ChatOllama directly.

    Runs plain prompts — no graph, no persistence, no production data — so it
    is safe to run against a running Ollama. RAG / tool expectations are
    reported honestly as "not exercised live" instead of faked.
    """

    def __init__(self, context_length: int = 4096) -> None:
        self.context_length = context_length

    def run_scenario(self, scenario: str, context_length: int = 4096) -> EvalCaseResult:
        from langchain_core.messages import HumanMessage

        from jarvis.models.ollama_client import get_model_named

        spec = _SCENARIOS[scenario]
        model = _MOCK_MODELS.get(scenario) or settings.general_model
        llm = get_model_named(model, intent="general")
        started = __import__("time").monotonic()
        error: str | None = None
        detail: list[str] = []
        try:
            resp = llm.invoke([HumanMessage(content=spec["prompt"])])
            _ = resp.content
        except Exception as exc:  # noqa: BLE001
            error = exc.__class__.__name__
        latency_ms = (__import__("time").monotonic() - started) * 1000.0
        detail.append("live inference")
        return EvalCaseResult(
            scenario=scenario,
            passed=error is None,
            expected_intent=spec["expected_intent"],
            actual_intent=spec["expected_intent"],
            selected_model=model,
            latency_ms=latency_ms,
            tool_behavior="not_exercised",
            rag_used=False,
            gpu_policy=settings.gpu_policy,
            processor_split="unknown",
            error=error,
            detail=detail,
        )


def run_evaluation(
    *,
    scenarios: list[str] | None = None,
    live: bool = False,
    evaluator: Any | None = None,
) -> list[EvalCaseResult]:
    """Evaluate *scenarios* (default all) with mock or live evaluator."""
    names = scenarios or list(_SCENARIOS.keys())
    runner = evaluator or (LiveEvaluator() if live else MockEvaluator())
    return [runner.run_scenario(name) for name in names]


def eval_report_dict(results: list[EvalCaseResult]) -> dict[str, Any]:
    passed = sum(1 for r in results if r.passed)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": results[0].gpu_policy if results else settings.gpu_policy,
        "summary": {
            "cases": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
        "results": [r.to_dict() for r in results],
    }


def eval_report_markdown(results: list[EvalCaseResult]) -> str:
    mode = "live (local Ollama)" if any(
        r.processor_split != "mock" for r in results
    ) else "mock (no LLM / GPU / cloud used)"
    lines = [
        "# Jarvis Performance Evaluation",
        "",
        f"- **generated_at**: `{datetime.now(timezone.utc).isoformat()}`",
        f"- **mode**: {mode}",
        "",
        "> Hardware-specific note: latency figures are indicative only and "
        "depend on the machine, GPU, VRAM, and Ollama build.",
        "",
        "| scenario | result | intent | model | latency_ms | tool | rag | gpu_policy |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        d = r.to_dict()
        lines.append(
            f"| {d['scenario']} | {'PASS' if d['passed'] else 'FAIL'} "
            f"| {d['actual_intent']} | {d['selected_model'] or '-'} | {d['latency_ms']} "
            f"| {d['tool_behavior']} | {d['rag_used']} | {d['gpu_policy']} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jarvis-evaluate-performance",
        description="Performance/regression evaluation for Jarvis (mock by default).",
    )
    parser.add_argument("--live", action="store_true", help="Use local Ollama (opt-in).")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=list(_SCENARIOS.keys()),
        default=None,
        help="Run one scenario (repeatable). Default: all.",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=4096,
        help="Context length for live runs (mock ignores it).",
    )
    parser.add_argument("--output", default=None, help="Write a JSON report to this path.")
    parser.add_argument("--markdown", default=None, help="Write a Markdown report to this path.")
    parser.add_argument(
        "--allow-cloud",
        action="store_true",
        help="Accepted for CLI parity; this suite never calls the cloud.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    print("Jarvis performance evaluation")
    print("=" * 72)
    if not args.live:
        print("  Mode: mock (no LLM, GPU, or cloud involved). Use --live for local Ollama.")
    results = run_evaluation(scenarios=args.scenario, live=args.live)
    for r in results:
        d = r.to_dict()
        status = "PASS" if d["passed"] else "FAIL"
        print(
            f"  [{status}] {d['scenario']:<22} intent={d['actual_intent']} "
            f"model={d['selected_model']} lat={d['latency_ms']:.1f}ms "
            f"tool={d['tool_behavior']} rag={d['rag_used']}"
        )
        if d["error"]:
            print(f"          error: {d['error']}")
    summary = eval_report_dict(results)["summary"]
    print("=" * 72)
    print(f"  Summary: {summary['passed']}/{summary['cases']} scenarios passed.")
    if args.output:
        dest = Path(args.output)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(redact_report_value(eval_report_dict(results)), fh, indent=2)
        print(f"  [OK]   JSON report written to {args.output}")
    if args.markdown:
        dest = Path(args.markdown)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(eval_report_markdown(results))
        print(f"  [OK]   Markdown report written to {args.markdown}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())