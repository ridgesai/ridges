FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .
RUN uv sync --frozen --no-dev

CMD ["uv", "run", "-m", "api.src.main"]
