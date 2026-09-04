# syntax=docker/dockerfile:1
#
# Two runtime targets sharing one model-download stage:
#   docker build --target runtime-cpu ...
#   docker build --target runtime-gpu ...
# The GPU target needs CUDA + cuDNN at runtime for CTranslate2's GPU
# backend, so it uses an nvidia/cuda base instead of python:slim.

ARG WHISPER_MODEL=small
ARG STABLE_TS_FORK_URL=https://github.com/joesolly/stable-ts.git
ARG STABLE_TS_REF=frozen-2026-09-03

# ---------------------------------------------------------------------------
# Shared stage: download the faster-whisper model weights. Identical for
# both variants -- CPU/GPU only changes how inference runs, not the weights.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS model-fetch
ARG WHISPER_MODEL
RUN pip install --no-cache-dir faster-whisper==1.* \
    && python -c "\
from faster_whisper import WhisperModel; \
WhisperModel('${WHISPER_MODEL}', download_root='/models', compute_type='int8')"

# ---------------------------------------------------------------------------
# CPU runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime-cpu
ARG STABLE_TS_FORK_URL
ARG STABLE_TS_REF

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

# PyPI's default `torch` wheel bundles the full CUDA runtime (~5GB of
# nvidia-* packages + triton), even though this image only ever runs on
# CPU. Installing the CPU-only build first stops pip from resolving that
# GPU variant as a transitive dep of stable-ts[fw]/openai-whisper.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch torchaudio

RUN pip install --no-cache-dir \
    "stable-ts[fw] @ git+${STABLE_TS_FORK_URL}@${STABLE_TS_REF}" \
    watchdog

# Pre-download silero-vad (used by transcribe(..., vad=True) in watcher.py)
# so the container never needs outbound network at runtime -- it otherwise
# lazily torch.hub.load()s this from GitHub on first transcription.
RUN python -c "\
import torch; \
torch.hub.load(repo_or_dir='snakers4/silero-vad:master', model='silero_vad', trust_repo=True)"

COPY --from=model-fetch /models /models
ENV WHISPER_DOWNLOAD_ROOT=/models

WORKDIR /app
COPY app/watcher.py /app/watcher.py

ENV WATCH_DIR=/watch \
    MODEL_SIZE=small \
    OUTPUT_FORMATS=srt \
    STABLE_CHECK_SECONDS=30 \
    QUEUE_MAXSIZE=200 \
    DEVICE=cpu \
    COMPUTE_TYPE=int8

VOLUME ["/watch"]
CMD ["python", "watcher.py"]

# ---------------------------------------------------------------------------
# GPU runtime (CUDA 12.4 + cuDNN runtime, matches CTranslate2's GPU support)
# ---------------------------------------------------------------------------
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 AS runtime-gpu
ARG STABLE_TS_FORK_URL
ARG STABLE_TS_REF

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg python3 python3-pip git \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

RUN pip3 install --no-cache-dir \
    "stable-ts[fw] @ git+${STABLE_TS_FORK_URL}@${STABLE_TS_REF}" \
    watchdog

# Pre-download silero-vad (used by transcribe(..., vad=True) in watcher.py)
# so the container never needs outbound network at runtime -- it otherwise
# lazily torch.hub.load()s this from GitHub on first transcription.
RUN python -c "\
import torch; \
torch.hub.load(repo_or_dir='snakers4/silero-vad:master', model='silero_vad', trust_repo=True)"

COPY --from=model-fetch /models /models
ENV WHISPER_DOWNLOAD_ROOT=/models

WORKDIR /app
COPY app/watcher.py /app/watcher.py

ENV WATCH_DIR=/watch \
    MODEL_SIZE=small \
    OUTPUT_FORMATS=srt \
    STABLE_CHECK_SECONDS=30 \
    QUEUE_MAXSIZE=200 \
    DEVICE=cuda \
    COMPUTE_TYPE=float16

VOLUME ["/watch"]
CMD ["python", "watcher.py"]
