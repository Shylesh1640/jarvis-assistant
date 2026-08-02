"""Streamlit chat frontend, calls the FastAPI backend.

Supports optional "select text → ask follow-up" workflow: after the
assistant replies, the user can paste a snippet they want to ask about
into the box below the latest assistant message, then type their
question in the normal chat input. The snippet is forwarded to the
backend as `selected_text` so the model knows exactly what text the
question is referencing.
"""
import logging

import httpx
import streamlit as st

BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/chat"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("streamlit")

st.set_page_config(page_title="Jarvis Assistant", page_icon="🤖")
st.title("Jarvis Assistant")

# --- Sidebar: model configuration ---
with st.sidebar:
    st.header("Model configuration")
    try:
        models_resp = httpx.get(f"{BASE_URL}/models", timeout=5)
        models_resp.raise_for_status()
        cfg = models_resp.json()
        st.subheader("Local (Ollama)")
        for key in ("general", "coding", "strong_local"):
            m = cfg.get(key, {})
            st.caption(f"**{key}**")
            st.code(f"{m.get('model', '?')}")
        st.subheader("Complex (OpenRouter)")
        if cfg.get("complex", {}).get("configured"):
            for m in cfg["complex"]["models"]:
                st.code(m)
        else:
            st.caption("Not configured (no API key)")
    except Exception:
        st.caption("Could not load model info")

# --- Session state init ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_action" not in st.session_state:
    st.session_state.pending_action = None
# Snippet the user wants their next chat input to be "about".
if "pending_selection" not in st.session_state:
    st.session_state.pending_selection = ""
# ID of the assistant message the user is currently composing a follow-up for.
if "selection_target_index" not in st.session_state:
    st.session_state.selection_target_index = None


def _clear_selection() -> None:
    st.session_state.pending_selection = ""
    st.session_state.selection_target_index = None


def _send_message(
    text: str,
    *,
    approved: bool = False,
    selected_text: str = "",
) -> None:
    """Send a message to the backend and append both turns to history.

    `selected_text`, when non-empty, is forwarded to the backend so the
    model can frame the question as being about that snippet.
    """
    display_user = text
    if selected_text:
        # Show a short preview so the user can see what got attached.
        preview = selected_text if len(selected_text) <= 120 else selected_text[:117] + "..."
        display_user = f"_About this selection:_\n\n> {preview}\n\n{text}"

    if text:
        st.session_state.messages.append({"role": "user", "content": display_user})
        with st.chat_message("user"):
            st.markdown(display_user)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1] if text
    ]

    with st.chat_message("assistant"):
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
                st.caption(
                    f"Path: {data['path_used']} | Model: {data.get('model_used') or 'local'}"
                )
                st.markdown(answer)

                if data.get("approval_required"):
                    st.session_state.pending_action = data.get("pending_action")
                    logger.info("Approval required: %s", data.get("pending_action"))
                else:
                    st.session_state.pending_action = None
            except httpx.TimeoutException:
                answer = "This request is taking too long in interactive mode. Try simplifying it or run it as a background task."
                st.error(answer)
            except Exception as exc:  # noqa: BLE001
                answer = f"Error contacting backend: {exc}"
                st.error(answer)

    if text:
        st.session_state.messages.append({"role": "assistant", "content": answer})


# --- Render history ---
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- "Ask about a selection" panel under the latest assistant message ---
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
            col_clear, _ = st.columns([1, 5])
            if col_clear.button("Clear selection", help="Drop the pending selection"):
                _clear_selection()
                st.rerun()
        else:
            st.caption(
                "Want to ask about part of the response above? "
                "Select the text in the message, copy it, paste below, "
                "then click **Use this selection** and type your question."
            )
            new_sel = st.text_area(
                "Paste selected text here (optional)",
                key=f"selection_input_{latest_assistant_idx}",
                height=80,
                label_visibility="collapsed",
                placeholder="Paste the snippet you want to ask about...",
            )
            if st.button("Use this selection"):
                snippet = (new_sel or "").strip()
                if snippet:
                    st.session_state.pending_selection = snippet
                    st.session_state.selection_target_index = latest_assistant_idx
                    st.rerun()

# --- Approval UI ---
if st.session_state.pending_action:
    st.warning(f"Jarvis wants to: {st.session_state.pending_action}")
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("Approve"):
            logger.info("User approved action: %s", st.session_state.pending_action)
            st.session_state.pending_action = None
            _send_message("", approved=True)
            st.rerun()
    with col2:
        if st.button("Deny"):
            st.session_state.messages.append({
                "role": "assistant",
                "content": "Action cancelled by user.",
            })
            st.session_state.pending_action = None
            st.rerun()

# --- Main chat input ---
pending_hint = (
    "Ask Jarvis about your selection..."
    if st.session_state.pending_selection
    else "Ask Jarvis something..."
)
user_input = st.chat_input(pending_hint)

if user_input:
    selection = st.session_state.pending_selection
    _send_message(user_input, selected_text=selection)
    if selection:
        _clear_selection()
