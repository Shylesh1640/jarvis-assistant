"""Streamlit chat frontend, calls the FastAPI backend."""
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
    st.header("Model Configuration")
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

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_action" not in st.session_state:
    st.session_state.pending_action = None


def _send_message(text: str, *, approved: bool = False) -> None:
    if text:
        st.session_state.messages.append({"role": "user", "content": text})
        with st.chat_message("user"):
            st.markdown(text)

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


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- Approval UI ---
if st.session_state.pending_action:
    st.warning(f"Jarvis wants to: {st.session_state.pending_action}")
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("✅ Approve"):
            logger.info("User approved action: %s", st.session_state.pending_action)
            st.session_state.pending_action = None
            _send_message("", approved=True)
            st.rerun()
    with col2:
        if st.button("❌ Deny"):
            st.session_state.messages.append({
                "role": "assistant",
                "content": "Action cancelled by user.",
            })
            st.session_state.pending_action = None
            st.rerun()

user_input = st.chat_input("Ask Jarvis something...")

if user_input:
    _send_message(user_input)
