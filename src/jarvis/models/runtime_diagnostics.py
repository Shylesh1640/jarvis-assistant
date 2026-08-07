"""Runtime / GPU diagnostics for the local Ollama + GPU stack.

All functions are *best-effort and never raise*: every failure path
returns a structured result with a ``warnings`` list so the API and UI
can surface partial information instead of crashing. Nothing here
exposes API keys or private document content.

Processor-split classification:
    "100% GPU"              — model fully offloaded to GPU
    "Partial CPU/GPU"       — some layers on GPU, some on CPU
    "100% CPU"               — no GPU offload
    "Unknown"                — Ollama unreachable / no model loaded / can't tell

Windows approach:
    * ``ollama ps`` -> NAME / ID / SIZE / PROCESSOR / STATUS
    * ``nvidia-smi --query-gpu=...`` for VRAM when available
    * Ollama HTTP ``/api/ps`` and ``/api/version`` as a fallback

The structured snapshot shape (``get_runtime_snapshot``):

    {
      "ollama_reachable": bool,
      "model": str,                 # actually-loaded model, NOT configured name
      "processor": "100% GPU" | "Partial CPU/GPU" | "100% CPU" | "Unknown",
      "gpu_name": str | None,
      "vram_total_mb": int | None,
      "vram_used_mb": int | None,
      "system_ram_used_mb": int | None,
      "warnings": list[str],
      "ollama_version": str | None,
      "running_models": list[dict],
      "configured_models": dict,    # only non-secret names from settings
      "context": dict,              # num_ctx / num_batch / cap settings
      "parallel": dict,             # num_parallel / max_loaded_models
      "recommendations": list[str],
    }
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import Any

import httpx

from jarvis.config.settings import settings, validate_runtime_settings

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

    Each item: {"name": str, "size": int (bytes), "expires_at": str}
    """
    url = (base_url or settings.ollama_base_url).rstrip("/")
    try:
        r = httpx.post(f"{url}/api/ps", timeout=_HTTP_TIMEOUT, json={})
        if r.status_code == 200:
            data = r.json() or {}
            models = data.get("models") or []
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
        return [], [f"Ollama /api/ps returned HTTP {r.status_code}"]
    except Exception as exc:  # noqa: BLE001
        return [], [f"Ollama /api/ps failed: {exc.__class__.__name__}"]


def get_ollama_process_info() -> tuple[list[dict], list[str]]:
    """Run ``ollama ps`` and parse the PROCESSOR column.

    Returns (rows, warnings) where each row is:
        {"name": str, "size": str, "processor": str, "status": str}
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


_PS_HEADER = re.compile(r"NAME\s+ID\s+SIZE\s+PROCESSOR\s+STATUS", re.IGNORECASE)


def _parse_ollama_ps(stdout: str) -> list[dict]:
    """Parse `ollama ps` output rows into structured dicts.

    Uses column offsets from the header line so multi-word values (SIZE =
    "5.2 GB", PROCESSOR = "40%/60% CPU/GPU") are split correctly.
    """
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    rows: list[dict] = []
    if not lines:
        return rows
    header_idx = 0
    for i, ln in enumerate(lines):
        if _PS_HEADER.search(ln):
            header_idx = i
            break
    header = lines[header_idx]
    # Find column start indices for NAME, ID, SIZE, PROCESSOR, STATUS.
    cols = {}
    for col in ("NAME", "ID", "SIZE", "PROCESSOR", "STATUS"):
        idx = header.upper().find(col)
        if idx == -1:
            return rows  # can't parse
        cols[col] = idx
    for ln in lines[header_idx + 1:]:
        # Each field spans from its column start to the next column start.
        name = ln[cols["NAME"]:cols["ID"]].strip()
        model_id = ln[cols["ID"]:cols["SIZE"]].strip()
        size = ln[cols["SIZE"]:cols["PROCESSOR"]].strip()
        # PROCESSOR spans until STATUS column, but the row may be shorter
        # than the STATUS column, so we clamp.
        end = min(cols["STATUS"], len(ln))
        processor = ln[cols["PROCESSOR"]:end].strip()
        status = ln[cols["STATUS"]:].strip() if cols["STATUS"] < len(ln) else ""
        if not name:
            continue
        rows.append({
            "name": name,
            "size": size,
            "processor": processor,
            "status": status,
        })
    return rows


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
    """
    s = (processor_str or "").strip().lower()
    if not s:
        return "Unknown"
    if "gpu" in s and "cpu" not in s:
        if "100%" in s:
            return "100% GPU"
        # e.g. "90% GPU" still best-effort full GPU
        return "100% GPU"
    if "cpu" in s and "gpu" not in s:
        return "100% CPU"
    if "cpu" in s and "gpu" in s:
        return "Partial CPU/GPU"
    if "100%" in s and "gpu" in s:
        return "100% GPU"
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

    # The actually-loaded model is the first row of `ollama ps` (or /api/ps).
    loaded_model = ""
    processor_raw = ""
    if ps_rows:
        loaded_model = ps_rows[0].get("name", "")
        processor_raw = ps_rows[0].get("processor", "")
    elif running:
        loaded_model = running[0].get("name", "")
    processor = classify_processor(processor_raw)
    if not reachable:
        processor = "Unknown"

    recommendations = _recommendations(
        reachable=reachable,
        processor=processor,
        loaded_models_count=len(running) + len(ps_rows),
        vram=gpu,
    )

    snap: dict[str, Any] = {
        "ollama_reachable": reachable,
        "ollama_version": version,
        "model": loaded_model,
        "processor": processor,
        "processor_raw": processor_raw,
        "gpu_name": gpu.get("gpu_name") if gpu else None,
        "vram_total_mb": gpu.get("vram_total_mb") if gpu else None,
        "vram_used_mb": gpu.get("vram_used_mb") if gpu else None,
        "system_ram_used_mb": _system_ram_used_mb(),
        "warnings": warnings,
        "recommendations": recommendations,
        "running_models": running or ps_rows,
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
        },
        "parallel": {
            "num_parallel": settings.ollama_num_parallel,
            "max_loaded_models": settings.ollama_max_loaded_models,
            "gpu_optimization_enabled": settings.gpu_optimization_enabled,
            "flash_attention": settings.ollama_flash_attention,
            "kv_cache_type": settings.ollama_kv_cache_type,
            "keep_alive": settings.ollama_keep_alive,
        },
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


__all__ = [
    "check_ollama_reachable",
    "get_ollama_running_models",
    "get_ollama_process_info",
    "get_gpu_info",
    "get_runtime_snapshot",
    "classify_processor",
    "get_ollama_version",
]
