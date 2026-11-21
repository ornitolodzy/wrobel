# syntax=docker/dockerfile:1.7
# ImageRecognitionServcie OCR - CPU image. Model weights are downloaded at build time,
# the running container never needs internet access.
#
#   docker build -t image-recognition-servcie .
#   docker run --rm -p 8000:8000 image-recognition-servcie

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG UV_VERSION=0.13.7

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------- base: runtime system libs
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# libgomp1: PaddlePaddle (OpenMP); libgl1 + libglib2.0-0: opencv-contrib-python (required by PaddleX)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------- builder: virtualenv from uv.lock
FROM base AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src

# Dependencies first (cached layer as long as uv.lock does not change)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------------------------------------------------------------- models: bake weights into the image
FROM builder AS models
ARG OCR_PADDLE_DET_MODEL=PP-OCRv5_mobile_det
ARG OCR_PADDLE_REC_MODEL=latin_PP-OCRv5_mobile_rec
ENV OCR_MODEL_DIR=/opt/models \
    OCR_PADDLE_DET_MODEL=${OCR_PADDLE_DET_MODEL} \
    OCR_PADDLE_REC_MODEL=${OCR_PADDLE_REC_MODEL} \
    OCR_PADDLE_ENABLE_MKLDNN=false
# Downloads the weights and runs a warm-up inference (fails the build if the model is broken).
RUN /opt/venv/bin/python -m ocr_service.download_models \
 && rm -rf /opt/models/temp /opt/models/locks

# ---------------------------------------------------------------- runtime
FROM base AS runtime
ARG OCR_PADDLE_DET_MODEL=PP-OCRv5_mobile_det
ARG OCR_PADDLE_REC_MODEL=latin_PP-OCRv5_mobile_rec

RUN groupadd --system --gid 10001 ocr \
 && useradd --system --uid 10001 --gid ocr --home-dir /home/ocr --create-home \
    --shell /usr/sbin/nologin ocr

COPY --from=builder /opt/venv /opt/venv
# PaddleX writes lock files next to the weights, so the directory belongs to the service user.
COPY --from=models --chown=ocr:ocr /opt/models /opt/models

ENV PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/ocr \
    OCR_HOST=0.0.0.0 \
    OCR_PORT=8000 \
    OCR_MODEL_DIR=/opt/models \
    OCR_OFFLINE=true \
    OCR_PADDLE_DET_MODEL=${OCR_PADDLE_DET_MODEL} \
    OCR_PADDLE_REC_MODEL=${OCR_PADDLE_REC_MODEL} \
    OCR_PADDLE_ENABLE_MKLDNN=false

USER ocr:ocr
WORKDIR /home/ocr
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('OCR_PORT', '8000'), timeout=4)"]

CMD ["python", "-m", "ocr_service"]
