"""Runtime / GPU diagnostics for the local Ollama + GPU stack.

All functions are *best-effort and never raise*: every failure path
returns a structured result with a ``warnings`` list so the API and UI
can surface partial information instead of crashing. Nothing here
exposes API keys or private document content.

Processor-split classification:
    "100% GPU"              — model fully offloaded to GPU (only when Ollama
                               literally reports an all-GPU split — we never
                               guess or over-claim)
    "Partial CPU/GPU"       — some layers on GPU, some on CPU
    "100% CPU"               — no GPU offload
    "Unknown"                — Ollama unreachable / no model loaded / can't tell

Windows approach:
    * ``ollama ps`` -> NAME / ID / SIZE / PROCESSOR (0.31 also has CONTEXT /
      UNTIL; the parser tolerates any subset of columns and never requires a
      STATUS column)
    * ``nvidia-smi --query-gpu=...`` for VRAM when available
    * Ollama HTTP ``GET /api/ps`` (with a POST fallback for older builds) and
      ``/api/version`` as a fallback

The structured snapshot shape (``get_runtime_snapshot``):

    {
      "ollama_reachable": bool,
      "model": str,                 # actually-loaded model, NOT configured name
      "active_model": str,          # alias of ``model`` (canonical UI field)
      "processor": "100% GPU" | "Partial CPU/GPU" | "100% CPU" | "Unknown",
      "gpu_name": str | None,
      "vram_total_mb": int | None,
      "vram_used_mb": int | None,
      "system_ram_used_mb": int | None,
      "warnings": list[str],
      "ollama_version": str | None,
      "running_models": list[dict],       # rich rows (name/size/expires_at)
      "running_model_names": list[str],   # canonical list of model names
      "context_length": int,              # model's CONTEXT if reported, else cfg
      "configured_models": dict,    # only non-secret names from settings
      "context": dict,              # num_ctx / num_batch / cap settings
      "parallel": dict,             # num_parallel / max_loaded_models
      "recommendations": list[str],
      "runtime": dict,              # capabilities object (runtime_mode,
                                    # database/vector/task backends,
                                    # docker_required / docker_detected)
      "docker": dict,               # Docker daemon/container/disk status
      "wsl": dict,                  # WSL distro/.wslconfig presence
    }
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from typing import Any

import httpx

from jarvis.config.runtime_capabilities import get_runtime_capabilities
from jarvis.config.settings import settings, validate_runtime_settings
from jarvis.models.platform_diagnostics import get_docker_wsl_diagnostics

logger = logging.getLogger(__name__)

# Brief timeout for the Ollama HTTP endpoint so we never block the request
# path when the server is down.
_HTTP_TIMEOUT = 3.0


# ---------------------------------------------------------------------------
# Low-level probes (each returns (data, warnings) and never raises)
# ---------------------------------------------------------------------------


def check_ollama_reachable(base_url: str | None = None) -> tuple[bool, list[str]]:
    """Return (reachable, warnings). Uses /api/version (cheap)."""
    url = (base_url or settings.ollama_base_url).rstrip("/")
    try:
        r = httpx.get(f"{url}/api/version", timeout=_HTTP_TIMEOUT)
        return r.status_code == 200, []
    except Exception as exc:  # noqa: BLE001
        return False, [f"Ollama unreachable at {url}: {exc.__class__.__name__}"]


def get_ollama_version(base_url: str | None = None) -> str | None:
    try:
        r = httpx.get(f"{(base_url or settings.ollama_base_url).rstrip('/')}/api/version", timeout=_HTTP_TIMEOUT)
        if r.status_code == 200:
            return str(r.json().get("version") or "")
    except Exception:  # noqa: BLE001
        return None
    return None


def get_ollama_running_models(base_url: str | None = None) -> tuple[list[dict], list[str]]:
    """Return (running_models, warnings) using the HTTP /api/ps endpoint.

    Primary request uses ``GET`` — Ollama 0.31 serves ``/api/ps`` on GET.
    A ``POST`` fallback keeps compatibility with older builds that only
    accepted POST. Each item: {"name": str, "size": int (bytes),
    "expires_at": str}
    """
    url = (base_url or settings.ollama_base_url).rstrip("/")
    response = None
    last_err: str | None = None
    try:
        response = httpx.get(f"{url}/api/ps", timeout=_HTTP_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        last_err = exc.__class__.__name__
        # Older Ollama servers rejected GET /api/ps (405) — retry with POST.
        try:
            response = httpx.post(f"{url}/api/ps", timeout=_HTTP_TIMEOUT, json={})
        except Exception as exc2:  # noqa: BLE001
            return [], [f"Ollama /api/ps failed: {exc2.__class__.__name__}"]
    if response.status_code == 200:
        try:
            data = response.json() if response.content else {}
        except Exception:  # noqa: BLE001 — malformed body → safe empty result
            return [], ["Ollama /api/ps returned malformed JSON"]
        models = (data or {}).get("models") or []
        out = []
        for m in models:
            out.append({
                "name": m.get("name") or m.get("model") or "",
                "size": int(m.get("size") or 0),
                "expires_at": m.get("expires_at") or "",
            })
        if len(models) > settings.ollama_max_loaded_models:
            return out, [
                f"Ollama has {len(models)} models loaded but OLLAMA_MAX_LOADED_MODELS={settings.ollama_max_loaded_models}."
            ]
        return out, []
    if last_err:
        return [], [f"Ollama /api/ps failed: {last_err}"]
    return [], [f"Ollama /api/ps returned HTTP {response.status_code}"]


def get_ollama_process_info() -> tuple[list[dict], list[str]]:
    """Run ``ollama ps`` and parse the loaded-model columns.

    Returns (rows, warnings) where each row is a dict with the columns the
    installed Ollama build printed (at minimum ``name``; optionally
    ``size``/``processor``/``context``/``until``/``status``/``model_id``).
    Works with the Ollama 0.31 column set — which drops ``STATUS`` and adds
    ``CONTEXT``/``UNTIL`` — as well as older variants.
    """
    if shutil.which("ollama") is None:
        return [], ["`ollama` CLI not found on PATH."]
    try:
        proc = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return [], [f"`ollama ps` failed: {exc.__class__.__name__}"]
    if proc.returncode != 0:
        return [], [f"`ollama ps` exited {proc.returncode}: {proc.stderr.strip()[:200]}"]
    return _parse_ollama_ps(proc.stdout), []


# Column names the parser understands, in left-to-right display order. Any
# subset may appear; a missing column yields a safe empty/default value.
_PS_COLUMNS = ("NAME", "ID", "SIZE", "PROCESSOR", "CONTEXT", "UNTIL", "STATUS")


def _parse_ollama_ps(stdout: str) -> list[dict]:
    """Parse `ollama ps` output rows into structured dicts.

    Locates the header line by the presence of a known column token, then
    slices each data row by the header column offsets so multi-word values
    (SIZE = "5.2 GB", PROCESSOR = "40%/60% CPU/GPU", UNTIL = "4 minutes
    ago") are split correctly. No STATUS column is required: whichever of
    the known columns exist in the header are parsed, and everything else
    falls back to a safe value instead of raising.
    """
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    rows: list[dict] = []
    if not lines:
        return rows

    header_idx, header = _find_ps_header(lines)
    if header_idx is None:
        return rows

    spans = _column_spans(header)
    if not spans:
        return rows

    for ln in lines[header_idx + 1:]:
        row: dict = _row_from_line(ln, spans)
        name = row.get("name", "")
        if not name:
            continue
        other_fields = {k: v for k, v in row.items() if k != "name" and isinstance(v, str) and v}
        if ":" not in name and not other_fields:
            continue  # not a structured data row (e.g. a stray caption line)
        rows.append(row)
    return rows


def _find_ps_header(lines: list[str]) -> tuple[int | None, str]:
    """Return (index, text) of the `ollama ps` header line, or (None, "")."""
    for i, ln in enumerate(lines):
        if "NAME" in ln.upper() or "PROCESSOR" in ln.upper():
            return i, ln
    return None, ""


def _column_spans(header: str) -> list[tuple[str, int]]:
    """Return sorted [(column_name, start_index)] from the header text.

    Uses word-boundary matching so a column like ``ID`` is never found as a
    substring of another header word. Columns absent from the header are
    simply not returned.
    """
    upper = header.upper()
    spans: list[tuple[str, int]] = []
    for col in _PS_COLUMNS:
        m = re.search(rf"\b{re.escape(col)}\b", upper)
        if m:
            spans.append((col, m.start()))
    return sorted(spans, key=lambda t: t[1])


def _row_from_line(line: str, spans: list[tuple[str, int]]) -> dict:
    """Slice *line* by column offsets and produce a safe row dict."""
    fields: dict[str, str] = {}
    for i, (col, start) in enumerate(spans):
        end = spans[i + 1][1] if i + 1 < len(spans) else None
        fields[col] = line[start:end].strip() if end is not None else line[start:].strip()

    return {
        "name": fields.get("NAME", ""),
        "model_id": fields.get("ID", ""),
        "size": fields.get("SIZE", ""),
        "processor": fields.get("PROCESSOR", ""),
        "context": fields.get("CONTEXT", ""),
        "until": fields.get("UNTIL", ""),
        "status": fields.get("STATUS", ""),
    }


def _parse_context_int(field: str) -> int | None:
    """Pull the first integer out of a CONTEXT column value, or None."""
    if not field:
        return None
    m = re.search(r"\d+", field)
    return int(m.group()) if m else None


def get_gpu_info() -> tuple[dict | None, list[str]]:
    """Query ``nvidia-smi`` if present. Returns (info, warnings).

    info = {"gpu_name": str, "vram_total_mb": int, "vram_used_mb": int}
    Returns (None, warnings) when nvidia-smi is unavailable.
    """
    if shutil.which("nvidia-smi") is None:
        return None, ["nvidia-smi not found on PATH (GPU VRAM metrics unavailable)."]
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return None, [f"nvidia-smi failed: {exc.__class__.__name__}"]
    if proc.returncode != 0:
        return None, [f"nvidia-smi exited {proc.returncode}: {proc.stderr.strip()[:200]}"]
    line = (proc.stdout or "").splitlines()[0].strip() if (proc.stdout or "").splitlines() else ""
    if not line:
        return None, ["nvidia-smi returned no output."]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 3:
        return None, [f"nvidia-smi output unreadable: {line[:120]}"]
    try:
        total = int(parts[1])
        used = int(parts[2])
    except ValueError:
        return None, [f"nvidia-smi numeric parse failed: {line[:120]}"]
    return {
        "gpu_name": parts[0],
        "vram_total_mb": total,
        "vram_used_mb": used,
    }, []


# ---------------------------------------------------------------------------
# Processor-split classifier
# ---------------------------------------------------------------------------


def classify_processor(processor_str: str) -> str:
    """Classify a processor string like '100% GPU' / '40%/60% CPU/GPU' / '100% CPU'.

    Returns one of: "100% GPU", "Partial CPU/GPU", "100% CPU", "Unknown".

    ``"100% GPU"`` is claimed **only** when Ollama literally reports an
    all-GPU split. Anything that mentions CPU alongside GPU, or a GPU
    percentage below 100, is honestly classified as partial — we never
    over-claim full GPU offload.
    """
    s = (processor_str or "").strip().lower()
    if not s:
        return "Unknown"
    mentions_gpu = "gpu" in s
    mentions_cpu = "cpu" in s
    if mentions_gpu and mentions_cpu:
        return "Partial CPU/GPU"
    if mentions_gpu:
        if "100% gpu" in s or s in ("gpu", "100%gpu"):
            return "100% GPU"
        return "Partial CPU/GPU"  # e.g. "90% GPU" → 10% is on CPU
    if mentions_cpu:
        return "100% CPU"
    return "Unknown"


# ---------------------------------------------------------------------------
# Aggregate snapshot (the shape the /runtime endpoint + UI consume)
# ---------------------------------------------------------------------------


def get_runtime_snapshot() -> dict[str, Any]:
    """Collect every probe into a single structured dict (never raises)."""
    warnings: list[str] = []
    reachable, reach_warns = check_ollama_reachable()
    warnings.extend(reach_warns)

    version = get_ollama_version() if reachable else None
    running, run_warns = get_ollama_running_models() if reachable else ([], [])
    warnings.extend(run_warns)

    ps_rows, ps_warns = get_ollama_process_info()
    warnings.extend(ps_warns)

    gpu, gpu_warns = get_gpu_info()
    warnings.extend(gpu_warns)

    warnings.extend(validate_runtime_settings())

    _append_container_diagnostic_warning(
        warnings,
        cli_missing=_probe_unavailable(ps_warns, "ollama"),
        gpu_missing=_probe_unavailable(gpu_warns, "nvidia-smi"),
    )

    # The actually-loaded model is the first row of `ollama ps` (or /api/ps).
    loaded_model = ""
    processor_raw = ""
    context_raw = ""
    if ps_rows:
        loaded_model = ps_rows[0].get("name", "")
        processor_raw = ps_rows[0].get("processor", "")
        context_raw = ps_rows[0].get("context", "")
    elif running:
        loaded_model = running[0].get("name", "")
    processor = classify_processor(processor_raw)
    if not reachable:
        processor = "Unknown"

    running_names = [m.get("name", "") for m in (running or ps_rows) if m.get("name")]

    recommendations = _recommendations(
        reachable=reachable,
        processor=processor,
        loaded_models_count=len(running) + len(ps_rows),
        vram=gpu,
    )

    # Runtime capabilities + Docker/WSL blocks (best-effort, never raises).
    docker_wsl = get_docker_wsl_diagnostics()
    docker_detected = docker_wsl["docker"].get("daemon_reachable", False)
    capabilities = get_runtime_capabilities(docker_reachable=docker_detected)
    warnings.extend(capabilities["warnings"])
    warnings.extend(docker_wsl["docker"].get("warnings", []))
    warnings.extend(docker_wsl["wsl"].get("warnings", []))

    snap: dict[str, Any] = {
        "ollama_reachable": reachable,
        "ollama_version": version,
        "model": loaded_model,
        "active_model": loaded_model,
        "processor": processor,
        "processor_raw": processor_raw,
        "gpu_name": gpu.get("gpu_name") if gpu else None,
        "vram_total_mb": gpu.get("vram_total_mb") if gpu else None,
        "vram_used_mb": gpu.get("vram_used_mb") if gpu else None,
        "system_ram_used_mb": _system_ram_used_mb(),
        "warnings": warnings,
        "recommendations": recommendations,
        "running_models": running or ps_rows,
        "running_model_names": running_names,
        "context_length": _parse_context_int(context_raw) or settings.ollama_context_length,
        "configured_models": {
            "general": settings.general_model,
            "strong_local": settings.strong_local_model,
            "coding": settings.coding_model,
            "coding_small": settings.coding_model_small,
            "embedding": settings.embedding_model,
            # NOTE: complex cloud models intentionally NOT included to avoid
            # leaking anything sensitive; they're already public model ids
            # but listing them here adds noise to the runtime view.
        },
        "context": {
            "num_ctx": settings.ollama_context_length,
            "num_batch": settings.ollama_num_batch,
            "history_max_turns": settings.history_max_turns,
            "context_token_budget": settings.context_token_budget,
            "rag_context_token_cap": settings.rag_context_token_cap,
            "selected_text_token_cap": settings.selected_text_token_cap,
            "retrieval_top_k": settings.retrieval_top_k,
            "rag_relevance_threshold": settings.rag_relevance_threshold,
        },
        "parallel": {
            "num_parallel": settings.ollama_num_parallel,
            "max_loaded_models": settings.ollama_max_loaded_models,
            "gpu_optimization_enabled": settings.gpu_optimization_enabled,
            "num_gpu": settings.ollama_num_gpu,
            "flash_attention": settings.ollama_flash_attention,
            "kv_cache_type": settings.ollama_kv_cache_type,
            "keep_alive": settings.ollama_keep_alive,
        },
        "gpu_policy": {
            "policy": settings.gpu_policy,
            "allow_cpu_fallback": settings.gpu_allow_cpu_fallback,
            "require_full_offload": settings.gpu_require_full_offload,
            "max_vram_percent": settings.gpu_max_vram_percent,
            "min_free_vram_mb": settings.gpu_min_free_vram_mb,
            "strong_model_allow_partial_offload": settings.gpu_strong_model_allow_partial_offload,
            "runtime_check_enabled": settings.gpu_runtime_check_enabled,
            "strong_local_model": settings.strong_local_model,
        },
        "runtime": capabilities,
        "docker": docker_wsl["docker"],
        "wsl": docker_wsl["wsl"],
    }
    return snap


def _recommendations(
    *, reachable: bool, processor: str, loaded_models_count: int, vram: dict | None
) -> list[str]:
    out: list[str] = []
    if not reachable:
        out.append("Start the Ollama server (`ollama serve` or the desktop app).")
        return out
    if loaded_models_count > 1:
        out.append(
            f"{loaded_models_count} models are loaded simultaneously — set OLLAMA_MAX_LOADED_MODELS=1 and OLLAMA_NUM_PARALLEL=1 to keep a single model in VRAM."
        )
    if processor == "Partial CPU/GPU":
        out.append(
            "The model is larger than available dedicated VRAM or the context allocation is too large. "
            "The application is using the maximum available GPU offload; complete GPU-only execution is not "
            "possible without more VRAM or a smaller model."
        )
    if processor == "100% CPU":
        out.append(
            "No GPU offload detected. The model is running entirely on CPU. Check that Ollama can see your GPU (`nvidia-smi`) and that GPU layers are not disabled."
        )
    if processor == "Unknown" and vram is None:
        out.append("GPU diagnostics unavailable — install/configure nvidia-smi or an Ollama build with GPU to report processor split.")
    if vram and vram.get("vram_total_mb"):
        used = vram.get("vram_used_mb", 0)
        total = vram["vram_total_mb"]
        if total > 0 and used / total > 0.95:
            out.append("VRAM is nearly full — reducing OLLAMA_CONTEXT_LENGTH will free KV cache memory.")
    return out


def _system_ram_used_mb() -> int | None:
    """Best-effort system-RAM estimate via psutil if available, else None."""
    try:
        import psutil  # type: ignore[import-not-found]

        vm = psutil.virtual_memory()
        return int(vm.used / (1024 * 1024))
    except Exception:  # noqa: BLE001
        return None


def _in_container() -> bool:
    """Best-effort container detection (Docker writes a sentinel file)."""
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def _probe_unavailable(warnings: list[str], tool: str) -> bool:
    """True when a probe warning says *tool* is not on PATH."""
    return any("not found" in w and tool in w for w in warnings)


def _append_container_diagnostic_warning(
    warnings: list[str],
    *,
    cli_missing: bool,
    gpu_missing: bool,
) -> None:
    """Consolidate the Docker limitation into one actionable warning.

    Inside the container the `ollama` CLI and `nvidia-smi` are usually absent,
    so `processor` / `gpu_name` / VRAM are reported as Unknown/null even though
    the host has a working GPU. We keep the individual probe warnings and add a
    single explanation with a fix, so the UI surfaces the reason instead of
    looking like a broken deployment.
    """
    if not _in_container():
        return
    if not cli_missing and not gpu_missing:
        return
    missing = []
    if cli_missing:
        missing.append("the `ollama` CLI")
    if gpu_missing:
        missing.append("`nvidia-smi`")
    warnings.append(
        "Running inside a container: "
        + " and ".join(missing)
        + " are not available on PATH, so processor-split / VRAM diagnostics "
        "show Unknown/null even though the host GPU exists. Run "
        "`jarvis-validate-runtime` on the host (outside Docker) for accurate "
        "GPU numbers, or mount the host binaries into the container."
    )


__all__ = [
    "check_ollama_reachable",
    "get_ollama_running_models",
    "get_ollama_process_info",
    "get_gpu_info",
    "get_runtime_snapshot",
    "classify_processor",
    "get_ollama_version",
]
