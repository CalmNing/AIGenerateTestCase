FROM python:3.11
WORKDIR /app
# 避免构建过程中出现交互性弹窗（时区配置等），优化构建体验
ENV DEBIAN_FRONTEND=noninteractive

# 配置正确的 Debian 13 (trixie) 仓库源
RUN echo 'Types: deb\nURIs: https://deb.debian.org/debian\nSuites: trixie trixie-updates\nComponents: main contrib non-free non-free-firmware\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' > /etc/apt/sources.list.d/debian.sources && \
    echo 'Types: deb\nURIs: https://deb.debian.org/debian-security\nSuites: trixie-security\nComponents: main contrib non-free non-free-firmware\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' > /etc/apt/sources.list.d/debian-security.sources && \
    apt-get update && \
    # 安装核心包，--no-install-recommends 减小镜像体积（仅安装必需依赖）
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        # 可选：安装简体中文语言包（如需识别中文，保留此行；仅需英文可删除）
        tesseract-ocr-chi-sim \
        # 可选：如果你的 Python 代码需要调用 PIL/Pillow 处理图片，可添加相关依赖（按需选择）
        libjpeg-dev \
        zlib1g-dev \
    && \
    # 关键：清理 apt 缓存，减小最终镜像体积（Docker 最佳实践）
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
# RUN #python -m pip install paddlepaddle==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]