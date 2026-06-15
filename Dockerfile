FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY main.py ./

RUN uv sync --frozen --no-dev

EXPOSE 8000
VOLUME ["/app/cred"]

CMD ["uv", "run", "--frozen", "hm-api", "serve", "--host", "0.0.0.0", "--port", "8000"]
