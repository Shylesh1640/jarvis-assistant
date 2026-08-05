"""Streamlit chat frontend for the Jarvis Assistant.

A modern, dark-themed chat UI on top of the FastAPI backend:

* Sidebar shows live model configuration, backend health, and the current
  size of the RAG document store.
* Suggestions (pills) appear on an empty conversation to help first-time
  users; they vanish as soon as the first message is sent.
* Each assistant reply is annotated with badges for the branch path and
  the model that produced it.
* Risky tool calls surface an inline Approve / Deny card before they run.
* Optional "select text -> ask follow-up" workflow: paste a snippet from
  the latest assistant reply, then ask a question framed around it.
"""

from __future__ import annotations

import logging

import httpx
import streamlit as st

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("streamlit")

BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/chat"
HEALTH_URL = f"{BASE_URL}/health"
MODELS_URL = f"{BASE_URL}/models"
DOCS_URL = f"{BASE_URL}/documents/count"

# First-message suggestions. Each label maps to the prompt actually sent.
SUGGESTIONS = {
    "Explain an idea": "Explain how retrieval-augmented generation works, simply.",
    "Write some code": "Write a Python function that returns the n-th Fibonacci number.",
    "Do the math": "Calculate (123 + 456) * 7 and explain the steps.",
}

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
        r = httpx.get(DOCS_URL, timeout=5)
        r.raise_for_status()
        return int(r.json().get("count", 0))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

st.session_state.setdefault("messages", [])
st.session_state.setdefault("pending_action", None)
# Snippet the user wants their next chat input to be about.
st.session_state.setdefault("pending_selection", "")
# Index of the assistant message a follow-up selection refers to.
st.session_state.setdefault("selection_target_index", None)


def _clear_selection() -> None:
    st.session_state.pending_selection = ""
    st.session_state.selection_target_index = None


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
        display_user = f"_About this selection:_\n\n> {preview}\n\n{text}"

    if text:
        st.session_state.messages.append({"role": "user", "content": display_user})
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(display_user)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
        if text
    ]

    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        with st.spinner("Thinking..."):
            try:
                payload = {
                    "session_id": "default",
                    "message": text,
                    "history": history,
                    "selected_text": selected_text or None,
                    "approved": approved,
                }
                resp = httpx.post(API_URL, json=payload, timeout=300)
                resp.raise_for_status()
                data = resp.json()
                answer = data["response"]

                # Annotate the reply with badges for path + model.
                path = (data.get("path_used") or "").lower() or "unknown"
                path_color = {
                    "general": "blue",
                    "coding": "green",
                    "complex": "violet",
                }.get(path, "gray")
                with st.container(horizontal=True):
                    st.badge(path, color=path_color)
                    if model_used := data.get("model_used"):
                        st.badge(model_used, color="gray")

                st.markdown(answer)

                if data.get("approval_required"):
                    st.session_state.pending_action = data.get("pending_action")
                    logger.info("Approval required: %s", data.get("pending_action"))
                else:
                    st.session_state.pending_action = None
            except httpx.TimeoutException:
                answer = "This request is taking too long in interactive mode. Try simplifying it or running it as a background task."
                st.error(answer, icon=":material/schedule:")
            except Exception as exc:  # noqa: BLE001
                answer = f"Error contacting backend: {exc}"
                st.error(answer, icon=":material/error:")

    if text:
        st.session_state.messages.append({"role": "assistant", "content": answer})


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

    st.subheader("Ingest documents", divider=False)
    st.caption("Drop `.txt` / `.md` files into `data/docs`, then run:")
    st.code("uv run jarvis-ingest", language="bash")

    with st.expander("Tips", icon=":material/lightbulb:"):
        st.markdown(
            "- Ask coding questions to route to the strong local coder.\n"
            "- Long or architecture-style prompts route to the cloud chain.\n"
            "- Select part of a reply, paste it below the reply, then ask about it.\n"
            "- Risky tool calls pause and ask for approval."
        )

    if st.button(
        "Clear conversation", icon=":material/delete:", use_container_width=False
    ):
        st.session_state.messages = []
        st.session_state.pending_action = None
        st.session_state.pending_selection = ""
        st.session_state.selection_target_index = None
        st.rerun()

# ---------------------------------------------------------------------------
# Conversation header
# ---------------------------------------------------------------------------

st.title("Chat with Jarvis")
st.caption(
    "General, coding, and complex tasks — routed to the right model automatically."
)

# Suggestions on an empty conversation (pills vanish once a message lands).
if not st.session_state.messages:
    selected = st.pills(
        "Try asking",
        list(SUGGESTIONS.keys()),
        label_visibility="collapsed",
    )
    if selected:
        _send_message(SUGGESTIONS[selected])
        st.rerun()

# ---------------------------------------------------------------------------
# Render history
# ---------------------------------------------------------------------------

for idx, msg in enumerate(st.session_state.messages):
    avatar = ":material/person:" if msg["role"] == "user" else ":material/smart_toy:"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])

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
    st.warning(
        f"Jarvis wants to: {st.session_state.pending_action}",
        icon=":material/warning:",
    )
    with st.container(horizontal=True):
        if st.button("Approve", type="primary", icon=":material/check:"):
            logger.info("User approved action: %s", st.session_state.pending_action)
            st.session_state.pending_action = None
            _send_message("", approved=True)
            st.rerun()
        if st.button("Deny", icon=":material/block:"):
            st.session_state.messages.append(
                {"role": "assistant", "content": "Action cancelled by user."}
            )
            st.session_state.pending_action = None
            st.rerun()

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
    _send_message(user_input, selected_text=selection)
    if selection:
        _clear_selection()
