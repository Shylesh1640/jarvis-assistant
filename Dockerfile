# Backend runtime: FastAPI + LangGraph + Ollama.
FROM python:3.14-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY streamlit_app.py .

RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "jarvis.api.main:app", "--host", "0.0.0.0", "--port", "8000"]