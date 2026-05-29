Load testing
===

This directory contains a load-testing harness for investigating performance
and memory problems in the redaction pipeline -- in particular the reported
**out-of-memory (OOM)** failures when redacting **large documents submitted as
`BASE64`** (inline) attachments.

Unlike the unit/integration tests, this harness runs a *real*, deployment-shaped
stack (real Redis broker + result store, a real Celery worker, the real API
server) so the memory behavior matches production.

## Why BASE64 is the focus

With the `LINK` attachment type only a URL travels through the system and the
worker streams the download. With `BASE64`, the entire document is carried
**inline as a Celery task argument**: it is buffered by the API, serialized into
a Celery message, stored in the Redis broker, then re-loaded and decoded by the
worker -- several full-size copies of an already-inflated (~1.33x) payload, with
up to `concurrency` of them in flight at once. That amplification is the prime
suspect for the OOM reports, and this harness exercises exactly that path.

## What's here

| File | Purpose |
| --- | --- |
| `docker-compose.load.yml` | Real `redis` + `api` + `worker` (+ one-shot `init`) with **configurable, hard memory limits** so OOM is reproducible on a laptop. |
| `config.load.toml` | Offline app config (tesseract OCR + `redact:noop`, no auth, no API keys). |
| `run_load.py` | Driver: sends concurrent BASE64 redaction requests, receives callbacks, samples container memory, detects OOM kills, prints a report. |

## Sample documents

The harness needs large sample PDFs, which we **cannot** check into the repo.
Put them in a directory on your machine and pass that directory as the first
(positional) argument to the driver. Every matching file in that directory
(default: `*.pdf`) is sent as a `BASE64` redaction request.

## Prerequisites

- Docker (Docker Desktop on macOS; `host.docker.internal` is used so the worker
  can call back to the driver running on the host).
- The project's Python env (`uv`).

## Usage

1. Bring up the stack (first run also builds the image):

```bash
cd tests/load
docker compose -f docker-compose.load.yml up --build -d
```

2. Run the driver from the **repo root**, passing your docs directory:

```bash
uv run python tests/load/run_load.py /path/to/big/pdfs \
  --concurrency 8 --num-requests 16
```

3. Inspect logs if something crashed, then tear down:

```bash
docker compose -f tests/load/docker-compose.load.yml logs worker
docker compose -f tests/load/docker-compose.load.yml down -v
```

## Tuning memory limits (reproducing OOM)

The container memory limits are intentionally small and configurable so you can
dial them down until OOM reproduces. Swap is pinned to the memory limit so the
kernel OOM-killer fires (container exits with code **137** / `State.OOMKilled`)
instead of silently swapping. Set these env vars before `docker compose up`:

| Env var | Default | Meaning |
| --- | --- | --- |
| `BC_API_MEM` | `768m` | API container memory limit |
| `BC_WORKER_MEM` | `1g` | Worker container memory limit |
| `BC_REDIS_MEM` | `1g` | Redis container memory limit |
| `BC_WORKER_CONCURRENCY` | `4` | Celery worker concurrency (parallel docs in memory) |
| `BC_API_WORKERS` | `1` | Uvicorn workers in the API |

Example -- squeeze the worker to make OOM likely:

```bash
BC_WORKER_MEM=512m BC_WORKER_CONCURRENCY=8 \
  docker compose -f docker-compose.load.yml up --build -d
```

## Driver options

The docs directory is a required positional argument (`run_load.py <docs_dir>`).
The remaining options also read from an env var:

| Option | Env var | Default |
| --- | --- | --- |
| `--api-url` | `BC_API_URL` | `http://localhost:8000` |
| `--concurrency` | `BC_LOAD_CONCURRENCY` | `4` |
| `--num-requests` | `BC_LOAD_NUM_REQUESTS` | `0` (one per doc) |
| `--output-format` | `BC_LOAD_OUTPUT_FORMAT` | `PDF` (most memory-intensive) |
| `--completion-timeout` | `BC_LOAD_COMPLETION_TIMEOUT` | `600` (seconds) |
| `--extensions` | `BC_LOAD_EXTENSIONS` | `.pdf` |
| `--callback-port` | `BC_LOAD_CALLBACK_PORT` | `9999` |
| `--callback-url-host` | `BC_LOAD_CALLBACK_URL_HOST` | `host.docker.internal` |

## Reading the report

At the end the driver prints a summary: how many requests were accepted, how
many completion callbacks came back, peak memory per container, and any OOM
kills / non-zero exit codes. The final `RESULT:` line calls out the likely
outcome:

- **OOM detected** -- a container was OOM-killed (exit 137). Reproduced.
- **Some requests never completed** -- a worker likely crashed/restarted
  mid-chain; check `docker compose logs worker`.
- **Completed with redaction ERRORs** -- finished but returned error callbacks.
- **All requests completed cleanly** -- try larger docs / higher concurrency /
  lower memory limits.

The driver exits non-zero unless every accepted request completed cleanly, so it
can be scripted.

## Notes & caveats

- The API and worker containers use `restart: "no"` so a crash stays visible for
  inspection rather than being auto-healed.
- Ports `8000` (API), `8001` (worker liveness), and `6380` (Redis) are published
  on the host; change them in the compose file if they collide.
- The `redact:noop` engine means **no LLM** runs, so no OpenAI/Azure credentials
  are required -- but tesseract OCR and PDF rendering still run, which is where
  most of the worker's memory pressure comes from.
