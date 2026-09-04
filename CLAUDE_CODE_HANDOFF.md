# Handoff: podcast-subs

This project was scaffolded in a Claude chat session (no git/Docker access
there), then handed here to actually build, test, and ship. Paste this
whole file as your first message to Claude Code in this directory.

## What this is

A watcher service that monitors a mounted podcast directory (meant to sit
next to an Audiobookshelf library) and auto-generates `.srt`/`.vtt`
transcripts for new/existing episodes using `stable-ts` (faster-whisper
backend). Async producer/consumer design: a bounded `asyncio.Queue` streams
files in from a startup scan + a watchdog observer, one transcription runs
at a time (single model instance, deliberate -- CPU inference doesn't
benefit from concurrent calls on the same cores).

Files already in this repo:
- `app/watcher.py` -- the async watcher/transcriber
- `Dockerfile` -- shared model-download stage, two runtime targets:
  `runtime-cpu` (python:3.11-slim) and `runtime-gpu` (nvidia/cuda base,
  needed for CTranslate2's GPU backend)
- `docker-compose.yml` -- local run/build config
- `.github/workflows/docker-publish.yml` -- matrix-builds both variants on
  a pushed tag (`*.*.*`, no `v` prefix) or manual dispatch, pushes to GHCR
  tagged `<version>-cpu` / `<version>-gpu` / `latest-cpu` / `latest-gpu`
- `README.md` -- setup/build/publish instructions

## Why you're needed for the next part

Everything above was written and YAML/syntax-checked, but **never actually
built or run** -- the chat sandbox had no Docker daemon and its network
allowlist couldn't reach Docker Hub, GHCR, or the CUDA base image. So
nothing here has been proven to actually work end to end.

## Tasks, roughly in order

1. **Fork stable-ts.** `jianfch/stable-ts` is archived/frozen upstream.
   Fork it to your own account, note the commit SHA you want to pin (the
   HEAD of `main` at fork time is fine), and optionally tag it
   (`frozen-<date>`) for a readable ref.

2. **Wire the pin into this repo.** Either:
   - Set `STABLE_TS_FORK_URL` / `STABLE_TS_REF` as repo variables (repo
     Settings -> Secrets and variables -> Actions -> Variables) for the
     GitHub Actions workflow, and/or
   - Update the same values in `docker-compose.yml`'s `build.args` for
     local builds.

3. **Build and fix the CPU image first:**
   ```
   docker build --target runtime-cpu -t podcast-subs:cpu-test .
   ```
   Expect possible friction around: the `stable-ts[fw]` extras install
   pulling compatible `faster-whisper`/`ctranslate2`/torch versions,
   Python version mismatches, or the model-download stage needing
   network access mid-build.

4. **Build and fix the GPU image:**
   ```
   docker build --target runtime-gpu -t podcast-subs:gpu-test .
   ```
   This is the least-tested part. Likely friction points: whether
   `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` actually has what
   CTranslate2 needs at the pinned version, whether `python3-pip` from
   Ubuntu 22.04's apt is new enough, and whether GPU inference actually
   needs `--gpus all` at *run* time (not just build time) to verify.

5. **Smoke-test the watcher against a real directory.** Point
   `WATCH_DIR` at a folder with a couple of real podcast audio files
   (plus, ideally, one file that's still "downloading" -- e.g. write to
   it slowly -- to confirm the stability-check/backpressure logic in
   `watcher.py` behaves as intended, not just compiles).

6. **Run the GitHub Actions workflow for real** -- either push a test tag
   (e.g. `0.0.1-test`) or use `workflow_dispatch` -- and watch both matrix
   legs (cpu, gpu) actually push to GHCR. Fix whatever the real CI
   environment surfaces that local building didn't.

7. Once confirmed working, update `docker-compose.yml` to reference the
   published `ghcr.io/...` image instead of `build:`, ready for a
   Portainer stack on the target host (an i5-12600K TrueNAS SCALE box,
   CPU-only -- so `runtime-cpu` / `DEVICE=cpu` is the one that actually
   matters for real deployment; `runtime-gpu` is there for future-proofing
   if a GPU gets added later).

Nothing above should require re-architecting what's here -- the design
(async queue, single-consumer transcription, shared model-fetch stage,
matrix-tagged CI) was intentional. The goal of this pass is verification
and fixing real build/runtime errors, not a redesign.
