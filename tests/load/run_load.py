#!/usr/bin/env python
"""Load driver for reproducing OOM / memory issues on the BASE64 redaction path.

This script drives the stack defined in ``docker-compose.load.yml`` (Redis +
API + Celery worker). It reads large sample documents from a local directory
(passed as the ``docs_dir`` positional argument; these are deliberately
*not* checked into the repo), base64-encodes them, and fires concurrent
``POST /api/v1/redact`` requests using the ``BASE64`` attachment type -- the
path users have reported running out of memory on.

While the requests run, the driver:
  * receives the worker's completion callbacks on a small local HTTP server
    (reachable from the worker container via ``host.docker.internal``),
  * samples per-container memory with ``docker stats`` and tracks the peak,
  * detects container OOM-kills (``State.OOMKilled`` / exit code 137),

and prints a summary report at the end.

Example:

    cd tests/load
    docker compose -f docker-compose.load.yml up --build -d
    uv run python tests/load/run_load.py /path/to/big/pdfs \\
        --concurrency 8 --num-requests 16
    docker compose -f docker-compose.load.yml down -v

Run ``uv run python tests/load/run_load.py --help`` for all options.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

# Container names are pinned in docker-compose.load.yml so we can target them
# directly with `docker stats` / `docker inspect`.
CONTAINERS = ("bc-load-api", "bc-load-worker", "bc-load-redis")

_MEM_UNITS = {
    "B": 1,
    "KB": 10**3,
    "MB": 10**6,
    "GB": 10**9,
    "TB": 10**12,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
}
_MEM_RE = re.compile(r"^([\d.]+)\s*([KMGT]?I?B)$", re.IGNORECASE)


def human_to_bytes(s: str) -> float:
    """Parse a docker-style memory string (e.g. '123.4MiB') into bytes."""
    s = s.strip()
    m = _MEM_RE.match(s)
    if not m:
        return 0.0
    value, unit = m.groups()
    return float(value) * _MEM_UNITS.get(unit.upper(), 1)


def bytes_to_human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1000
    return f"{n:.1f}TB"


# --------------------------------------------------------------------------- #
# Callback receiver
# --------------------------------------------------------------------------- #


@dataclass
class CallbackRecord:
    document_id: str
    status: str
    body_bytes: int
    received_at: float
    error: str | None = None


class CallbackCollector:
    """A tiny threaded HTTP server that records redaction callbacks.

    The worker posts a ``RedactionResultSuccess`` / ``RedactionResultError``
    payload here when a chain finishes. We record the input document id, the
    status, and the size of the payload (the success payload re-embeds the full
    base64 document, so its size is itself interesting for the memory story).
    """

    def __init__(self, host: str, port: int):
        self._records: dict[str, CallbackRecord] = {}
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

        collector = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence per-request logging
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw)
                    doc_id = payload.get("inputDocumentId", "<unknown>")
                    status = payload.get("status", "<unknown>")
                    err = payload.get("error")
                except Exception:
                    doc_id, status, err = "<unparseable>", "<unparseable>", None
                collector._record(
                    CallbackRecord(
                        document_id=doc_id,
                        status=status,
                        body_bytes=len(raw),
                        received_at=time.monotonic(),
                        error=err,
                    )
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _record(self, rec: CallbackRecord) -> None:
        with self._cv:
            self._records[rec.document_id] = rec
            self._cv.notify_all()

    def wait_for(self, document_ids: set[str], timeout: float) -> None:
        """Block until callbacks for all ``document_ids`` arrive or timeout."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while not document_ids.issubset(self._records.keys()):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._cv.wait(timeout=remaining)

    def snapshot(self) -> dict[str, CallbackRecord]:
        with self._lock:
            return dict(self._records)


# --------------------------------------------------------------------------- #
# Container memory / OOM monitoring
# --------------------------------------------------------------------------- #


@dataclass
class ContainerStats:
    peak_mem_bytes: float = 0.0
    last_mem_bytes: float = 0.0
    samples: int = 0
    oom_killed: bool = False
    exit_code: int | None = None
    status: str = ""
    error: str = ""


class DockerMonitor:
    """Polls ``docker stats`` for the load containers and tracks peak memory."""

    def __init__(self, containers: tuple[str, ...], interval: float = 1.0):
        self._containers = containers
        self._interval = interval
        self._stats: dict[str, ContainerStats] = {
            c: ContainerStats() for c in containers
        }
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._refresh_inspect()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self._interval)

    def _sample(self) -> None:
        try:
            out = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.Name}}\t{{.MemUsage}}",
                    *self._containers,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except Exception:
            return
        for line in out.stdout.splitlines():
            if "\t" not in line:
                continue
            name, mem_usage = line.split("\t", 1)
            name = name.strip()
            used = mem_usage.split("/")[0].strip()
            used_bytes = human_to_bytes(used)
            with self._lock:
                st = self._stats.get(name)
                if st is None:
                    continue
                st.samples += 1
                st.last_mem_bytes = used_bytes
                st.peak_mem_bytes = max(st.peak_mem_bytes, used_bytes)

    def _refresh_inspect(self) -> None:
        for name in self._containers:
            try:
                out = subprocess.run(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.State.OOMKilled}};{{.State.ExitCode}};"
                        "{{.State.Status}};{{.State.Error}}",
                        name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except Exception:
                continue
            if out.returncode != 0:
                continue
            parts = out.stdout.strip().split(";", 3)
            if len(parts) < 4:
                continue
            oom, code, status, error = parts
            with self._lock:
                st = self._stats[name]
                st.oom_killed = oom.strip().lower() == "true"
                try:
                    st.exit_code = int(code.strip())
                except ValueError:
                    st.exit_code = None
                st.status = status.strip()
                st.error = error.strip()

    def snapshot(self) -> dict[str, ContainerStats]:
        with self._lock:
            return {k: ContainerStats(**vars(v)) for k, v in self._stats.items()}


# --------------------------------------------------------------------------- #
# Request driving
# --------------------------------------------------------------------------- #


@dataclass
class RequestResult:
    index: int
    document_id: str
    case_id: str
    doc_name: str
    raw_bytes: int
    b64_bytes: int
    status_code: int | None = None
    submit_seconds: float = 0.0
    error: str | None = None


@dataclass
class LoadConfig:
    api_url: str
    docs_dir: Path
    callback_host: str
    callback_port: int
    concurrency: int
    num_requests: int
    output_format: str
    callback_url_host: str
    completion_timeout: float
    extensions: tuple[str, ...] = (".pdf",)


def discover_docs(cfg: LoadConfig) -> list[Path]:
    if not cfg.docs_dir.is_dir():
        raise SystemExit(f"Docs directory not found: {cfg.docs_dir}")
    docs = sorted(
        p
        for p in cfg.docs_dir.iterdir()
        if p.is_file() and p.suffix.lower() in cfg.extensions
    )
    if not docs:
        raise SystemExit(
            f"No documents with extensions {cfg.extensions} found in {cfg.docs_dir}"
        )
    return docs


def build_request_body(
    cfg: LoadConfig, doc_b64: str, doc_id: str, case_id: str
) -> dict:
    callback_url = f"http://{cfg.callback_url_host}:{cfg.callback_port}/callback"
    return {
        "jurisdictionId": "load",
        "caseId": case_id,
        "outputFormat": cfg.output_format,
        "subjects": [
            {
                "role": "accused",
                "subject": {
                    "subjectId": "sub1",
                    "name": "Jane Doe",
                },
            }
        ],
        "objects": [
            {
                "document": {
                    "attachmentType": "BASE64",
                    "documentId": doc_id,
                    "content": doc_b64,
                },
                "callbackUrl": callback_url,
            }
        ],
    }


def submit_one(cfg: LoadConfig, index: int, doc: Path, doc_b64: str) -> RequestResult:
    doc_id = f"doc-{index}-{uuid.uuid4().hex[:8]}"
    case_id = f"case-{index}-{uuid.uuid4().hex[:8]}"
    result = RequestResult(
        index=index,
        document_id=doc_id,
        case_id=case_id,
        doc_name=doc.name,
        raw_bytes=doc.stat().st_size,
        b64_bytes=len(doc_b64),
    )
    body = build_request_body(cfg, doc_b64, doc_id, case_id)
    t0 = time.monotonic()
    try:
        resp = requests.post(
            f"{cfg.api_url}/api/v1/redact",
            json=body,
            timeout=cfg.completion_timeout,
        )
        result.status_code = resp.status_code
        if resp.status_code != 201:
            result.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    finally:
        result.submit_seconds = time.monotonic() - t0
    return result


def wait_for_api(api_url: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{api_url}/api/v1/health", timeout=5)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def parse_args(argv: list[str]) -> LoadConfig:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "docs_dir",
        help="Directory of sample documents to send.",
    )
    p.add_argument(
        "--api-url",
        default=os.environ.get("BC_API_URL", "http://localhost:8000"),
        help="Base URL of the API (env: BC_API_URL).",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("BC_LOAD_CONCURRENCY", "4")),
        help="Number of concurrent in-flight redaction requests.",
    )
    p.add_argument(
        "--num-requests",
        type=int,
        default=int(os.environ.get("BC_LOAD_NUM_REQUESTS", "0")),
        help="Total requests to send. 0 (default) = one per document found.",
    )
    p.add_argument(
        "--output-format",
        default=os.environ.get("BC_LOAD_OUTPUT_FORMAT", "PDF"),
        choices=["PDF", "TEXT", "HTML", "JSON"],
        help="Redaction output format (PDF is the most memory-intensive).",
    )
    p.add_argument(
        "--callback-host",
        default=os.environ.get("BC_LOAD_CALLBACK_HOST", "0.0.0.0"),
        help="Host/interface to bind the local callback server to.",
    )
    p.add_argument(
        "--callback-port",
        type=int,
        default=int(os.environ.get("BC_LOAD_CALLBACK_PORT", "9999")),
        help="Port for the local callback server.",
    )
    p.add_argument(
        "--callback-url-host",
        default=os.environ.get("BC_LOAD_CALLBACK_URL_HOST", "host.docker.internal"),
        help=(
            "Host the worker uses to reach the callback server. "
            "Defaults to host.docker.internal (works from inside containers)."
        ),
    )
    p.add_argument(
        "--completion-timeout",
        type=float,
        default=float(os.environ.get("BC_LOAD_COMPLETION_TIMEOUT", "600")),
        help="Seconds to wait for all completion callbacks before giving up.",
    )
    p.add_argument(
        "--extensions",
        default=os.environ.get("BC_LOAD_EXTENSIONS", ".pdf"),
        help="Comma-separated list of file extensions to include.",
    )
    args = p.parse_args(argv)

    extensions = tuple(
        e if e.startswith(".") else f".{e}"
        for e in (x.strip().lower() for x in args.extensions.split(","))
        if e.strip()
    )

    return LoadConfig(
        api_url=args.api_url.rstrip("/"),
        docs_dir=Path(args.docs_dir).expanduser(),
        callback_host=args.callback_host,
        callback_port=args.callback_port,
        concurrency=max(1, args.concurrency),
        num_requests=args.num_requests,
        output_format=args.output_format,
        callback_url_host=args.callback_url_host,
        completion_timeout=args.completion_timeout,
        extensions=extensions,
    )


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


@dataclass
class RunReport:
    results: list[RequestResult] = field(default_factory=list)
    callbacks: dict[str, CallbackRecord] = field(default_factory=dict)
    container_stats: dict[str, ContainerStats] = field(default_factory=dict)
    wall_seconds: float = 0.0


def _print_container_section(report: RunReport) -> bool:
    """Print per-container peak memory / OOM status. Returns whether OOM seen."""
    print("\nContainers (peak memory / OOM status)")
    oom_detected = False
    for name in CONTAINERS:
        st = report.container_stats.get(name, ContainerStats())
        flags = []
        if st.oom_killed:
            flags.append("OOM-KILLED")
            oom_detected = True
        if st.exit_code not in (None, 0):
            flags.append(f"exit={st.exit_code}")
            if st.exit_code == 137:
                oom_detected = True
        flag_str = f"  [{', '.join(flags)}]" if flags else ""
        print(
            f"  {name:<18} peak {bytes_to_human(st.peak_mem_bytes):>10}"
            f"  status={st.status or '?'}{flag_str}"
        )
    return oom_detected


def print_report(cfg: LoadConfig, report: RunReport) -> int:
    line = "=" * 72
    print(f"\n{line}\nLOAD TEST REPORT\n{line}")

    total = len(report.results)
    accepted = sum(1 for r in report.results if r.status_code == 201)
    submit_errors = [r for r in report.results if r.error]

    print("\nRequests")
    print(f"  total submitted:     {total}")
    print(f"  accepted (HTTP 201): {accepted}")
    print(f"  submit errors:       {len(submit_errors)}")
    print(f"  concurrency:         {cfg.concurrency}")
    print(f"  output format:       {cfg.output_format}")
    print(f"  wall time:           {report.wall_seconds:.1f}s")

    if report.results:
        sizes = [r.raw_bytes for r in report.results]
        print(
            f"  doc size (raw):      min {bytes_to_human(min(sizes))}, "
            f"max {bytes_to_human(max(sizes))}"
        )

    # Completion callbacks
    complete = sum(1 for c in report.callbacks.values() if c.status == "COMPLETE")
    errored = sum(1 for c in report.callbacks.values() if c.status == "ERROR")
    print("\nCompletion callbacks")
    print(f"  received:  {len(report.callbacks)} / {accepted}")
    print(f"  COMPLETE:  {complete}")
    print(f"  ERROR:     {errored}")
    print(f"  missing:   {accepted - len(report.callbacks)}")

    # Per-container memory and OOM status
    oom_detected = _print_container_section(report)

    # Surface callback / submit errors so failures are easy to read.
    failures = [r for r in report.results if r.error]
    cb_errors = [c for c in report.callbacks.values() if c.status == "ERROR"]
    if failures:
        print("\nSubmit errors (first 5):")
        for r in failures[:5]:
            print(f"  - doc={r.doc_name} ({bytes_to_human(r.raw_bytes)}): {r.error}")
    if cb_errors:
        print("\nCallback errors (first 5):")
        for c in cb_errors[:5]:
            print(f"  - {c.document_id}: {c.error}")

    print(f"\n{line}")
    if oom_detected:
        print("RESULT: OOM detected (container OOM-killed / exit 137). Reproduced!")
    elif accepted and len(report.callbacks) < accepted:
        print(
            "RESULT: Some requests never completed (no callback). Likely a "
            "worker crash/restart -- inspect `docker compose logs worker`."
        )
    elif errored:
        print(
            "RESULT: Completed with redaction ERRORs -- inspect callback errors above."
        )
    else:
        print(
            "RESULT: All requests completed cleanly. "
            "Try larger docs / more concurrency."
        )
    print(line)

    # Non-zero exit if we failed to fully complete, so this is CI/script friendly.
    return 0 if (accepted and len(report.callbacks) == accepted and not errored) else 1


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main(argv: list[str]) -> int:
    cfg = parse_args(argv)
    docs = discover_docs(cfg)
    num_requests = cfg.num_requests or len(docs)

    print(f"Discovered {len(docs)} document(s) in {cfg.docs_dir}:")
    for d in docs:
        print(f"  - {d.name} ({bytes_to_human(d.stat().st_size)})")
    print(f"Will send {num_requests} request(s) at concurrency {cfg.concurrency}.")

    print(f"Waiting for API at {cfg.api_url} ...")
    if not wait_for_api(cfg.api_url):
        raise SystemExit(
            f"API at {cfg.api_url} did not become healthy. Is the stack up? "
            f"(docker compose -f tests/load/docker-compose.load.yml up --build -d)"
        )
    print("API is healthy.")

    # Pre-encode each document once and reuse across requests.
    encoded: dict[str, str] = {}
    for d in docs:
        encoded[d.name] = base64.b64encode(d.read_bytes()).decode("ascii")

    callbacks = CallbackCollector(cfg.callback_host, cfg.callback_port)
    callbacks.start()
    monitor = DockerMonitor(CONTAINERS)
    monitor.start()

    results: list[RequestResult] = []
    submitted_doc_ids: set[str] = set()
    t0 = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
            futures = []
            for i in range(num_requests):
                doc = docs[i % len(docs)]
                futures.append(pool.submit(submit_one, cfg, i, doc, encoded[doc.name]))
            for fut in as_completed(futures):
                res = fut.result()
                results.append(res)
                if res.status_code == 201:
                    submitted_doc_ids.add(res.document_id)
                status = "OK" if res.status_code == 201 else f"FAIL({res.error})"
                print(
                    f"  [submit {res.index}] {res.doc_name} "
                    f"({bytes_to_human(res.raw_bytes)} -> "
                    f"{bytes_to_human(res.b64_bytes)} b64) "
                    f"{status} in {res.submit_seconds:.2f}s"
                )

        if submitted_doc_ids:
            print(
                f"\nWaiting up to {cfg.completion_timeout:.0f}s for "
                f"{len(submitted_doc_ids)} completion callback(s) ..."
            )
            callbacks.wait_for(submitted_doc_ids, cfg.completion_timeout)
    finally:
        wall = time.monotonic() - t0
        monitor.stop()
        callbacks.stop()

    report = RunReport(
        results=results,
        callbacks=callbacks.snapshot(),
        container_stats=monitor.snapshot(),
        wall_seconds=wall,
    )
    return print_report(cfg, report)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
