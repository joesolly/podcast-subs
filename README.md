# podcast-subs

Watches a mounted podcast directory and generates sidecar transcripts (SRT
by default, several other formats available -- see
[Output formats](#output-formats)) for new episodes using
[stable-ts](https://github.com/jianfch/stable-ts) (faster-whisper backend).
Built to sit next to an Audiobookshelf library.

## Quick start

Pull the published image and point it at your library — no build required.

```yaml
# docker-compose.yml
services:
  podcast-subs:
    image: ghcr.io/joesolly/podcast-subs:latest-cpu   # or :latest-gpu on a GPU host
    container_name: podcast-subs
    restart: unless-stopped
    environment:
      MODEL_SIZE: small
      OUTPUT_FORMATS: srt
      STABLE_CHECK_SECONDS: "30"
      DEVICE: cpu            # cuda on the gpu image
      COMPUTE_TYPE: int8     # float16 on the gpu image
      # LANGUAGE: en          # uncomment to force a language instead of auto-detect
    volumes:
      - /path/to/podcasts:/watch
      # multiple libraries work too -- mount as many subpaths as you want:
      # - /path/to/showA:/watch/showA
      # - /path/to/showB:/watch/showB
```

```bash
docker compose up -d
```

Pin to a specific release instead of `latest-cpu`/`latest-gpu` with e.g.
`ghcr.io/joesolly/podcast-subs:1.0.0-cpu` -- see [Releases](#publishing-to-ghcr)
for how tags map to versions.

This repo's own [docker-compose.yml](docker-compose.yml) is this same example,
ready to copy for a Portainer stack.

## Run

Point the volume mount(s) at whatever directory (or directories --
see [Quick start](#quick-start)) Audiobookshelf scans for your podcast
library. New episodes get a same-named `.srt` written alongside the audio
file once the watcher detects the file has stopped growing
(`STABLE_CHECK_SECONDS`, default 30s of unchanged mtime).

On startup it also scans the whole tree once for any existing audio files
missing a transcript, so it backfills your library, not just new episodes.
The scan streams into a bounded `asyncio.Queue` (`QUEUE_MAXSIZE`, default
200) rather than building a full in-memory list first, so a very large
library won't spike memory, and transcription of the first file starts
immediately rather than waiting for the whole scan to finish.

Both the faster-whisper model weights and the silero-vad model (used for
voice-activity detection) are baked into the image at build time, so the
container needs no outbound network at runtime -- only the initial image
pull.

## Output formats

Set `OUTPUT_FORMATS` to a comma-separated list to control what gets written
next to each audio file. Supported values, with approximate sizes for a
60-minute episode (measured, not estimated -- scales roughly linearly with
episode length):

| Format | What it is                                        | ~size/hour |
|--------|----------------------------------------------------|-----------|
| `srt`  | Subtitles, word-level timing (default)              | ~1.3 MB   |
| `vtt`  | WebVTT subtitles -- what browsers' `<track>` wants   | ~240 KB   |
| `txt`  | Plain text, no timestamps -- best for full-text search (`grep`) | ~66 KB |
| `json` | Full result: every word's timestamp + confidence, as a plain dict you can extend with your own top-level keys (tags, notes, etc.) after the fact | ~3.1 MB |
| `tsv`  | Tab-separated timestamp/text rows                    | ~87 KB    |
| `ass`  | Styled/karaoke-style subtitles (fonts, colors, highlighting) | ~210 KB |

For example, to get playback subtitles plus something you can grep and
later annotate:

```yaml
OUTPUT_FORMATS: srt,txt,json
```

A file is only skipped as "already done" once *every* format in
`OUTPUT_FORMATS` exists next to it -- so decide on your format list before
transcribing a large library. Adding a format later means every existing
episode gets re-transcribed once to backfill it. The watcher also never
merges into an existing sidecar file, only overwrites, so hand-added
metadata in a `.json` file is safe as long as you don't delete it and let
the file get reprocessed.

## Building locally

Only needed if you want to change `WHISPER_MODEL` or bump the stable-ts pin
below -- most deployments should just use the published image above.

```bash
docker compose build   # with build: uncommented in docker-compose.yml
```

or directly:

```bash
docker build --target runtime-cpu -t podcast-subs:cpu .
docker build --target runtime-gpu -t podcast-subs:gpu .
```

Model download happens once, during the build (baked into the image at
`/models`). Rebuilding without changing `WHISPER_MODEL` reuses Docker's
layer cache, so it won't re-download.

To change model size, edit `WHISPER_MODEL` under `build.args` *and*
`MODEL_SIZE` under `environment` in `docker-compose.yml` (both must match --
the build arg controls what gets baked in, the env var controls what the
watcher actually loads at runtime).

### The stable-ts pin

`jianfch/stable-ts` is archived/frozen upstream, so this project builds
against a fork instead:

- Fork: https://github.com/joesolly/stable-ts
- Pinned ref: `frozen-2026-09-03` (a tag on the fork, not `main` -- so a
  future push to the fork can't silently change the build)

To bump the pin, push a new tag to the fork and update `STABLE_TS_REF` in
`Dockerfile`, `docker-compose.yml`, and the `STABLE_TS_REF` repo variable
(*Settings -> Secrets and variables -> Actions -> Variables*) used by CI.

## Publishing to GHCR

### Via GitHub Actions (`.github/workflows/docker-publish.yml`)

Builds both `runtime-cpu` and `runtime-gpu` targets in a matrix and pushes
each to GHCR tagged with the same version, suffixed by variant:

```
ghcr.io/joesolly/podcast-subs:1.2.0-cpu
ghcr.io/joesolly/podcast-subs:1.2.0-gpu
ghcr.io/joesolly/podcast-subs:latest-cpu
ghcr.io/joesolly/podcast-subs:latest-gpu
```

Triggers:
- Push a tag matching `*.*.*` (e.g. `git tag 1.2.0 && git push --tags`) --
  the tag name becomes the version as-is.
- Or run manually from the Actions tab (`workflow_dispatch`), optionally
  overriding `version` and `whisper_model`. Without an input, version falls
  back to `sha-<short-sha>`.

No secrets needed beyond the `STABLE_TS_FORK_URL`/`STABLE_TS_REF` repo
variables above -- it authenticates to GHCR with the automatically provided
`GITHUB_TOKEN`.

### Manually

```bash
docker build --target runtime-cpu -t ghcr.io/joesolly/podcast-subs:latest-cpu .
docker build --target runtime-gpu -t ghcr.io/joesolly/podcast-subs:latest-gpu .
docker push ghcr.io/joesolly/podcast-subs:latest-cpu
docker push ghcr.io/joesolly/podcast-subs:latest-gpu
```

## Notes

- CPU-only by default (`DEVICE=cpu`, `COMPUTE_TYPE=int8`) — no GPU assumed on lovelace.
  The `runtime-gpu` image exists for future-proofing if a GPU gets added later.
- `small` is a reasonable speed/accuracy default for CPU; `medium` is
  noticeably slower but more accurate. `large-v3` is likely impractical
  without a GPU for anything but short episodes.
- Audiobookshelf does not currently render these sidecar files in its
  player — this just gets `.srt`/`.vtt` sitting next to the audio for
  whenever that lands, or for use with any player that reads sidecar subs.
