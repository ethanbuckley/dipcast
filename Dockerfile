# Runtime image. Build the data locally first (see README "Run"), then:
#   docker build -t dipcast . && docker run -p 8000:8000 -e DIPCAST_REFRESH_MINUTES=20 dipcast
# data/processed (river network, overflows, model, lakes, calibration) is copied
# in read-only; mutable state (live polls, forecast log) goes to DIPCAST_STATE.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy DIPCAST_ROOT=/app DIPCAST_STATE=/state PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock LICENSE README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev
COPY data/processed/ ./data/processed/
RUN mkdir -p /state
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "dipcast.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
