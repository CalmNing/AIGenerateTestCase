# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# ------------------------------------------------------------------
# Layer 1: 系统依赖（极少变化，充分利用 Docker layer cache）
# ------------------------------------------------------------------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        sqlite3 \
        nodejs \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------
# Layer 2: 安装 uv（极少变化，独立层，避免每次重装）
# ------------------------------------------------------------------
RUN pip install uv && rm -rf /root/.cache/pip
ENV UV_SYSTEM_PYTHON=1

# ------------------------------------------------------------------
# Layer 3: Python 依赖（requirements.txt 不变时命中 layer cache）
#          BuildKit cache mount 让 uv 在 requirements.txt 变化时
#          也能复用已缓存的 wheel，无需重新下载
# ------------------------------------------------------------------
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r requirements.txt

# ------------------------------------------------------------------
# Layer 4: 应用代码（最频繁变化，放最后）
# ------------------------------------------------------------------
COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
