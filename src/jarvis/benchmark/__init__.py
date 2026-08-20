"""GPU / model load-test and benchmark framework (Phase 6).

Local-only by default: benchmarks never call OpenRouter, never start/stop
services, never touch production sessions/documents/tasks/approvals, and
clean up after themselves. All probes are best-effort — a missing
``nvidia-smi`` or ``ollama ps`` degrades the report gracefully instead of
failing it.
"""
from jarvis.benchmark.core import (
    BenchmarkReport,
    BenchmarkResult,
    build_context_fill,
    build_prompt,
    default_invoke,
    resolve_benchmark_models,
    run_benchmark_suite,
    safe_benchmark_prompts,
)

__all__ = [
    "BenchmarkReport",
    "BenchmarkResult",
    "build_context_fill",
    "build_prompt",
    "default_invoke",
    "resolve_benchmark_models",
    "run_benchmark_suite",
    "safe_benchmark_prompts",
]