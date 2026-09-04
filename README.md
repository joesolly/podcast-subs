# podcast-subs

Watches a mounted podcast directory and generates sidecar `.srt`/`.vtt`
transcripts for new episodes using [stable-ts](https://github.com/jianfch/stable-ts)
(faster-whisper backend). Built to sit next to an Audiobookshelf library.

## One-time setup

1. **Fork stable-ts** on GitHub (`jianfch/stable-ts` -> `joesolly/stable-ts`).
   The upstream repo is archived/frozen, so this fork is your durable copy.
2. Clone your fork and note the commit SHA you want to pin:
   ```bash
   git clone https://github.com/joesolly/stable-ts.git
   cd stable-ts
   git rev-parse HEAD
   ```
3. In `Dockerfile` (build args) or `docker-compose.yml`, set:
   - `STABLE_TS_FORK_URL` to your fork's URL
   - `STABLE_TS_REF` to that commit SHA (or a tag you create, e.g. `frozen-2026-05-30`)

   Pinning to a SHA/tag instead of `main` means a future push to your own
   fork can't silently change the build either.

## Build

```bash
docker compose build
```

Model download happens once, during the build (baked into the image at
`/models`). Rebuilding without changing `WHISPER_MODEL` reuses Docker's
layer cache, so it won't re-download.

To change model size, edit `WHISPER_MODEL` under `build.args` *and*
`MODEL_SIZE` under `environment` in `docker-compose.yml` (both must match —
the build arg controls what gets baked in, the env var controls what the
watcher actually loads at runtime).

## Run

```bash
docker compose up -d
```

Point the `/path/to/podcasts` volume mount at whatever directory
Audiobookshelf scans for your podcast library. New episodes get a same-named
`.srt` written alongside the audio file once the watcher detects the file
has stopped growing (`STABLE_CHECK_SECONDS`, default 30s of unchanged mtime).

On startup it also scans the whole tree once for any existing audio files
missing a transcript, so it backfills your library, not just new episodes.
The scan streams into a bounded `asyncio.Queue` (`QUEUE_MAXSIZE`, default
200) rather than building a full in-memory list first, so a very large
library won't spike memory, and transcription of the first file starts
immediately rather than waiting for the whole scan to finish.

## Publishing to GHCR

### Manually

```bash
docker build --target runtime-cpu -t ghcr.io/joesolly/podcast-subs:latest-cpu .
docker build --target runtime-gpu -t ghcr.io/joesolly/podcast-subs:latest-gpu .
docker push ghcr.io/joesolly/podcast-subs:latest-cpu
docker push ghcr.io/joesolly/podcast-subs:latest-gpu
```

Then swap `build:` for `image: ghcr.io/joesolly/podcast-subs:latest-cpu` (or
`-gpu`) in the compose file for Portainer stacks.

### Via GitHub Actions (`.github/workflows/docker-publish.yml`)

Builds both `runtime-cpu` and `runtime-gpu` targets in a matrix and pushes
each to GHCR tagged with the same version, suffixed by variant:

```
ghcr.io/<owner>/podcast-subs:1.2.0-cpu
ghcr.io/<owner>/podcast-subs:1.2.0-gpu
ghcr.io/<owner>/podcast-subs:latest-cpu
ghcr.io/<owner>/podcast-subs:latest-gpu
```

Triggers:
- Push a tag matching `*.*.*` (e.g. `git tag 1.2.0 && git push --tags`) --
  the tag name becomes the version as-is.
- Or run manually from the Actions tab (`workflow_dispatch`), optionally
  overriding `version` and `whisper_model`. Without an input, version falls
  back to `sha-<short-sha>`.

**One-time repo setup** -- add these under
*Settings -> Secrets and variables -> Actions -> Variables* so the workflow
knows which fork/ref to build from (keeps the pin out of the workflow file
itself, so bumping it later doesn't need a code change):
- `STABLE_TS_FORK_URL` -- e.g. `https://github.com/joesolly/stable-ts.git`
- `STABLE_TS_REF` -- the pinned commit SHA or tag from the fork setup above

No other secrets needed -- it authenticates to GHCR with the automatically
provided `GITHUB_TOKEN`.

Note: this workflow was written in chat without a live GitHub Actions run
or Docker daemon to test against (network here can't reach Docker Hub /
GHCR / the CUDA base image). The YAML is syntax-validated, but the first
real run -- especially the `runtime-gpu` target pulling the CUDA base image
-- should be watched, ideally from Claude Code where it can be iterated on
against actual build/push errors.

## Notes

- CPU-only by default (`DEVICE=cpu`, `COMPUTE_TYPE=int8`) — no GPU assumed on lovelace.
- `small` is a reasonable speed/accuracy default for CPU; `medium` is
  noticeably slower but more accurate. `large-v3` is likely impractical
  without a GPU for anything but short episodes.
- Audiobookshelf does not currently render these sidecar files in its
  player — this just gets `.srt`/`.vtt` sitting next to the audio for
  whenever that lands, or for use with any player that reads sidecar subs.

## Continuing this with Claude Code

This was scaffolded in chat, without git/Docker access. To actually fork,
commit, build-test, and push to GHCR, open this directory in Claude Code
(`claude` in your terminal, pointed at this folder) — it can run `git`,
`docker build`, and `gh` directly and iterate against real build errors
instead of guessing.
