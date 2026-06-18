FROM python:3.11-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src

RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

CMD ["live_stream_analysis", "--help"]
