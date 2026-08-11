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
TASKS_URL = f"{BASE_URL}/tasks"
RUNTIME_URL = f"{BASE_URL}/runtime"

SUGGESTIONS = {
    "Explain an idea": "Explain how retrieval-augmented generation works, simply.",
    "Write some code": "Write a Python function that returns the n-th Fibonacci number.",
    "Do the math": "Calculate (123 + 456) * 7 and explain the steps.",
    "Search the code": "Search the workspace for 'TODO' comments.",
}

ANSWER_STYLES = ["default", "concise", "detailed", "code", "teaching", "architecture"]

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


def create_task(description: str, session_id: str) -> dict | None:
    try:
        r = httpx.post(
            TASKS_URL,
            json={"description": description, "session_id": session_id},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not start task: {exc}", icon=":material/error:")
        return None


def poll_task(task_id: str, timeout: float = 295.0, interval: float = 2.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{TASKS_URL}/{task_id}", timeout=10)
            r.raise_for_status()
            data = r.json()
            if data["status"] in ("completed", "failed"):
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
    }


def _clear_selection() -> None:
    st.session_state.pending_selection = ""
    st.session_state.selection_target_index = None


def _clear_pending_approval() -> None:
    st.session_state.pending_action = None
    st.session_state.pending_tool_calls = []
    st.session_state.approval_id = None
    st.session_state.approval_expires_at = None


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
    }


def _render_assistant_meta(rec: dict, *, debug: bool) -> None:
    """Badges, tools line, citations, copy-code helper, and debug expander."""
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

    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        with st.spinner("Thinking..."):
            try:
                payload = {
                    "session_id": "default",
                    "message": text,
                    "history": history,
                    "selected_text": selected_text or None,
                    "approved": approved,
                    "show_reasoning": show_reasoning,
                    "answer_style": answer_style if answer_style != "default" else None,
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
            except httpx.TimeoutException:
                answer = "This request is taking too long in interactive mode. Toggle 'Run as background task' for heavy prompts."
                st.error(answer, icon=":material/schedule:")
            except Exception as exc:  # noqa: BLE001
                answer = f"Error contacting backend: {exc}"
                st.error(answer, icon=":material/error:")

    if text:
        st.session_state.messages.append(_assistant_record(answer, data if "data" in locals() else {}))


def _run_background_task(description: str) -> None:
    """Post a /tasks job, poll it, and append the result as an assistant turn."""
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(description)
    st.session_state.messages.append({"role": "user", "content": description})

    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        st.badge("background", color="orange")
        started = create_task(description, "default")
        if started is None:
            st.session_state.messages.append(
                {"role": "assistant", "content": "Failed to start background task."}
            )
            return
        task_id = started["id"]
        st.caption(f"Task submitted: `{task_id}` — polling…")
        with st.spinner("Running in background…"):
            result = poll_task(task_id)
        if result is None:
            answer = "Background task is still running. Poll it later via GET /tasks/{id}."
            st.warning(answer, icon=":material/schedule:")
            record = {"role": "assistant", "content": answer, "path": "background"}
        elif result["status"] == "failed":
            answer = f"Task failed: {result.get('error')}"
            st.error(answer, icon=":material/error:")
            record = {"role": "assistant", "content": answer, "path": "background"}
        else:
            answer = result.get("result") or "(no output)"
            st.markdown(answer)
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
                st.session_state.messages.append(
                    {"role": "assistant", "content": "Action cancelled by user."}
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

    with st.expander("Tips", icon=":material/lightbulb:"):
        st.markdown(
            "- Ask coding questions to route to the strong local coder.\n"
            "- Long or architecture-style prompts route to the cloud chain.\n"
            "- Select part of a reply, paste it below the reply, then ask about it.\n"
            "- Risky tool calls pause and ask for approval.\n"
            "- Toggle 'Run as background task' for very long prompts."
        )

    if st.button("Clear conversation", icon=":material/delete:"):
        st.session_state.messages = []
        _clear_pending_approval()
        st.session_state.pending_selection = ""
        st.session_state.selection_target_index = None
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
