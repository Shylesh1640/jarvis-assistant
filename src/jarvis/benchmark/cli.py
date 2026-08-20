"""CLI: ``jarvis-benchmark`` — safe local GPU / model load test.

Usage::

    uv run jarvis-benchmark                         # all configured local models
    uv run jarvis-benchmark --model qwen3:8b
    uv run jarvis-benchmark --context 4096
    uv run jarvis-benchmark --quick                 # general model @ 4096 only
    uv run jarvis-benchmark --output reports/benchmark.json
    uv run jarvis-benchmark --markdown reports/benchmark.md
    uv run jarvis-benchmark --save-baseline
    uv run jarvis-benchmark --compare-baseline [--baseline reports/baseline.json]

Safety contract (Phase 6):
    * Local models only — OpenRouter / cloud is never called.
    * Never starts/stops Ollama, Docker, WSL, FastAPI or Streamlit.
    * Never touches production sessions / documents / tasks / approvals;
      a throwaway ``bench-*`` session id is generated for reporting only.
    * All diagnostics are best-effort; a missing ``nvidia-smi`` or
      ``ollama ps`` degrades the report instead of failing it.
    * A single generation is time-capped at ``BENCHMARK_MAX_LATENCY_SECONDS``
      so a runaway model cannot hang the CLI.
    * When Ollama is unreachable the CLI exits non-zero with a structured,
      actionable result instead of a stack trace.
"""
from __future__ import annotations

import argparse
import os
import sys

from jarvis.benchmark.core import (
    BenchmarkReport,
    ollama_available,
    run_benchmark_suite,
)
from jarvis.benchmark.performance import (
    compare_with_baseline,
    load_baseline,
    save_baseline,
)
from jarvis.config.settings import settings

_EXIT_OK = 0
_EXIT_OLLAMA_UNAVAILABLE = 2
_EXIT_HARD_FAILURE = 3


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jarvis-benchmark",
        description="Safe local GPU/model load-test for Jarvis (local models only).",
    )
    parser.add_argument("--model", default=None, help="Benchmark one local model by name.")
    parser.add_argument(
        "--context",
        type=int,
        default=None,
        help="Run a single context size (overrides BENCHMARK_CONTEXT_SIZES).",
    )
    parser.add_argument(
        "--quick", action="store_true", help="Minimal run: general model @ 4096."
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write the full JSON report to this path (e.g. reports/benchmark.json).",
    )
    parser.add_argument(
        "--markdown",
        default=None,
        help="Write a Markdown report to this path (e.g. reports/benchmark.md).",
    )
    parser.add_argument(
        "--allow-cloud",
        action="store_true",
        help="Accepted for CLI parity; the benchmark is local-only and never calls the cloud.",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save this run as the performance baseline (reports/baseline.json).",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Baseline path for --compare-baseline (default reports/baseline.json).",
    )
    parser.add_argument(
        "--compare-baseline",
        action="store_true",
        help="Compare this run against a saved baseline and report regressions.",
    )
    return parser.parse_args(argv)


def _build_sizes(args: argparse.Namespace) -> list[int]:
    if args.context is not None:
        return [args.context]
    if args.quick:
        return [4096]
    return list(settings.benchmark_context_sizes_list)


def _write_json(path: str, report: BenchmarkReport) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report.to_json())
    print(f"  [OK]   JSON report written to {path}")


def _write_markdown(path: str, report: BenchmarkReport) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report.to_markdown())
    print(f"  [OK]   Markdown report written to {path}")


def _print_report(report: BenchmarkReport) -> None:
    print(f"  Session id (temporary): {report.session_id}")
    print(f"  Models tested: {', '.join(report.models_tested) or '-'}")
    print(f"  Context sizes: {', '.join(str(c) for c in report.context_sizes)}")
    print("-" * 72)
    for r in report.results:
        d = r.to_dict()
        status = "OK " if d["success"] else "FAIL"
        print(
            f"  [{status}] {d['model']} [{d['prompt_type']}] ctx={d['context_length']} "
            f"lat={d['latency_ms']:.0f}ms tok/s={d['tokens_per_second']:.1f} "
            f"split={d['processor_split']}"
        )
        if d["error_category"]:
            print(f"          error: {d['error_category']}")
        if d["warning"]:
            print(f"          warning: {d['warning']}")
    print("-" * 72)
    s = report.summary()
    print(
        f"  Summary: {s['succeeded']}/{s['runs']} runs succeeded; "
        f"avg latency {s['average_latency_ms']} ms; "
        f"avg throughput {s['average_tokens_per_second']} tok/s"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    print("Jarvis GPU benchmark (local models only)")
    print("=" * 72)

    reachable, warnings = ollama_available()
    if not reachable:
        print("  [FAIL] Ollama is not reachable. No benchmark was run.")
        for w in warnings:
            print(f"         {w}")
        print("  Suggested action: start Ollama (`ollama serve` or the desktop app),")
        print("  then re-run `uv run jarvis-benchmark`.")
        return _EXIT_OLLAMA_UNAVAILABLE

    sizes = _build_sizes(args)
    report = run_benchmark_suite(
        model=args.model,
        quick=args.quick,
        context_sizes=sizes,
        invoke_factory=None,
    )
    _print_report(report)

    if args.output:
        _write_json(args.output, report)
    if args.markdown:
        _write_markdown(args.markdown, report)

    if args.save_baseline:
        baseline_path = args.baseline or "reports/baseline.json"
        save_baseline(report, baseline_path)

    if args.compare_baseline:
        baseline_path = args.baseline or "reports/baseline.json"
        baseline = load_baseline(baseline_path)
        if baseline is None:
            print(f"  [FAIL] No baseline found at {baseline_path}. Run with --save-baseline first.")
            return _EXIT_HARD_FAILURE
        regressions = compare_with_baseline(report, baseline)
        if regressions:
            print("  Baseline comparison:")
            for line in regressions:
                print(f"    {line}")
            return _EXIT_HARD_FAILURE
        print("  Baseline comparison: no regressions detected.")

    failed = report.summary()["failed"]
    if failed:
        print(f"  {failed} benchmark run(s) failed — see the report for error categories.")
        return _EXIT_HARD_FAILURE
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())