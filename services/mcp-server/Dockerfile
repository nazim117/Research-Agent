FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .
# /data is where the mcp-env-data named volume mounts (see docker-compose.yml's
# MCP_ENV_FILE=/data/.env) — must exist and be owned by appuser *before* the
# volume is first attached, since Docker seeds a new named volume's ownership
# from whatever's already at the mount path in the image. Without this,
# appuser (non-root) gets a root-owned /data and PUT /config/env 500s with
# PermissionError.
RUN mkdir -p /data && chown appuser:appuser /data
USER appuser
EXPOSE 8083
ENTRYPOINT ["python", "main.py"]
