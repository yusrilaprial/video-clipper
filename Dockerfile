# syntax=docker/dockerfile:1
FROM python:3.12-slim

# WITH_WHISPER=false -> image lebih kecil (~400 MB lebih ringan), subtitle hanya dari caption YouTube
ARG WITH_WHISPER=true
# INSTALL_CJK_FONTS=true -> subtitle Mandarin/Jepang/Korea tertanam dengan benar (+ ~100 MB)
ARG INSTALL_CJK_FONTS=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CLIPPER_HOST=0.0.0.0 \
    CLIPPER_PORT=5000 \
    CLIPPER_OUTPUT_DIR=/data/output \
    CLIPPER_WHISPER_DIR=/data/models \
    HF_HOME=/data/models/hf

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-dejavu-core fonts-noto-core \
       curl ca-certificates tini \
    && if [ "$INSTALL_CJK_FONTS" = "true" ]; then apt-get install -y --no-install-recommends fonts-noto-cjk; fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-server.txt requirements-whisper.txt ./
RUN pip install -r requirements.txt -r requirements-server.txt \
    && if [ "$WITH_WHISPER" = "true" ]; then pip install -r requirements-whisper.txt; fi

COPY . .

RUN useradd --create-home --uid 10001 clipper \
    && mkdir -p /data/output /data/models \
    && chown -R clipper:clipper /data /app
USER clipper

VOLUME ["/data"]
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${CLIPPER_PORT}/healthz" || exit 1

# Penting: 1 worker. Status job disimpan di memori proses; konkurensi render diatur CLIPPER_MAX_JOBS.
ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "exec gunicorn --worker-class gthread --workers 1 --threads 16 --timeout 120 --bind ${CLIPPER_HOST}:${CLIPPER_PORT} --access-logfile - app:app"]
