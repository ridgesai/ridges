FROM ghcr.io/astral-sh/uv:latest AS uv
FROM docker:27-cli AS docker-cli

FROM alpine/git:latest AS git-shallow
WORKDIR /repo
COPY .git .git
RUN git clone --depth 1 --no-checkout file:///repo /shallow

FROM python:3.12-slim-bookworm AS deps
WORKDIR /app
COPY --from=uv /uv /bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache

FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*
RUN useradd -u 1000 -m screener && \
    git config --system user.email "screener@ridges.ai" && \
    git config --system user.name "ridges-screener" && \
    git config --system safe.directory /app
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=deps /app/.venv /app/.venv
COPY api/ /app/api/
COPY evaluator/ /app/evaluator/
COPY models/ /app/models/
COPY queries/ /app/queries/
COPY utils/ /app/utils/
COPY validator/ /app/validator/
COPY pyproject.toml /app/
COPY --from=git-shallow /shallow/.git /app/.git
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
COPY validator/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && \
    mkdir -p /app/logs && chown 1000:1000 /app/logs && \
    mkdir -p /app/evaluator/datasets/swebench_verified/repos && \
    chown -R 1000:1000 /app/evaluator/datasets
USER 1000
ENTRYPOINT ["/entrypoint.sh"]
