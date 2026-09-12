# Runtime image. Build the data locally first (see README "Run"), then:
#   docker build -t dipcast . && docker run -p 8000:8000 dipcast
# data/processed (river network pickle, overflows, model) is copied in; the
# rainfall cache is not needed at runtime because forecasts are fetched live.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy DIPCAST_ROOT=/app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev
COPY data/processed/river_network.pkl data/processed/overflows.parquet \
     data/processed/annual_returns.parquet data/processed/live_latest.parquet \
     data/processed/spill_model.pkl ./data/processed/
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "dipcast.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
