# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

# bookworm is oldstable — use archive.debian.org
RUN sed -i 's|deb.debian.org|archive.debian.org|g' /etc/apt/sources.list

WORKDIR /app

# ------------------------------------------------------------------
# Layer 1: 系统依赖（极少变化，充分利用 Docker layer cache）
# ------------------------------------------------------------------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        sqlite3 \
        curl \
        xz-utils \
    && rm -rf /var/lib/apt/lists/* && \
    curl -fsSL https://nodejs.org/dist/v20.19.1/node-v20.19.1-linux-x64.tar.xz -o /tmp/node.tar.xz && \
    tar -xf /tmp/node.tar.xz -C /usr/local --strip-components=1 && \
    rm /tmp/node.tar.xz

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
