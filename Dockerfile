FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --no-dev --no-install-project --frozen

COPY src/ src/

RUN uv sync --no-dev --frozen

ENTRYPOINT ["uv", "run", "nordnet-mcp"]
