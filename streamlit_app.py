"""Streamlit chat frontend, calls the FastAPI backend."""
import httpx
import streamlit as st

API_URL = "http://localhost:8000/chat"

st.set_page_config(page_title="Jarvis Assistant", page_icon="🤖")
st.title("Jarvis Assistant")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_input = st.chat_input("Ask Jarvis something...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                resp = httpx.post(
                    API_URL,
                    json={"session_id": "default", "message": user_input},
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                answer = data["response"]
                st.caption(f"Path: {data['path_used']} | Model: {data.get('model_used') or 'local'}")
                st.markdown(answer)
            except Exception as exc:  # noqa: BLE001
                answer = f"Error contacting backend: {exc}"
                st.error(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
