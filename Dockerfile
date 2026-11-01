FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    WEB_UI_HOST=127.0.0.1 \
    WEB_UI_PORT=8763

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        libsndfile1 \
        curl \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# 建立非 root 執行使用者 app (UID/GID 10001)
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g app -s /bin/bash -m app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/data \
    && chown -R app:app /app

EXPOSE 8763

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8763/api/version')" || exit 1

USER app

CMD ["python", "main.py"]
