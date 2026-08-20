"""Core benchmark logic: schema, safe prompts, diagnostics, orchestration.

Design goals
------------
* **Local only.** Nothing here calls OpenRouter or any cloud provider.
* **No side effects on production data.** The benchmark builds its own
  ``ChatOllama`` clients and never touches the persistence layer, so no
  sessions / documents / tasks / approvals are created or modified.
* **Never raises on missing hardware.** ``nvidia-smi`` missing, ``ollama
  ps`` unavailable, psutil absent — each degrades to ``None`` / ``unknown``
  with a warning rather than failing the run.
* **Time-bounded.** A single generation is capped at
  ``BENCHMARK_MAX_LATENCY_SECONDS``; an overrun is recorded as a timeout
  failure instead of hanging the CLI.
* **Fully injectable.** ``invoke`` and the diagnostic hooks are plain
  callables, so the test suite can drive the whole pipeline with mocks and
  never touch a real model or GPU.

Threshold behaviour
-------------------
``BENCHMARK_*`` thresholds are recorded on the result (``warning`` /
``threshold_violations``) and never fail the run unless they are configured.
A threshold is "configured" when its value is > 0 (or the latency cap which
is always enforced as a hard timeout).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from langchain_core.messages import HumanMessage

from jarvis.config.settings import Settings, settings
from jarvis.models.runtime_diagnostics import (
    check_ollama_reachable,
    classify_processor,
    get_gpu_info,
    get_ollama_process_info,
)

logger = logging.getLogger(__name__)

PROCESSOR_UNKNOWN = "unknown"

# Safe, boring benchmark prompts (no tools, no private data, no file access).
safe_benchmark_prompts: dict[str, str] = {
    "general": (
        "Write a short, clear explanation of how a local-first AI assistant "
        "works, in three sentences."
    ),
    "coding": (
        "Write a Python function named fibonacci that returns the n-th "
        "Fibonacci number. Return only the code, with no explanation."
    ),
    "long_context": (
        "Read the passage below, then answer in one sentence: what is the "
        "main idea?\n\n{fill}"
    ),
}

# A repetitive, innocuous filler paragraph used to pad long-context prompts.
_FILLER_SENTENCE = (
    "The quiet efficiency of a well-designed system lies in predictable, "
    "repeatable behaviour across many similar inputs."
)


def build_context_fill(text: str, target_tokens: int) -> str:
    """Pad *text* with innocuous filler so its estimated token count is >= target.

    Uses the same word-count proxy as the rest of the app
    (``estimate_tokens``), so the padding is approximate — good enough for a
    load test, never claimed to be a real tokenizer.
    """
    from jarvis.orchestration.context_window import estimate_tokens

    if target_tokens <= 0:
        return text
    sentences: list[str] = []
    while estimate_tokens("\n\n".join(sentences + [text])) < target_tokens:
        sentences.append(_FILLER_SENTENCE)
        if len(sentences) > 20_000:
            break  # safety valve against pathological config
    return "\n\n".join(sentences + [text])


def build_prompt(prompt_type: str, context_length: int) -> str:
    """Build the benchmark prompt for *prompt_type* at *context_length* tokens."""
    base = safe_benchmark_prompts.get(
        prompt_type, safe_benchmark_prompts["general"]
    )
    if prompt_type == "long_context":
        return build_context_fill(base, context_length)
    return base


def estimate_tokens_of(text: str) -> int:
    from jarvis.orchestration.context_window import estimate_tokens

    return estimate_tokens(text)


def default_invoke(model_name: str) -> Callable[[str], str]:
    """Build the default generation callable for *model_name*.

    Lazily constructs a ChatOllama via ``get_model_named`` (the same builder
    the branches use, so runtime options apply) and returns a function that
    sends a plain user prompt and returns the text reply.
    """
    from jarvis.models.ollama_client import get_model_named

    llm = get_model_named(model_name, intent="general")

    def _invoke(prompt: str) -> str:
        resp = llm.invoke([HumanMessage(content=prompt)])
        content = getattr(resp, "content", "")
        return content if isinstance(content, str) else str(content)

    return _invoke


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResult:
    """One (model, prompt_type, context_length) benchmark run."""

    model: str
    prompt_type: str
    context_length: int
    prompt_tokens_estimate: int
    response_tokens_estimate: int
    latency_ms: float
    tokens_per_second: float
    processor_split: str = PROCESSOR_UNKNOWN
    gpu_name: str | None = None
    gpu_vram_total_mb: int | None = None
    gpu_vram_used_before_mb: int | None = None
    gpu_vram_used_peak_mb: int | None = None
    system_ram_before_mb: int | None = None
    system_ram_peak_mb: int | None = None
    cpu_percent_peak: float | None = None
    success: bool = True
    error_category: str | None = None
    warning: str | None = None
    threshold_violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["latency_ms"] = round(float(data["latency_ms"]), 1)
        data["tokens_per_second"] = round(float(data["tokens_per_second"]), 2)
        data["cpu_percent_peak"] = (
            round(float(data["cpu_percent_peak"]), 1)
            if data["cpu_percent_peak"] is not None
            else None
        )
        return data


@dataclass
class BenchmarkReport:
    """A full benchmark suite report (JSON-serialisable, secret-free)."""

    session_id: str
    generated_at: str
    schema_version: str = "1.0"
    results: list[BenchmarkResult] = field(default_factory=list)
    thresholds: dict[str, Any] = field(default_factory=dict)
    models_tested: list[str] = field(default_factory=list)
    context_sizes: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "session_id": self.session_id,
            "models_tested": list(self.models_tested),
            "context_sizes": list(self.context_sizes),
            "thresholds": dict(self.thresholds),
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def summary(self) -> dict[str, Any]:
        total = len(self.results)
        ok = sum(1 for r in self.results if r.success)
        return {
            "runs": total,
            "succeeded": ok,
            "failed": total - ok,
            "average_latency_ms": (
                round(sum(r.latency_ms for r in self.results) / total, 1)
                if total
                else 0.0
            ),
            "average_tokens_per_second": (
                round(
                    sum(r.tokens_per_second for r in self.results if r.success) / ok, 2
                )
                if ok
                else 0.0
            ),
        }

    def to_markdown(self) -> str:
        lines: list[str] = [
            "# Jarvis Benchmark Report",
            "",
            f"- **generated_at**: `{self.generated_at}`",
            f"- **session_id**: `{self.session_id}` (temporary — nothing was persisted)",
            f"- **schema_version**: `{self.schema_version}`",
            f"- **models tested**: {', '.join(self.models_tested) or '—'}",
            f"- **context sizes**: {', '.join(str(c) for c in self.context_sizes) or '—'}",
            "",
            "> Results are **hardware-specific**: they describe this machine's GPU, "
            "VRAM, RAM and Ollama build only. Docker/WSL RAM is separate from "
            "Ollama GPU/Windows memory.",
            "",
            "## Results",
            "",
            "| model | prompt_type | context | prompt_tok | resp_tok | latency_ms | tok/s | split | gpu | success | error | warning |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in self.results:
            d = r.to_dict()
            lines.append(
                f"| {d['model']} | {d['prompt_type']} | {d['context_length']} "
                f"| {d['prompt_tokens_estimate']} | {d['response_tokens_estimate']} "
                f"| {d['latency_ms']} | {d['tokens_per_second']} | {d['processor_split']} "
                f"| {d['gpu_name'] or '-'} | {'yes' if d['success'] else 'no'} "
                f"| {d['error_category'] or '-'} | {(d['warning'] or '-')[:60]} |"
            )
        lines.append("")
        lines.append("## Summary")
        s = self.summary()
        lines.append(
            f"- runs: **{s['runs']}** (succeeded {s['succeeded']}, failed {s['failed']})"
        )
        lines.append(f"- average latency: **{s['average_latency_ms']} ms**")
        lines.append(
            f"- average throughput: **{s['average_tokens_per_second']} tokens/s**"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Thresholds (only applied when configured, i.e. value > 0)
# ---------------------------------------------------------------------------


def apply_thresholds(result: BenchmarkResult, s: Settings | None = None) -> BenchmarkResult:
    """Attach configured-threshold violations to *result* as warnings.

    Thresholds are conservative CPU / RAM / VRAM guards. They never fail a
    run outright — they are recorded so an operator can review.
    """
    cfg = s or settings
    violations: list[str] = []
    if cfg.benchmark_max_cpu_ram_mb > 0 and result.system_ram_peak_mb is not None:
        if result.system_ram_peak_mb > cfg.benchmark_max_cpu_ram_mb:
            violations.append(
                f"system RAM peak {result.system_ram_peak_mb} MB exceeded "
                f"BENCHMARK_MAX_CPU_RAM_MB={cfg.benchmark_max_cpu_ram_mb}"
            )
    if cfg.benchmark_max_vram_percent > 0 and result.gpu_vram_total_mb:
        used = result.gpu_vram_used_peak_mb or 0
        pct = 100.0 * used / result.gpu_vram_total_mb
        if pct > cfg.benchmark_max_vram_percent:
            violations.append(
                f"VRAM {used}/{result.gpu_vram_total_mb} MB ({pct:.1f}%) exceeded "
                f"BENCHMARK_MAX_VRAM_PERCENT={cfg.benchmark_max_vram_percent}"
            )
    if cfg.benchmark_min_gpu_utilization_percent > 0:
        if result.processor_split == "100% CPU":
            violations.append(
                "benchmark ran 100% on CPU — below "
                f"BENCHMARK_MIN_GPU_UTILIZATION_PERCENT={cfg.benchmark_min_gpu_utilization_percent}"
            )
        elif result.processor_split == "unknown":
            violations.append(
                "processor split could not be confirmed (nvidia-smi / ollama ps unavailable)"
            )
    result.threshold_violations = violations
    if violations and not result.warning:
        result.warning = "; ".join(violations)
    return result


# ---------------------------------------------------------------------------
# Diagnostics (safe, injectable, never raise)
# ---------------------------------------------------------------------------


def default_system_metrics() -> dict[str, Any]:
    """Return system RAM used (MB) and CPU percent via psutil, or None values."""
    try:
        import psutil  # type: ignore[import-not-found]

        vm = psutil.virtual_memory()
        return {
            "system_ram_used_mb": int(vm.used / (1024 * 1024)),
            "cpu_percent": float(psutil.cpu_percent(interval=0.1)),
        }
    except Exception:  # noqa: BLE001
        return {"system_ram_used_mb": None, "cpu_percent": None}


def processor_split_for(model: str, process_info_fn: Callable[[], tuple[list[dict], list[str]]]) -> str:
    """Classify the processor split for *model* from ``ollama ps`` rows.

    Returns the honest classification only when a matching row is found;
    otherwise ``unknown``. Never guesses.
    """
    try:
        rows, _ = process_info_fn()
    except Exception:  # noqa: BLE001
        return PROCESSOR_UNKNOWN
    model_lower = (model or "").lower()
    for row in rows or []:
        if model_lower in (row.get("name") or "").lower():
            raw = row.get("processor") or ""
            classified = classify_processor(raw)
            return classified if classified != "Unknown" else PROCESSOR_UNKNOWN
    return PROCESSOR_UNKNOWN


class _MetricsMonitor:
    """Background sampler that records peak RAM / CPU / VRAM during a run."""

    def __init__(
        self,
        *,
        gpu_info_fn: Callable[[], tuple[dict | None, list[str]]] | None = None,
        system_metrics_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.gpu_info_fn = gpu_info_fn or get_gpu_info
        self.system_metrics_fn = system_metrics_fn or default_system_metrics
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_ram_mb: int | None = None
        self.peak_cpu: float | None = None
        self.peak_vram_mb: int | None = None
        self.vram_before_mb: int | None = None
        self.vram_after_mb: int | None = None
        self.samples = 0

    def start(self) -> None:
        gpu, _ = self.gpu_info_fn()
        self.vram_before_mb = gpu.get("vram_used_mb") if gpu else None
        sysm = self.system_metrics_fn()
        self.peak_ram_mb = sysm.get("system_ram_used_mb")
        self.peak_cpu = sysm.get("cpu_percent")
        self.peak_vram_mb = self.vram_before_mb

        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    g, _ = self.gpu_info_fn()
                    if g and g.get("vram_used_mb") is not None:
                        used = g["vram_used_mb"]
                        self.peak_vram_mb = (
                            used if self.peak_vram_mb is None else max(self.peak_vram_mb, used)
                        )
                    m = self.system_metrics_fn()
                    if m.get("system_ram_used_mb") is not None:
                        ram = m["system_ram_used_mb"]
                        self.peak_ram_mb = (
                            ram if self.peak_ram_mb is None else max(self.peak_ram_mb, ram)
                        )
                    if m.get("cpu_percent") is not None:
                        cpu = m["cpu_percent"]
                        self.peak_cpu = (
                            cpu if self.peak_cpu is None else max(self.peak_cpu, cpu)
                        )
                    self.samples += 1
                except Exception:  # noqa: BLE001 — never let a sampler kill the run
                    pass
                time.sleep(0.2)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        gpu, _ = self.gpu_info_fn()
        self.vram_after_mb = gpu.get("vram_used_mb") if gpu else None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _invoke_with_timeout(
    invoke: Callable[[str], str],
    prompt: str,
    timeout_seconds: float,
) -> str:
    """Run *invoke* on a worker thread; raise TimeoutError if it overruns."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(invoke, prompt)
        try:
            return future.result(timeout=timeout_seconds)
        except TimeoutError:
            future.cancel()
            raise


def run_single_benchmark(
    *,
    model: str,
    prompt_type: str,
    context_length: int,
    invoke: Callable[[str], str],
    max_latency_seconds: float = 60.0,
    gpu_info_fn: Callable[[], tuple[dict | None, list[str]]] | None = None,
    process_info_fn: Callable[[], tuple[list[dict], list[str]]] | None = None,
    system_metrics_fn: Callable[[], dict[str, Any]] | None = None,
) -> BenchmarkResult:
    """Run one benchmark generation and return a fully-populated result.

    All diagnostics are injectable so tests can mock every probe. A missing
    ``nvidia-smi`` / ``ollama ps`` degrades fields to ``None``/``unknown``.
    """
    gpu_info_fn = gpu_info_fn or get_gpu_info
    process_info_fn = process_info_fn or get_ollama_process_info
    system_metrics_fn = system_metrics_fn or default_system_metrics

    prompt = build_prompt(prompt_type, context_length)
    prompt_tokens = estimate_tokens_of(prompt)

    monitor = _MetricsMonitor(
        gpu_info_fn=gpu_info_fn, system_metrics_fn=system_metrics_fn
    )
    monitor.start()
    started = time.monotonic()
    try:
        response = _invoke_with_timeout(invoke, prompt, max_latency_seconds)
        success, error_category = True, None
    except TimeoutError:
        response = ""
        success, error_category = False, "timeout"
    except Exception as exc:  # noqa: BLE001
        response = ""
        success, error_category = False, _error_category(exc)
    finally:
        latency_ms = (time.monotonic() - started) * 1000.0
        monitor.stop()

    response_tokens = estimate_tokens_of(response) if response else 0
    latency_s = max(latency_ms / 1000.0, 1e-9)
    tokens_per_second = response_tokens / latency_s if success else 0.0

    gpu, _ = gpu_info_fn()
    result = BenchmarkResult(
        model=model,
        prompt_type=prompt_type,
        context_length=context_length,
        prompt_tokens_estimate=prompt_tokens,
        response_tokens_estimate=response_tokens,
        latency_ms=latency_ms,
        tokens_per_second=tokens_per_second,
        processor_split=processor_split_for(model, process_info_fn),
        gpu_name=gpu.get("gpu_name") if gpu else None,
        gpu_vram_total_mb=gpu.get("vram_total_mb") if gpu else None,
        gpu_vram_used_before_mb=monitor.vram_before_mb,
        gpu_vram_used_peak_mb=monitor.peak_vram_mb,
        system_ram_before_mb=monitor.peak_ram_mb,
        system_ram_peak_mb=monitor.peak_ram_mb,
        cpu_percent_peak=monitor.peak_cpu,
        success=success,
        error_category=error_category,
    )
    if not success:
        result.warning = f"{error_category} during benchmark generation."
    return apply_thresholds(result)


def _error_category(exc: Exception) -> str:
    msg = str(exc).lower()
    if any(k in msg for k in ("connection", "refused", "unreachable")):
        return "ollama_unavailable"
    if any(k in msg for k in ("model not found", "does not exist", "not found")):
        return "model_not_found"
    if any(k in msg for k in ("oom", "out of memory", "cuda", "blastohm")):
        return "out_of_memory"
    return exc.__class__.__name__


def resolve_benchmark_models(
    s: Settings | None = None,
    model: str | None = None,
    *,
    quick: bool = False,
) -> list[dict[str, str]]:
    """Resolve which (model, prompt_type) pairs to benchmark.

    Defaults to the configured local models in a fixed order: general,
    coding, then the strong local model — the strong local model only when
    it is enabled/configured and different from the general model. Model
    names are read from settings; nothing is assumed or hard-coded.
    """
    cfg = s or settings
    out: list[dict[str, str]] = []
    if quick:
        if cfg.general_model:
            out.append({"name": cfg.general_model, "prompt_type": "general"})
        return out
    if model:
        return [{"name": model, "prompt_type": "general"}]
    seen: set[str] = set()
    candidates = [
        (cfg.general_model, "general"),
        (cfg.coding_model, "coding"),
    ]
    if cfg.use_strong_local and cfg.strong_local_model:
        candidates.append((cfg.strong_local_model, "long_context"))
    for name, prompt_type in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "prompt_type": prompt_type})
    return out


def new_benchmark_session_id() -> str:
    return f"bench-{uuid.uuid4().hex[:12]}"


def run_benchmark_suite(
    *,
    models: Iterable[dict[str, str]] | None = None,
    context_sizes: Iterable[int] | None = None,
    model: str | None = None,
    quick: bool = False,
    invoke_factory: Callable[[str], Callable[[str], str]] | None = None,
    gpu_info_fn: Callable[[], tuple[dict | None, list[str]]] | None = None,
    process_info_fn: Callable[[], tuple[list[dict], list[str]]] | None = None,
    system_metrics_fn: Callable[[], dict[str, Any]] | None = None,
    s: Settings | None = None,
) -> BenchmarkReport:
    """Run the full local benchmark suite and return a report.

    * ``models`` / ``context_sizes`` default to the configured local models
      and ``BENCHMARK_CONTEXT_SIZES``.
    * ``invoke_factory(model_name) -> Callable[[str], str]`` lets tests
      substitute a fake generation path; defaults to real ChatOllama.
    * Returns a ``BenchmarkReport`` even when Ollama is unavailable (every
      result then carries ``error_category``) so the CLI can fail with an
      actionable, structured result.
    """
    cfg = s or settings
    report = BenchmarkReport(
        session_id=new_benchmark_session_id(),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    report.thresholds = {
        "max_latency_seconds": cfg.benchmark_max_latency_seconds,
        "min_gpu_utilization_percent": cfg.benchmark_min_gpu_utilization_percent,
        "max_cpu_ram_mb": cfg.benchmark_max_cpu_ram_mb,
        "max_vram_percent": cfg.benchmark_max_vram_percent,
    }
    report.context_sizes = list(context_sizes) if context_sizes else list(cfg.benchmark_context_sizes_list)
    report.models_tested = [
        m["name"] for m in (models or resolve_benchmark_models(cfg, model, quick=quick))
    ]

    selected = list(models) if models else resolve_benchmark_models(cfg, model, quick=quick)
    if quick:
        sizes = [4096]
        report.context_sizes = sizes
    else:
        sizes = report.context_sizes

    invoke_factory = invoke_factory or (lambda name: default_invoke(name))
    max_latency = float(max(1, cfg.benchmark_max_latency_seconds))

    for entry in selected:
        name = entry["name"]
        prompt_type = entry.get("prompt_type", "general")
        invoke = invoke_factory(name)
        for ctx in sizes:
            result = run_single_benchmark(
                model=name,
                prompt_type=prompt_type,
                context_length=ctx,
                invoke=invoke,
                max_latency_seconds=max_latency,
                gpu_info_fn=gpu_info_fn,
                process_info_fn=process_info_fn,
                system_metrics_fn=system_metrics_fn,
            )
            report.results.append(result)
    return report


def ollama_available(base_url: str | None = None) -> tuple[bool, list[str]]:
    """Return (reachable, warnings) — used by the CLI for a fast pre-check."""
    return check_ollama_reachable(base_url=base_url)