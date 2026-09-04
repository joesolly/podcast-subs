#!/usr/bin/env python3
"""
Watches a mounted directory tree for podcast audio files and generates
sidecar .srt/.vtt transcripts using stable-ts + faster-whisper.

Async producer/consumer design:
  - A bounded asyncio.Queue decouples scanning from transcribing. The
    startup scan streams paths into the queue as it walks the tree
    (os.walk, not a materialized list), so memory use is capped at
    QUEUE_MAXSIZE regardless of library size, and transcription starts
    on the first file immediately instead of waiting for the full scan.
  - watchdog runs its observer thread as usual (it isn't async-native)
    and hands new-file events into the same queue via
    asyncio.run_coroutine_threadsafe.
  - Transcription is CPU-bound, so it runs in a worker thread via
    asyncio.to_thread -- this keeps the event loop free to keep scanning
    and queueing while a file is being transcribed. There's a single
    transcription slot (one model instance, sequential) since
    faster-whisper on CPU gains nothing from concurrent calls; the
    queue is what prevents the scan from getting ahead of it.

Env vars: see previous revision -- unchanged, plus
  QUEUE_MAXSIZE   Max pending files held in memory at once (default: 200)
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("podcast-subs")

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".flac", ".wav", ".ogg", ".opus"}

WATCH_DIR = Path(os.environ.get("WATCH_DIR", "/watch"))
MODEL_SIZE = os.environ.get("MODEL_SIZE", "small")
OUTPUT_FORMATS = [f.strip() for f in os.environ.get("OUTPUT_FORMATS", "srt").split(",") if f.strip()]
STABLE_CHECK_SECONDS = int(os.environ.get("STABLE_CHECK_SECONDS", "30"))
DOWNLOAD_ROOT = os.environ.get("WHISPER_DOWNLOAD_ROOT", "/models")
LANGUAGE = os.environ.get("LANGUAGE") or None
DEVICE = os.environ.get("DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("COMPUTE_TYPE", "int8")
QUEUE_MAXSIZE = int(os.environ.get("QUEUE_MAXSIZE", "200"))


def outputs_exist(audio_path: Path) -> bool:
    return all(audio_path.with_suffix(f".{fmt}").exists() for fmt in OUTPUT_FORMATS)


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


class Transcriber:
    """Loads the model once (lazily, on first real work) and reuses it."""

    def __init__(self):
        self._model = None

    def _ensure_loaded(self):
        if self._model is not None:
            return
        import stable_whisper

        log.info(
            "Loading faster-whisper model=%s device=%s compute_type=%s (download_root=%s)",
            MODEL_SIZE, DEVICE, COMPUTE_TYPE, DOWNLOAD_ROOT,
        )
        self._model = stable_whisper.load_faster_whisper(
            MODEL_SIZE,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root=DOWNLOAD_ROOT,
        )
        log.info("Model loaded.")

    def transcribe_sync(self, audio_path: Path):
        """Blocking call -- always run this via asyncio.to_thread."""
        self._ensure_loaded()
        log.info("Transcribing: %s", audio_path)
        try:
            result = self._model.transcribe(str(audio_path), language=LANGUAGE, vad=True)
        except Exception:
            log.exception("Transcription failed for %s", audio_path)
            return

        for fmt in OUTPUT_FORMATS:
            out_path = audio_path.with_suffix(f".{fmt}")
            try:
                result.to_srt_vtt(str(out_path), vtt=(fmt == "vtt"))
                log.info("Wrote: %s", out_path)
            except Exception:
                log.exception("Failed writing %s for %s", fmt, audio_path)


class AsyncWatcher:
    def __init__(self):
        self.queue: "asyncio.Queue[Path]" = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self.loop = asyncio.get_event_loop()
        self.transcriber = Transcriber()
        # Paths currently queued or being stability-checked, so the same
        # file (re-triggered by both the scan and a watchdog event, or by
        # repeated modify events) isn't queued twice in parallel.
        self._in_flight: set = set()

    def try_enqueue_threadsafe(self, path: Path):
        """Called from the watchdog thread; hops onto the event loop."""
        asyncio.run_coroutine_threadsafe(self._try_enqueue(path), self.loop)

    async def _try_enqueue(self, path: Path):
        if not is_audio(path) or outputs_exist(path) or path in self._in_flight:
            return
        self._in_flight.add(path)
        await self.queue.put(path)  # blocks here if queue is full -> backpressure

    async def _requeue_later(self, path: Path, delay: float):
        await asyncio.sleep(delay)
        self._in_flight.discard(path)
        await self._try_enqueue(path)

    async def scan_existing(self):
        """
        Streams the existing tree into the queue via os.walk, rather than
        materializing a full list first. Naturally pauses (via queue.put)
        once QUEUE_MAXSIZE files are pending, so a huge library never
        gets fully buffered in memory.
        """
        if not WATCH_DIR.exists():
            log.warning("Watch directory %s does not exist yet.", WATCH_DIR)
            return
        log.info("Starting backfill scan of %s", WATCH_DIR)
        count = 0
        for root, _dirs, files in os.walk(WATCH_DIR):
            for name in files:
                path = Path(root) / name
                if is_audio(path) and not outputs_exist(path):
                    await self._try_enqueue(path)
                    count += 1
                    # yield control periodically so watchdog events and the
                    # consumer can interleave with a long scan
                    if count % 25 == 0:
                        await asyncio.sleep(0)
        log.info("Backfill scan complete: %d file(s) queued.", count)

    async def consume(self):
        while True:
            path = await self.queue.get()
            try:
                if not path.exists():
                    continue
                if outputs_exist(path):
                    continue

                mtime_before = path.stat().st_mtime
                await asyncio.sleep(STABLE_CHECK_SECONDS)
                if not path.exists():
                    continue
                mtime_after = path.stat().st_mtime

                if mtime_before != mtime_after:
                    # still being written (download/copy in progress);
                    # check again later without blocking the consumer loop
                    asyncio.create_task(self._requeue_later(path, STABLE_CHECK_SECONDS))
                    continue

                await asyncio.to_thread(self.transcriber.transcribe_sync, path)
            finally:
                self._in_flight.discard(path)
                self.queue.task_done()


class AudioEventHandler(FileSystemEventHandler):
    def __init__(self, watcher: AsyncWatcher):
        self.watcher = watcher

    def on_created(self, event):
        if not event.is_directory:
            self.watcher.try_enqueue_threadsafe(Path(event.src_path))

    def on_modified(self, event):
        if not event.is_directory:
            self.watcher.try_enqueue_threadsafe(Path(event.src_path))


async def main():
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    log.info(
        "Watching: %s (formats=%s, model=%s, queue_maxsize=%d)",
        WATCH_DIR, OUTPUT_FORMATS, MODEL_SIZE, QUEUE_MAXSIZE,
    )

    watcher = AsyncWatcher()

    observer = Observer()
    observer.schedule(AudioEventHandler(watcher), str(WATCH_DIR), recursive=True)
    observer.start()

    consumer_task = asyncio.create_task(watcher.consume())
    scan_task = asyncio.create_task(watcher.scan_existing())

    try:
        await asyncio.gather(scan_task, consumer_task)
    except asyncio.CancelledError:
        pass
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
