"""Streamlit chat frontend for the Jarvis Assistant.

A modern, dark-themed chat UI on top of the FastAPI backend:

* Sidebar shows live model configuration, backend health, RAG store size,
  and an uploader for `.txt`/`.md` documents.
* Suggestions (pills) appear on an empty conversation to help first-time
  users; they vanish as soon as the first message is sent.
* A toolbar exposes "Show reasoning", answer-style, "Run as background
  task", and "Show debug info" toggles.
* Each assistant reply is annotated with badges for the branch path and
  the model that produced it, plus a "Tools used" line and RAG citations.
* Risky tool calls surface an inline Approve / Deny card before they run.
* Optional "select text -> ask follow-up" workflow: paste a snippet from
  the latest assistant reply, then ask a question framed around it.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
import streamlit as st

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("streamlit")

BASE_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
API_URL = f"{BASE_URL}/chat"
HEALTH_URL = f"{BASE_URL}/health"
MODELS_URL = f"{BASE_URL}/models"
DOCS_COUNT_URL = f"{BASE_URL}/documents/count"
DOCS_UPLOAD_URL = f"{BASE_URL}/documents/upload"
DOCS_LIST_URL = f"{BASE_URL}/documents"
DOCS_REINDEX_URL = f"{BASE_URL}/documents/reindex"
TASKS_URL = f"{BASE_URL}/tasks"
RUNTIME_URL = f"{BASE_URL}/runtime"
TRACES_URL = f"{BASE_URL}/traces/recent"


def _get_session_id() -> str:
    """Return a unique session ID for this browser session.

    Each Streamlit browser session gets its own stable UUID so that
    multiple users/tabs never share conversation state. The "default"
    session ID is only used when JARVIS_DEBUG_DEFAULT_SESSION=true is set
    (for local debugging only).
    """
    if os.environ.get("JARVIS_DEBUG_DEFAULT_SESSION", "").lower() == "true":
        return "default"
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    return st.session_state.session_id


# Session id this UI presents to the backend. Every request carries the
# bearer token issued for it so REQUIRE_SESSION_TOKEN stays a config-only
# switch for the operator. Unique per browser session to prevent
# cross-user conversation leakage and stale state.
SESSION_ID = _get_session_id()

SUGGESTIONS = {
    "Explain an idea": "Explain how retrieval-augmented generation works, simply.",
    "Write some code": "Write a Python function that returns the n-th Fibonacci number.",
    "Do the math": "Calculate (123 + 456) * 7 and explain the steps.",
    "Search the code": "Search the workspace for 'TODO' comments.",
}

ANSWER_STYLES = ["default", "concise", "detailed", "code", "teaching", "architecture", "research"]

st.set_page_config(
    page_title="Jarvis Assistant",
    page_icon=":material/smart_toy:",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------


@st.cache_data(ttl=30, show_spinner=False)
def fetch_models() -> dict | None:
    try:
        r = httpx.get(MODELS_URL, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_health() -> dict | None:
    try:
        r = httpx.get(HEALTH_URL, timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_doc_count() -> int | None:
    try:
        r = httpx.get(DOCS_COUNT_URL, timeout=5)
        r.raise_for_status()
        return int(r.json().get("count", 0))
    except Exception:
        return None


def session_token(session_id: str) -> str | None:
    """Return the bearer token for *session_id*, fetched once per run.

    The backend issues it via ``GET /sessions/{id}/token``. If the backend
    is unreachable we keep going token-less; enforcement is an operator
    opt-in and the chat request would 403 with a clear message if required.
    """
    cached = st.session_state.get(f"token_{session_id}")
    if cached:
        return cached
    try:
        r = httpx.get(f"{BASE_URL}/sessions/{session_id}/token", timeout=5)
        r.raise_for_status()
        token = r.json().get("session_token")
        st.session_state[f"token_{session_id}"] = token
        return token
    except Exception:  # noqa: BLE001
        logger.debug("Could not fetch session token for %s", session_id)
        return None


def fetch_trace_panel() -> list[dict] | None:
    try:
        r = httpx.get(TRACES_URL, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def fetch_runtime() -> dict | None:
    """Fetch the /runtime snapshot once on demand (never polled in a loop).

    Not cached by ``st.cache_data`` because the user refreshes it manually
    via the sidebar button — we don't want a TTL hiding a changed status.
    """
    try:
        r = httpx.get(RUNTIME_URL, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _runtime_mode_summary(snap: dict | None) -> dict:
    """Pure helper: turn a /runtime snapshot into display lines for the UI.

    Returns a dict with the fields the sidebar renders. Kept free of
    ``st.*`` calls so it can be unit-tested without importing Streamlit:
        {
          "available": bool,
          "mode": str,                  # local | docker
          "database_backend": str,
          "vector_store_backend": str,
          "task_backend": str,
          "docker_required": bool,
          "docker_detected": bool,
          "docker_containers": int,
          "wsl2_enabled": bool,
          "wsl_default_distro": str | None,
          "wsl_config_keys": list[str],  # present tuning keys, never values
          "warnings": list[str],
        }
    """
    rt = (snap or {}).get("runtime") or {}
    docker = (snap or {}).get("docker") or {}
    wsl = (snap or {}).get("wsl") or {}
    cfg_keys = wsl.get("config_keys") or {}
    return {
        "available": bool(rt),
        "mode": rt.get("runtime_mode", "local"),
        "database_backend": rt.get("database_backend", "?"),
        "vector_store_backend": rt.get("vector_store_backend", "?"),
        "task_backend": rt.get("task_backend", "?"),
        "docker_required": bool(rt.get("docker_required")),
        "docker_detected": bool(docker.get("daemon_reachable")),
        "docker_containers": len(docker.get("containers") or []),
        "wsl2_enabled": bool(wsl.get("wsl2_enabled")),
        "wsl_default_distro": wsl.get("default_distro"),
        "wsl_config_keys": [k for k, v in cfg_keys.items() if v],
        "warnings": list(rt.get("warnings") or []),
    }


def _gpu_policy_summary(snap: dict | None) -> dict:
    """Pure helper: GPU execution-policy display lines for the sidebar.

    Returns {"available": bool, "policy": str, "lines": list[str]} with the
    safe-GPU policy state so the UI can surface how models are allowed to
    run (never claims the live processor split — that's the /runtime
    ``processor`` field).
    """
    pol = ((snap or {}).get("gpu_policy") or {}) if snap else {}
    if not pol:
        return {"available": False, "policy": "prefer_gpu", "lines": []}
    lines = [
        f"Policy: `{pol.get('policy', 'prefer_gpu')}`",
        f"CPU fallback: {'allowed' if pol.get('allow_cpu_fallback') else 'blocked'}",
        f"VRAM cap: {pol.get('max_vram_percent')}% · min free: {pol.get('min_free_vram_mb')} MB",
        f"Strong-model partial offload: {'allowed' if pol.get('strong_model_allow_partial_offload') else 'not allowed'}",
        f"Runtime pre-checks: {'on' if pol.get('runtime_check_enabled') else 'off'}",
    ]
    return {"available": True, "policy": pol.get("policy", "prefer_gpu"), "lines": lines}


def export_conversation_to_markdown(messages: list[dict]) -> str:
    """Serialise the conversation to a Markdown string."""
    lines = ["# Jarvis Conversation Export\n"]
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        heading = "User" if role == "user" else "Assistant"
        lines.append(f"## {heading}\n")
        lines.append(f"{content}\n")
        if role == "assistant":
            meta_parts = []
            if m.get("path"):
                meta_parts.append(f"path: `{m['path']}`")
            if m.get("model"):
                meta_parts.append(f"model: `{m['model']}`")
            if m.get("tools_used"):
                meta_parts.append(f"tools: {', '.join(m['tools_used'])}")
            if meta_parts:
                lines.append(f"> {' | '.join(meta_parts)}\n")
    return "\n".join(lines)


def upload_documents(files) -> dict | None:
    try:
        parts = [("files", (f.name, f.read(), "text/plain")) for f in files]
        r = httpx.post(DOCS_UPLOAD_URL, files=parts, timeout=60)
        r.raise_for_status()
        st.cache_data.clear()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Upload failed: {exc}", icon=":material/error:")
        return None


def fetch_documents() -> list[dict]:
    try:
        r = httpx.get(DOCS_LIST_URL, timeout=10)
        r.raise_for_status()
        return r.json().get("documents", [])
    except Exception:  # noqa: BLE001
        return []


def delete_document(source: str) -> bool:
    try:
        r = httpx.delete(f"{DOCS_LIST_URL}/{source}?confirm=1", timeout=30)
        r.raise_for_status()
        st.cache_data.clear()
        return True
    except Exception as exc:  # noqa: BLE001
        st.error(f"Delete failed: {exc}", icon=":material/error:")
        return False


def reindex_documents() -> dict | None:
    try:
        r = httpx.post(DOCS_REINDEX_URL, json={"folder": None}, timeout=300)
        r.raise_for_status()
        st.cache_data.clear()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Reindex failed: {exc}", icon=":material/error:")
        return None


FEEDBACK_URL = f"{BASE_URL}/feedback"


def submit_feedback(
    score: str,
    *,
    question: str,
    answer: str,
    comment: str | None = None,
    model_used: str | None = None,
) -> bool:
    """Rate the last assistant reply. Returns True when the backend stored it."""
    try:
        r = httpx.post(
            FEEDBACK_URL,
            json={
                "session_id": SESSION_ID,
                "session_token": session_token(SESSION_ID),
                "question": question,
                "answer": answer,
                "score": score,
                "comment": comment,
                "model_used": model_used,
            },
            timeout=15,
        )
        r.raise_for_status()
        return bool(r.json().get("stored"))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not store feedback: {exc}", icon=":material/error:")
        return False


def create_task(description: str, session_id: str) -> dict | None:
    try:
        r = httpx.post(
            TASKS_URL,
            json={
                "description": description,
                "session_id": session_id,
                "session_token": session_token(session_id),
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as exc:
        try:
            body = exc.response.json()
            msg = body.get("message", "") or body.get("error", str(exc))
        except Exception:  # noqa: BLE001
            msg = str(exc)
        st.error(f"Could not start task: {msg}", icon=":material/error:")
        return None
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not start task: {exc}", icon=":material/error:")
        return None


def task_action(task_id: str, action: str) -> dict | None:
    """POST ``approve`` / ``deny`` / ``cancel`` for a background task."""
    try:
        r = httpx.post(f"{TASKS_URL}/{task_id}/{action}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Task {action} failed: {exc}", icon=":material/error:")
        return None


def poll_task(task_id: str, timeout: float = 295.0, interval: float = 2.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{TASKS_URL}/{task_id}", timeout=10)
            r.raise_for_status()
            data = r.json()
            if data["status"] in ("completed", "failed", "cancelled"):
                return data
        except Exception:
            pass
        time.sleep(interval)
    return None


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_action" not in st.session_state:
    st.session_state.pending_action = None
if "pending_tool_calls" not in st.session_state:
    st.session_state.pending_tool_calls = []
if "approval_id" not in st.session_state:
    st.session_state.approval_id = None
if "approval_expires_at" not in st.session_state:
    st.session_state.approval_expires_at = None
if "pending_selection" not in st.session_state:
    st.session_state.pending_selection = ""
if "selection_target_index" not in st.session_state:
    st.session_state.selection_target_index = None
if "toggles" not in st.session_state:
    st.session_state.toggles = {
        "show_reasoning": False,
        "answer_style": "default",
        "background_task": False,
        "debug": False,
        "deep_thinking": False,
        "show_reasoning_chain": False,
        "reasoning_strategy": "auto",
    }
# id of a background task currently being tracked live.
if "active_task_id" not in st.session_state:
    st.session_state.active_task_id = None


def _clear_selection() -> None:
    st.session_state.pending_selection = ""
    st.session_state.selection_target_index = None


def _clear_pending_approval() -> None:
    st.session_state.pending_action = None
    st.session_state.pending_tool_calls = []
    st.session_state.approval_id = None
    st.session_state.approval_expires_at = None


def _deny_pending_approval() -> str | None:
    """Tell the backend to mark this session's pending approval as denied.

    Returns the confirmation text on success, or a fallback string on any
    failure. The backend guarantees the durable row flips to ``denied`` so
    a later approve cannot resume the cancelled action.
    """
    try:
        r = httpx.post(
            API_URL,
            json={
                "session_id": SESSION_ID,
                "session_token": session_token(SESSION_ID),
                "message": "",
                "deny": True,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("response") or "Action cancelled by user."
    except Exception:  # noqa: BLE001
        logger.debug("Deny request failed; cancelling locally")
        return "Action cancelled by user."


def _assistant_record(answer: str, data: dict) -> dict:
    """Persist metadata alongside an assistant reply so re-renders show it."""
    return {
        "role": "assistant",
        "content": answer,
        "path": (data.get("path_used") or "").lower() or "unknown",
        "model": data.get("model_used"),
        "tools_used": list(data.get("tools_used", [])),
        "sources": list(data.get("sources", [])),
        "retrieved_context": data.get("retrieved_context"),
        "approval_required": bool(data.get("approval_required")),
        "deep_thinking_used": bool(data.get("deep_thinking_used")),
        "reasoning_strategy": data.get("reasoning_strategy"),
        "reasoning_steps": data.get("reasoning_steps", 0),
        "reasoning_chain": data.get("reasoning_chain"),
        "tokens_used_reasoning": data.get("tokens_used_reasoning", 0),
        "tokens_used_answer": data.get("tokens_used_answer", 0),
        "total_tokens": data.get("total_tokens", 0),
        "latency_ms_reasoning": data.get("latency_ms_reasoning", 0),
        "latency_ms_answer": data.get("latency_ms_answer", 0),
        "total_latency_ms": data.get("total_latency_ms", 0),
    }


def _render_feedback_buttons(idx: int) -> None:
    """Thumbs up / down rating buttons for one assistant reply."""
    msg = st.session_state.messages[idx]
    if msg.get("_feedback_sent"):
        st.caption(":material/check_circle: Thanks for the feedback!")
        return
    prev_question = ""
    if idx > 0 and st.session_state.messages[idx - 1].get("role") == "user":
        prev_question = st.session_state.messages[idx - 1]["content"]
    with st.container(horizontal=True):
        if st.button(":material/thumb_up:", key=f"fb-up-{idx}", help="Good answer"):
            if submit_feedback(
                "good",
                question=prev_question,
                answer=msg.get("content", ""),
                model_used=msg.get("model"),
            ):
                st.session_state.messages[idx]["_feedback_sent"] = True
                st.rerun()
        if st.button(":material/thumb_down:", key=f"fb-down-{idx}", help="Bad answer"):
            if submit_feedback(
                "bad",
                question=prev_question,
                answer=msg.get("content", ""),
                model_used=msg.get("model"),
            ):
                st.session_state.messages[idx]["_feedback_sent"] = True
                st.rerun()


def _render_assistant_meta(rec: dict, *, debug: bool) -> None:
    """Badges, tools line, citations, copy-code helper, reasoning chain, and debug expander."""
    path = rec.get("path", "unknown")
    path_color = {"general": "blue", "coding": "green", "complex": "violet"}.get(
        path, "gray"
    )
    with st.container(horizontal=True):
        st.badge(path, color=path_color)
        if model := rec.get("model"):
            st.badge(model, color="gray")

    tools = rec.get("tools_used") or []
    if tools:
        st.caption(f":material/build: Tools used: {', '.join(tools)}")

    sources = rec.get("sources") or []
    if sources:
        with st.expander(f"Sources ({len(sources)})", icon=":material/menu_book:"):
            for i, s in enumerate(sources, 1):
                src = s.get("source", "?")
                chunk = s.get("chunk_id", "")
                doc = (s.get("doc") or "").strip()
                if len(doc) > 220:
                    doc = doc[:217] + "..."
                st.markdown(f"**{i}. {src}**{(' — ' + chunk) if chunk else ''}")
                if doc:
                    st.caption(doc)

    if rec.get("deep_thinking_used"):
        steps = rec.get("reasoning_steps", 0)
        strategy = rec.get("reasoning_strategy") or "unknown"
        st.caption(f":material/psychology: Deep thinking ({strategy}, {steps} steps)")

    reasoning_chain = rec.get("_reasoning_chain")
    if reasoning_chain:
        with st.expander("Reasoning chain", icon=":material/account_tree:"):
            for step in reasoning_chain:
                step_num = step.get("step_number", "?")
                sub = step.get("sub_problem", "")
                analysis = step.get("analysis", "")
                conclusion = step.get("conclusion", "")
                st.markdown(f"**Step {step_num}:** {sub}")
                if analysis:
                    st.caption(f"Analysis: {analysis}")
                if conclusion:
                    st.caption(f"Conclusion: {conclusion}")
                st.divider()

    # Copy-code button for the assistant's reply content.
    content = rec.get("content", "")
    if content:
        with st.popover("Copy answer", icon=":material/content_copy:"):
            st.code(content, language="markdown")

    if debug:
        ctx = rec.get("retrieved_context") or ""
        with st.expander("Debug info", icon=":material/bug_report:"):
            st.caption(f"Path: `{path}` | Model: `{model or '-'}`")
            st.caption(f"Tools: {tools or '-'}")
            st.text("Retrieved context:")
            st.code(ctx or "(none)", language="text")


def _send_message(
    text: str,
    *,
    approved: bool = False,
    selected_text: str = "",
) -> None:
    """Send a message to the backend and append both turns to history."""
    display_user = text
    if selected_text:
        preview = (
            selected_text if len(selected_text) <= 120 else selected_text[:117] + "..."
        )
        display_user = f"### Selected text\n\n> {preview}\n\n### Question about selection\n\n{text}"

    if text:
        st.session_state.messages.append({"role": "user", "content": display_user})
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(display_user)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
        if text and m["role"] in ("user", "assistant")
    ]

    toggles = st.session_state.toggles
    show_reasoning = toggles["show_reasoning"]
    answer_style = toggles["answer_style"]
    background = toggles["background_task"]
    debug = toggles["debug"]
    deep_thinking = toggles["deep_thinking"]
    show_reasoning_chain = toggles["show_reasoning_chain"]
    reasoning_strategy = toggles["reasoning_strategy"]

    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        with st.spinner("Thinking..."):
            try:
                payload = {
                    "session_id": SESSION_ID,
                    "session_token": session_token(SESSION_ID),
                    "message": text,
                    "history": history,
                    "selected_text": selected_text or None,
                    "approved": approved,
                    "show_reasoning": show_reasoning,
                    "answer_style": answer_style if answer_style != "default" else None,
                    "deep_thinking": deep_thinking,
                    "show_reasoning_chain": show_reasoning_chain,
                    "reasoning_strategy": reasoning_strategy if reasoning_strategy != "auto" else None,
                }
                resp = httpx.post(API_URL, json=payload, timeout=300)
                resp.raise_for_status()
                data = resp.json()
                answer = data["response"]

                _render_assistant_meta(_assistant_record(answer, data), debug=debug)
                st.markdown(answer)

                if background and not data.get("approval_required") and not approved:
                    st.toast("Note: toggling 'Run as background task' posts a /tasks job next time.",
                             icon=":material/info:")

                if data.get("approval_required"):
                    st.session_state.pending_action = data.get("pending_action")
                    st.session_state.pending_tool_calls = list(data.get("pending_tool_calls") or [])
                    st.session_state.approval_id = data.get("approval_id")
                    st.session_state.approval_expires_at = data.get("approval_expires_at")
                    logger.info("Approval required: %s", data.get("pending_action"))
                else:
                    _clear_pending_approval()
            except httpx.HTTPStatusError as exc:
                try:
                    body = exc.response.json()
                    err = body.get("error", "request_failed")
                    msg = body.get("message", str(exc))
                    action = f"\n\n> Suggested: {body.get('suggested_action')}" if body.get("suggested_action") else ""
                    answer = f"**{err}** — {msg}{action}"
                except Exception:  # noqa: BLE001
                    answer = f"Backend error ({exc.response.status_code}): {exc}"
                st.error(answer, icon=":material/error:")
            except httpx.TimeoutException:
                answer = "This request is taking too long in interactive mode. Toggle 'Run as background task' for heavy prompts."
                st.error(answer, icon=":material/schedule:")
            except Exception as exc:  # noqa: BLE001
                answer = f"Error contacting backend: {exc}"
                st.error(answer, icon=":material/error:")

    if text:
        st.session_state.messages.append(_assistant_record(answer, data if "data" in locals() else {}))


def _run_background_task(description: str) -> None:
    """Post a /tasks job and open a live, auto-refreshing status card."""
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(description)
    st.session_state.messages.append({"role": "user", "content": description})

    started = create_task(description, SESSION_ID)
    if started is None:
        st.session_state.messages.append(
            {"role": "assistant", "content": "Failed to start background task."}
        )
        return
    st.session_state.active_task_id = started["id"]


@st.fragment(run_every=2)
def _render_task_card(task_id: str) -> None:
    """Live background-task card: progress, approval actions, terminal result.

    Polls ``GET /tasks/{id}`` every 2s and re-renders only this fragment, so
    the rest of the chat UI stays responsive. When the task hits a terminal
    state the result is folded into the conversation and the card clears.
    """
    try:
        r = httpx.get(f"{TASKS_URL}/{task_id}", timeout=10)
        r.raise_for_status()
        status = r.json()
    except Exception as exc:  # noqa: BLE001
        with st.container(border=True):
            st.caption(f":material/schedule: Task `{task_id}`")
            st.warning(f"Status unavailable: {exc}", icon=":material/error:")
        return

    state = status["status"]
    with st.container(border=True):
        st.markdown(f"**:material/schedule: Background task `{task_id[:10]}…`**")
        st.caption(status.get("description") or "")

        if state == "waiting_for_approval":
            st.badge("awaiting approval", color="orange")
            st.markdown(
                f"Jarvis wants to: `{status.get('pending_action') or 'perform an action'}`"
            )
            tool_calls = list(status.get("pending_tool_calls") or [])
            if tool_calls:
                for tc in tool_calls:
                    args = tc.get("args", {}) or {}
                    arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
                    st.code(f"{tc.get('name', '?')}({arg_str})", language="text")
            with st.container(horizontal=True):
                if st.button("Approve", type="primary", icon=":material/check:"):
                    task_action(task_id, "approve")
                    st.rerun(scope="fragment")
                if st.button("Deny", icon=":material/block:"):
                    task_action(task_id, "deny")
                    st.rerun(scope="fragment")
                if st.button("Cancel", icon=":material/close:"):
                    task_action(task_id, "cancel")
                    st.rerun(scope="fragment")
            return

        if state in ("queued", "running"):
            stage = status.get("stage") or ("queued" if state == "queued" else "running…")
            st.caption(f":material/running: {stage}")
            st.progress(0.35 if state == "running" else 0.05, text=stage)
            if st.button("Cancel task", icon=":material/stop:"):
                task_action(task_id, "cancel")
                st.rerun(scope="fragment")
            return

        if state == "cancelled":
            st.badge("cancelled", color="gray")
            st.caption(status.get("error") or "Cancelled.")
            if st.button("Dismiss", icon=":material/close:"):
                _finish_task_card(task_id, "cancelled", status)
                st.rerun(scope="fragment")
            return

        if state == "failed":
            st.badge("failed", color="red")
            st.error(status.get("error") or "Task failed.", icon=":material/error:")
            if st.button("Dismiss", icon=":material/close:"):
                _finish_task_card(task_id, "failed", status)
                st.rerun(scope="fragment")
            return

        # completed
        st.badge("completed", color="green")
        answer = status.get("result") or "(no output)"
        st.markdown(answer)
        if st.button("Dismiss", icon=":material/close:"):
            _finish_task_card(task_id, "completed", status)
            st.rerun(scope="fragment")


def _finish_task_card(task_id: str, state: str, status: dict) -> None:
    """Fold a terminal task's outcome into the conversation and clear the card."""
    if state == "completed":
        answer = status.get("result") or "(no output)"
        record = {
            "role": "assistant",
            "content": answer,
            "path": "background",
            "model": None,
            "tools_used": [],
            "sources": [],
            "retrieved_context": None,
            "approval_required": False,
        }
    else:
        answer = (
            f"Background task **{state}**"
            + (f": {status.get('error')}" if status.get("error") else "")
        )
        record = {
            "role": "assistant",
            "content": answer,
            "path": "background",
            "model": None,
            "tools_used": [],
            "sources": [],
            "retrieved_context": None,
            "approval_required": False,
        }
    st.session_state.messages.append(record)
    st.session_state.active_task_id = None


def _seconds_until(expires_at: str | None) -> int | None:
    """Whole seconds remaining until *expires_at* (ISO-8601), or None if unknown."""
    if not expires_at:
        return None
    try:
        dt = datetime.fromisoformat(expires_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
    except ValueError:
        return None


@st.fragment(run_every=1)
def _render_approval_card() -> None:
    """Inline card showing exactly what Jarvis wants to run.

    Lists each pending tool call (name + args), shows a live TTL countdown,
    and disables approval once the expiry has passed so the backend's 410
    guard and the UI never disagree.
    """
    remaining = _seconds_until(st.session_state.approval_expires_at)
    expired = remaining == 0

    with st.container(border=True):
        st.markdown("**:material/lock: Approval required**")
        action = st.session_state.pending_action or "perform an action"
        st.markdown(f"Jarvis wants to: `{action}`")

        tool_calls = st.session_state.pending_tool_calls or []
        if tool_calls:
            st.markdown("**Pending tool calls**")
            for tc in tool_calls:
                name = tc.get("name", "?")
                args = tc.get("args", {}) or {}
                arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) or "(no args)"
                st.code(f"{name}({arg_str})", language="text")

        if st.session_state.approval_id:
            st.caption(f"Approval ID: `{st.session_state.approval_id[:8]}…`")

        if expired:
            st.error(
                "This approval has expired. Re-ask Jarvis to try again.",
                icon=":material/expired:",
            )
            if st.button("Dismiss", icon=":material/close:"):
                _clear_pending_approval()
                st.rerun()
            return

        if remaining is not None:
            st.caption(f"Expires in {remaining}s")
        with st.container(horizontal=True):
            if st.button("Approve", type="primary", icon=":material/check:"):
                logger.info("User approved action: %s", st.session_state.pending_action)
                _clear_pending_approval()
                _send_message("", approved=True)
                st.rerun()
            if st.button("Deny", icon=":material/block:"):
                result = _deny_pending_approval()
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": result or "Action cancelled by user.",
                    }
                )
                _clear_pending_approval()
                st.rerun()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Jarvis Assistant")
    st.caption("Local-first hybrid AI assistant")

    health = fetch_health()
    if health and health.get("status") == "ok":
        st.badge("Backend online", icon=":material/check_circle:", color="green")
    else:
        st.badge("Backend offline", icon=":material/error:", color="red")
        st.caption(f"Expected at {BASE_URL}")

    st.subheader("Model configuration", divider=False)
    cfg = fetch_models()
    if cfg:
        st.caption("Local — Ollama")
        for key in ("general", "coding", "strong_local"):
            m = cfg.get(key, {})
            st.markdown(f"**{key}**: `{m.get('model', '?')}`")
        st.caption("Complex — OpenRouter")
        if cfg.get("complex", {}).get("configured"):
            for m in cfg["complex"]["models"]:
                st.code(m, language="text")
        else:
            st.caption("Not configured (no API key)")
    else:
        st.caption("Could not load model info")

    st.subheader("GPU Runtime", divider=False)
    if "runtime_snap" not in st.session_state:
        st.session_state.runtime_snap = None
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("Refresh", icon=":material/refresh:", help="Re-check Ollama and GPU status"):
            st.session_state.runtime_snap = fetch_runtime()
    with col1:
        if st.session_state.runtime_snap is None:
            st.session_state.runtime_snap = fetch_runtime()
    snap = st.session_state.runtime_snap
    if snap:
        if snap.get("ollama_reachable"):
            st.badge("Ollama online", icon=":material/check_circle:", color="green")
        else:
            st.badge("Ollama offline", icon=":material/error:", color="red")
        loaded = snap.get("model") or "(none)"
        st.caption(f"Loaded model: `{loaded}`")
        proc = snap.get("processor", "Unknown")
        proc_color = {
            "100% GPU": "green",
            "Partial CPU/GPU": "orange",
            "100% CPU": "red",
        }.get(proc, "gray")
        st.badge(proc, color=proc_color)
        if snap.get("gpu_name"):
            st.caption(f"GPU: {snap['gpu_name']}")
        vram_total = snap.get("vram_total_mb")
        vram_used = snap.get("vram_used_mb")
        if vram_total is not None and vram_used is not None:
            st.caption(f"VRAM: {vram_used} / {vram_total} MB")
        ctx = snap.get("context", {})
        st.caption(
            f"Context: num_ctx={ctx.get('num_ctx')} | history<= {ctx.get('history_max_turns')} turns | "
            f"budget={ctx.get('context_token_budget')}"
        )
        par = snap.get("parallel", {})
        st.caption(
            f"Parallel: num_parallel={par.get('num_parallel')} | max_loaded={par.get('max_loaded_models')}"
        )
        warns = snap.get("warnings") or []
        for w in warns[:5]:
            st.caption(f"Warning {w}")
        recs = snap.get("recommendations") or []
        for rec in recs:
            if "partial" in rec.lower() or "larger than" in rec.lower():
                st.warning(rec, icon=":material/warning:")
            elif "100% CPU" in rec or "No GPU offload" in rec:
                st.warning(rec, icon=":material/memory:")
            else:
                st.info(rec, icon=":material/lightbulb:")
        st.caption(
            "Tip: This optimization does **not** change your model. Models larger than dedicated "
            "VRAM may still need CPU/RAM. Run `ollama ps` and `nvidia-smi` to verify the split."
        )
    else:
        st.caption("Runtime diagnostics unavailable (backend /runtime not reachable).")

    st.subheader("Runtime mode", divider=False)
    snap = st.session_state.runtime_snap
    rt_sum = _runtime_mode_summary(snap)
    if rt_sum["available"]:
        if rt_sum["mode"] == "docker":
            st.badge("Docker", icon=":material/docker:", color="purple")
        else:
            st.badge("Local (no Docker)", icon=":material/memory:", color="green")
        st.caption(
            f"DB: `{rt_sum['database_backend']}` | Vector: `{rt_sum['vector_store_backend']}` | "
            f"Tasks: `{rt_sum['task_backend']}`"
        )
        if rt_sum["docker_required"]:
            st.caption("Docker **required** for this mode.")
            if not rt_sum["docker_detected"]:
                st.warning("Docker mode configured but the daemon is not reachable.", icon=":material/error:")
        else:
            st.caption("Docker **not required** — everything runs locally.")
        if rt_sum["docker_detected"]:
            st.caption(f"Docker daemon up · {rt_sum['docker_containers']} running container(s)")
        if rt_sum["wsl2_enabled"] or rt_sum["wsl_default_distro"]:
            st.caption(f"WSL: {'WSL2' if rt_sum['wsl2_enabled'] else 'WSL'} · default: {rt_sum['wsl_default_distro'] or 'none'}")
        if rt_sum["wsl_config_keys"]:
            st.caption("`.wslconfig` tuning keys present: " + ", ".join(rt_sum["wsl_config_keys"]))
        for w in rt_sum["warnings"][:3]:
            st.info(w, icon=":material/info:")
    else:
        st.caption("Runtime mode unavailable (backend /runtime not reachable).")

    st.subheader("GPU policy", divider=False)
    gp_sum = _gpu_policy_summary(st.session_state.runtime_snap)
    if gp_sum["available"]:
        for line in gp_sum["lines"]:
            st.caption(line)
    else:
        st.caption("GPU policy unavailable (backend /runtime not reachable).")

    st.subheader("RAG store", divider=False)
    count = fetch_doc_count()
    if count is None:
        st.caption("Vector store status unavailable")
    elif count == 0:
        st.caption("No documents indexed yet.")
        st.caption(
            "Run `uv run jarvis-ingest` to load `.txt` / `.md` files from `data/docs`."
        )
    else:
        st.metric("Indexed chunks", count)

    st.subheader("Upload documents", divider=False)
    with st.expander("Add .txt / .md files", icon=":material/upload:"):
        uploaded = st.file_uploader(
            "Drop files here",
            type=["txt", "md", "markdown", "rst"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )
        if uploaded and st.button("Ingest", icon=":material/play_arrow:"):
            res = upload_documents(uploaded)
            if res:
                st.success(
                    f"Ingested {len(res.get('files', []))} file(s) "
                    f"-> {res.get('chunks', 0)} chunk(s)."
                )

    st.subheader("Indexed documents", divider=False)
    docs = fetch_documents()
    if not docs:
        st.caption("No documents indexed yet.")
    else:
        for d in docs:
            cols = st.columns([5, 2, 2])
            cols[0].caption(f"`{d.get('source', '')}`")
            cols[1].caption(f"{d.get('chunk_count', 0)} chunk(s)")
            if cols[2].button(
                "Delete", key=f"del-{d.get('source')}", icon=":material/delete:"
            ):
                if delete_document(d.get("source", "")):
                    st.success(f"Deleted {d.get('source', '')}")
    with st.expander("Manage index", icon=":material/cleaning_services:"):
        if st.button("Reindex folder", icon=":material/refresh:"):
            res = reindex_documents()
            if res:
                st.success(
                    f"Reindexed {res.get('files', 0)} file(s) "
                    f"-> {res.get('chunks', 0)} chunk(s)."
                )

    st.subheader("Recent traces", divider=False)
    with st.expander("Trace debug panel", icon=":material/query_stats:"):
        trace_clicked = st.button(
            "Refresh traces", icon=":material/refresh:"
        )
        if trace_clicked:
            st.cache_data.clear()
        traces = fetch_trace_panel()
        if not traces:
            if trace_clicked:
                st.caption("No traces recorded yet — send a message and refresh.")
            else:
                st.caption("No traces recorded yet.")
        else:
            st.caption(f"{len(traces)} recent request(s).")
            for t in reversed(traces):
                path = t.get("path_used") or "unknown"
                model = t.get("selected_model") or "-"
                duration = t.get("duration_ms", 0)
                err = t.get("error")
                approval = t.get("approval_status") or "not_required"
                color = "red" if err else ("orange" if approval == "required" else "green")
                label = f"{t.get('intent') or '—'}/{path}"
                st.badge(label, color=color)
                gpu_policy = t.get("gpu_policy") or "-"
                split = t.get("processor_split") or "-"
                cost = t.get("estimated_cost_usd", 0.0)
                cloud = "cloud" if t.get("cloud_used") else "local"
                st.caption(
                    f"`{t.get('request_id', '')[:8]}…` {model} · {duration:.0f}ms · "
                    f"approval={approval} · {gpu_policy}/{split} · {cloud}"
                )
                if cost > 0:
                    st.caption(f":material/paid: estimated cost ${cost:.4f}")
                if err:
                    st.caption(f":material/error: {err}")
                st.divider()

    st.subheader("Performance analysis", divider=False)
    with st.expander("Deep thinking & reasoning performance", icon=":material/analytics:"):
        perf_url = f"{BASE_URL}/performance/summary"
        try:
            r = httpx.get(perf_url, timeout=10)
            if r.status_code == 200:
                perf_data = r.json()
                st.caption(f"Records: {perf_data.get('total_records', 0)}")
                for strategy, metrics in (perf_data.get("by_strategy") or {}).items():
                    count = metrics.get("count", 0)
                    acc = metrics.get("accuracy", {}).get("correctness", 0)
                    lat = metrics.get("efficiency", {}).get("latency_ms", 0)
                    st.caption(f"- `{strategy}`: count={count}, accuracy={acc:.2f}, latency={lat:.0f}ms")
            else:
                st.caption("Performance analysis unavailable.")
        except Exception:
            st.caption("Could not load performance data.")

    st.subheader("A/B testing", divider=False)
    with st.expander("Active reasoning A/B tests", icon=":material/science:"):
        ab_url = f"{BASE_URL}/ab-testing/active"
        try:
            r = httpx.get(ab_url, timeout=10)
            if r.status_code == 200:
                ab_data = r.json()
                tests = ab_data.get("tests", [])
                if tests:
                    for test in tests:
                        name = test.get("name", "unnamed")
                        va = test.get("variant_a", "A")
                        vb = test.get("variant_b", "B")
                        st.caption(f"`{name}`: {va} vs {vb}")
                else:
                    st.caption("No active A/B tests.")
            else:
                st.caption("A/B testing unavailable.")
        except Exception:
            st.caption("Could not load A/B test data.")

    with st.expander("Tips", icon=":material/lightbulb:"):
        st.markdown(
            "- Ask coding questions to route to the strong local coder.\n"
            "- Long or architecture-style prompts route to the cloud chain.\n"
            "- Select part of a reply, paste it below the reply, then ask about it.\n"
            "- Risky tool calls pause and ask for approval — in background tasks too.\n"
            "- Toggle 'Run as background task' for very long prompts; the task card\n"
            "  updates live and can be approved, denied, or cancelled from it.\n"
            "- The trace debug panel in the sidebar shows the last request pipeline."
        )

    if st.button("Clear conversation", icon=":material/delete:"):
        st.session_state.messages = []
        _clear_pending_approval()
        st.session_state.pending_selection = ""
        st.session_state.selection_target_index = None
        st.session_state.active_task_id = None
        st.rerun()

    if st.session_state.messages:
        md = export_conversation_to_markdown(st.session_state.messages)
        if st.download_button(
            "Export conversation (.md)",
            data=md.encode("utf-8"),
            file_name="jarvis_conversation.md",
            mime="text/markdown",
            icon=":material/download:",
        ):
            st.toast("Conversation exported.", icon=":material/download:")

# ---------------------------------------------------------------------------
# Conversation header + toggle toolbar
# ---------------------------------------------------------------------------

st.title("Chat with Jarvis")
st.caption(
    "General, coding, and complex tasks — routed to the right model automatically."
)

tg = st.session_state.toggles
with st.container(horizontal=True):
    st.toggle(
        "Reasoning",
        key="tgl_reasoning",
        value=tg["show_reasoning"],
        help="Ask the model for a short reasoning section before the answer.",
        on_change=lambda: tg.update({"show_reasoning": st.session_state.tgl_reasoning}),
    )
    st.toggle(
        "Deep thinking",
        key="tgl_deep",
        value=tg["deep_thinking"],
        help="Enable deep reasoning chain generation for complex questions.",
        on_change=lambda: tg.update({"deep_thinking": st.session_state.tgl_deep}),
    )
    st.toggle(
        "Show chain",
        key="tgl_chain",
        value=tg["show_reasoning_chain"],
        help="Show the reasoning chain in the response.",
        on_change=lambda: tg.update({"show_reasoning_chain": st.session_state.tgl_chain}),
    )
    st.selectbox(
        "Reasoning strategy",
        ["auto", "cot", "tot", "self_consistency", "reflexion", "fast_and_slow"],
        key="sel_reasoning",
        index=["auto", "cot", "tot", "self_consistency", "reflexion", "fast_and_slow"].index(
            tg["reasoning_strategy"]
        ),
        label_visibility="collapsed",
        on_change=lambda: tg.update({"reasoning_strategy": st.session_state.sel_reasoning}),
    )
    st.toggle(
        "Debug",
        key="tgl_debug",
        value=tg["debug"],
        help="Show retrieved context and metadata for each reply.",
        on_change=lambda: tg.update({"debug": st.session_state.tgl_debug}),
    )
    st.toggle(
        "Background",
        key="tgl_bg",
        value=tg["background_task"],
        help="Run very long prompts as a background /tasks job.",
        on_change=lambda: tg.update({"background_task": st.session_state.tgl_bg}),
    )
    st.selectbox(
        "Style",
        ANSWER_STYLES,
        key="sel_style",
        index=ANSWER_STYLES.index(tg["answer_style"]),
        label_visibility="collapsed",
        on_change=lambda: tg.update({"answer_style": st.session_state.sel_style}),
    )

# Suggestions on an empty conversation (pills vanish once a message lands).
if not st.session_state.messages:
    selected = st.pills(
        "Try asking",
        list(SUGGESTIONS.keys()),
        label_visibility="collapsed",
    )
    if selected:
        if tg["background_task"]:
            _run_background_task(SUGGESTIONS[selected])
        else:
            _send_message(SUGGESTIONS[selected])
        st.rerun()

# ---------------------------------------------------------------------------
# Render history
# ---------------------------------------------------------------------------

for idx, msg in enumerate(st.session_state.messages):
    avatar = ":material/person:" if msg["role"] == "user" else ":material/smart_toy:"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("path"):
            _render_assistant_meta(msg, debug=tg["debug"])
            if not msg.get("approval_required"):
                _render_feedback_buttons(idx)

# Retry button: re-send the last user message (drops the last assistant reply).
if (
    len(st.session_state.messages) >= 2
    and st.session_state.messages[-1]["role"] == "assistant"
    and not st.session_state.pending_action
    and not st.session_state.pending_selection
    and st.button("Retry last message", icon=":material/refresh:")
):
    last_user_idx = len(st.session_state.messages) - 2
    last_user_text = st.session_state.messages[last_user_idx]["content"]
    st.session_state.messages = st.session_state.messages[:last_user_idx]
    _send_message(last_user_text)
    st.rerun()

# ---------------------------------------------------------------------------
# Selection panel under the latest assistant message
# ---------------------------------------------------------------------------

latest_assistant_idx = None
for idx in range(len(st.session_state.messages) - 1, -1, -1):
    if st.session_state.messages[idx]["role"] == "assistant":
        latest_assistant_idx = idx
        break

if latest_assistant_idx is not None and not st.session_state.pending_action:
    with st.container(border=True):
        if st.session_state.pending_selection:
            sel = st.session_state.pending_selection
            preview = sel if len(sel) <= 100 else sel[:97] + "..."
            st.success(f"Asking about: _{preview}_")
            with st.container(horizontal=True):
                if st.button("Clear selection", icon=":material/close:"):
                    _clear_selection()
                    st.rerun()
        else:
            st.caption(
                "Want to ask about part of the response above? "
                "Select the text, copy it, paste below, then click **Use this selection**."
            )
            new_sel = st.text_area(
                "Paste selected text here (optional)",
                key=f"selection_input_{latest_assistant_idx}",
                height=80,
                label_visibility="collapsed",
                placeholder="Paste the snippet you want to ask about...",
            )
            if st.button("Use this selection", icon=":material/check:"):
                snippet = (new_sel or "").strip()
                if snippet:
                    st.session_state.pending_selection = snippet
                    st.session_state.selection_target_index = latest_assistant_idx
                    st.rerun()

# ---------------------------------------------------------------------------
# Approval UI
# ---------------------------------------------------------------------------

if st.session_state.pending_action:
    _render_approval_card()

if st.session_state.active_task_id:
    _render_task_card(st.session_state.active_task_id)

# ---------------------------------------------------------------------------
# Main chat input
# ---------------------------------------------------------------------------

pending_hint = (
    "Ask Jarvis about your selection..."
    if st.session_state.pending_selection
    else "Ask Jarvis something..."
)
user_input = st.chat_input(pending_hint, submit_mode="disable")

if user_input:
    selection = st.session_state.pending_selection
    if tg["background_task"]:
        _run_background_task(user_input)
    else:
        _send_message(user_input, selected_text=selection)
    if selection:
        _clear_selection()
