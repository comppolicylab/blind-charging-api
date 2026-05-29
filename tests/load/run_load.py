#!/usr/bin/env python
"""Load driver for reproducing OOM / memory issues on the redaction path.

This script drives the stack defined in ``docker-compose.load.yml`` (Redis +
API + Celery worker). It reads large sample documents from a local directory
(passed as the ``docs_dir`` positional argument; these are deliberately
*not* checked into the repo) and fires concurrent ``POST /api/v1/redact``
requests.

Two document-delivery paths can be exercised via ``--attachment-type``:

  * ``base64`` (default) -- each document is base64-encoded and carried
    *inline* in the request body. This is the path users have reported
    running out of memory on (the payload is buffered, serialized into a
    Celery message, stored in Redis, then re-loaded and decoded).
  * ``link`` -- the driver spins up a temporary local HTTP file server that
    serves the docs directory, and passes the worker a ``host.docker.internal``
    URL to pull each document from. The theory is this path is far more
    scalable; use it to compare memory behavior against ``base64``.

While the requests run, the driver:
  * receives the worker's completion callbacks on a small local HTTP server
    (reachable from the worker container via ``host.docker.internal``),
  * samples per-container memory/CPU with ``docker stats`` as a time series,
  * detects OOM-kills, both of a whole container (``State.OOMKilled`` / exit
    code 137) and of a single process inside a still-running container (e.g. a
    Celery child worker), via streaming ``docker events --filter event=oom``,

and prints a summary report at the end. Each run also writes tangible
artifacts to a timestamped directory under ``--results-dir`` (default
``tests/load/results/``):

  * ``memory.csv``   -- the raw per-container memory/CPU time series,
  * ``summary.json`` -- run config, per-container peaks/limits, and outcome,
  * ``memory.svg``   -- a memory-over-time graph (with memory-limit lines).

To compare runs (e.g. before/after a fix), overlay one container's memory
across every recorded run with ``--compare``.

Example:

    cd tests/load
    docker compose -f docker-compose.load.yml up --build -d
    uv run python tests/load/run_load.py /path/to/big/pdfs \\
        --concurrency 8 --num-requests 16 --label baseline
    docker compose -f docker-compose.load.yml down -v

    # ...make a fix, re-run with --label streaming-fix, then compare:
    uv run python tests/load/run_load.py --compare

Run ``uv run python tests/load/run_load.py --help`` for all options.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from http.server import (
    BaseHTTPRequestHandler,
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
# Document file server (LINK attachment path)
# --------------------------------------------------------------------------- #


class DocServer:
    """A tiny threaded HTTP server that serves the sample documents.

    Used by the ``LINK`` attachment path: instead of inlining a base64 copy of
    each document in the request body, we serve the docs directory over HTTP
    and hand the worker a URL to pull from. The worker reaches this server via
    ``host.docker.internal`` (mapped to the host gateway in
    ``docker-compose.load.yml``), so only a URL -- not the document bytes --
    travels through the API/Redis/Celery message path.
    """

    def __init__(self, host: str, port: int, directory: Path):
        # ``SimpleHTTPRequestHandler`` serves files relative to ``directory``
        # (Python 3.7+). Bind it via ``partial`` so each request handler is
        # rooted at the docs directory.
        handler = partial(_QuietFileHandler, directory=str(directory))
        self._server = ThreadingHTTPServer((host, port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _QuietFileHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # silence per-request logging
        pass


# --------------------------------------------------------------------------- #
# Container memory / OOM monitoring
# --------------------------------------------------------------------------- #


@dataclass
class ContainerStats:
    peak_mem_bytes: float = 0.0
    last_mem_bytes: float = 0.0
    mem_limit_bytes: float = 0.0
    samples: int = 0
    # Whether the *container* (PID 1) was OOM-killed, per `docker inspect`.
    oom_killed: bool = False
    # Count of kernel OOM-kill events seen on this container's cgroup, per
    # `docker events`. This fires even when the container itself survives --
    # e.g. when a Celery *child* worker process is killed but the parent (PID 1)
    # respawns it, so `oom_killed`/`exit_code` alone would miss the failure.
    oom_events: int = 0
    exit_code: int | None = None
    status: str = ""
    error: str = ""


@dataclass
class Tick:
    """A single ``docker stats`` sample across all containers."""

    elapsed: float
    mem_bytes: dict[str, float] = field(default_factory=dict)
    cpu_percent: dict[str, float] = field(default_factory=dict)


class DockerMonitor:
    """Polls ``docker stats`` for the load containers and records a time series.

    In addition to tracking peak / last memory per container (used for the
    text report), this keeps a full time series of every sample so we can plot
    memory-over-time and compare runs.
    """

    def __init__(self, containers: tuple[str, ...], interval: float = 1.0):
        self._containers = containers
        self._interval = interval
        self._stats: dict[str, ContainerStats] = {
            c: ContainerStats() for c in containers
        }
        self._ticks: list[Tick] = []
        self._start: float = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        # Streaming `docker events` watcher for kernel OOM-kill events. This is
        # the only way to observe an OOM-kill of a *process inside* a container
        # (e.g. a Celery child) when the container itself keeps running.
        self._events_proc: subprocess.Popen | None = None
        self._events_thread = threading.Thread(target=self._watch_events, daemon=True)

    def start(self) -> None:
        self._start = time.monotonic()
        self._start_event_watch()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._stop_event_watch()
        self._refresh_inspect()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self._interval)

    def _start_event_watch(self) -> None:
        """Start streaming `docker events` to catch in-container OOM-kills.

        The kernel OOM-killer fires per-cgroup and may kill a child process
        (e.g. a Celery worker) without taking down the container's PID 1. In
        that case `docker inspect` still reports ``OOMKilled=false`` and a
        zero exit code, so the inspect-based check below would miss it. Docker
        emits a container ``oom`` event for every such kill regardless of
        whether the container survives, so we listen for those too.
        """
        cmd = [
            "docker",
            "events",
            "--filter",
            "type=container",
            "--filter",
            "event=oom",
            "--format",
            "{{json .}}",
        ]
        for c in self._containers:
            cmd += ["--filter", f"container={c}"]
        try:
            self._events_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            self._events_proc = None
            return
        self._events_thread.start()

    def _watch_events(self) -> None:
        proc = self._events_proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except ValueError:
                continue
            attrs = evt.get("Actor", {}).get("Attributes", {})
            name = attrs.get("name") or evt.get("id", "")
            with self._lock:
                st = self._stats.get(name)
                if st is None:
                    continue
                st.oom_events += 1

    def _stop_event_watch(self) -> None:
        proc = self._events_proc
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self._events_thread.is_alive():
            self._events_thread.join(timeout=5)

    def _sample(self) -> None:
        try:
            out = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}",
                    *self._containers,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except Exception:
            return
        elapsed = time.monotonic() - self._start
        tick = Tick(elapsed=elapsed)
        for line in out.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[0].strip()
            mem_usage = parts[1]
            cpu_raw = parts[2] if len(parts) > 2 else ""
            used_str, _, limit_str = mem_usage.partition("/")
            used_bytes = human_to_bytes(used_str.strip())
            limit_bytes = human_to_bytes(limit_str.strip())
            try:
                cpu_pct = float(cpu_raw.strip().rstrip("%"))
            except ValueError:
                cpu_pct = 0.0
            with self._lock:
                st = self._stats.get(name)
                if st is None:
                    continue
                st.samples += 1
                st.last_mem_bytes = used_bytes
                st.peak_mem_bytes = max(st.peak_mem_bytes, used_bytes)
                if limit_bytes > 0:
                    st.mem_limit_bytes = limit_bytes
                tick.mem_bytes[name] = used_bytes
                tick.cpu_percent[name] = cpu_pct
        if tick.mem_bytes:
            with self._lock:
                self._ticks.append(tick)

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

    def ticks(self) -> list[Tick]:
        with self._lock:
            return [
                Tick(
                    elapsed=t.elapsed,
                    mem_bytes=dict(t.mem_bytes),
                    cpu_percent=dict(t.cpu_percent),
                )
                for t in self._ticks
            ]


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
    results_dir: Path
    label: str
    write_graph: bool
    attachment_type: str = "BASE64"
    doc_server_host: str = "0.0.0.0"
    doc_server_port: int = 9998
    extensions: tuple[str, ...] = (".pdf",)

    @property
    def is_link(self) -> bool:
        return self.attachment_type == "LINK"


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


def build_document_object(
    cfg: LoadConfig, doc: Path, doc_id: str, doc_b64: str
) -> dict:
    """Build the ``document`` payload for the configured attachment type.

    For ``LINK`` we point the worker at the local doc server (reachable from
    the worker container via ``callback_url_host``); for ``BASE64`` we inline
    the encoded document bytes.
    """
    if cfg.is_link:
        url = f"http://{cfg.callback_url_host}:{cfg.doc_server_port}/{quote(doc.name)}"
        return {
            "attachmentType": "LINK",
            "documentId": doc_id,
            "url": url,
        }
    return {
        "attachmentType": "BASE64",
        "documentId": doc_id,
        "content": doc_b64,
    }


def build_request_body(
    cfg: LoadConfig, doc: Path, doc_b64: str, doc_id: str, case_id: str
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
                "document": build_document_object(cfg, doc, doc_id, doc_b64),
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
    body = build_request_body(cfg, doc, doc_b64, doc_id, case_id)
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
        "--attachment-type",
        default=os.environ.get("BC_LOAD_ATTACHMENT_TYPE", "base64"),
        choices=["base64", "link"],
        help=(
            "How documents are delivered to the worker. 'base64' (default) "
            "inlines each document in the request body; 'link' serves the docs "
            "from a temporary local HTTP server and passes the worker a URL to "
            "pull from (the more scalable path)."
        ),
    )
    p.add_argument(
        "--doc-server-host",
        default=os.environ.get("BC_LOAD_DOC_SERVER_HOST", "0.0.0.0"),
        help=(
            "Host/interface to bind the local document file server to "
            "(only used for --attachment-type link)."
        ),
    )
    p.add_argument(
        "--doc-server-port",
        type=int,
        default=int(os.environ.get("BC_LOAD_DOC_SERVER_PORT", "9998")),
        help=(
            "Port for the local document file server "
            "(only used for --attachment-type link)."
        ),
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
    p.add_argument(
        "--results-dir",
        default=os.environ.get(
            "BC_LOAD_RESULTS_DIR", str(Path(__file__).parent / "results")
        ),
        help=(
            "Directory to write per-run artifacts (CSV, JSON, graph) into. "
            "Each run creates a timestamped subdirectory (env: BC_LOAD_RESULTS_DIR)."
        ),
    )
    p.add_argument(
        "--label",
        default=os.environ.get("BC_LOAD_LABEL", ""),
        help=(
            "Optional human-readable label for this run (e.g. 'baseline', "
            "'streaming-fix'). Included in the artifact dir name and graph title."
        ),
    )
    p.add_argument(
        "--no-graph",
        action="store_true",
        default=os.environ.get("BC_LOAD_NO_GRAPH", "").lower() in ("1", "true", "yes"),
        help="Skip rendering the memory-over-time graph (CSV/JSON still written).",
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
        results_dir=Path(args.results_dir).expanduser(),
        label=args.label.strip(),
        write_graph=not args.no_graph,
        attachment_type=args.attachment_type.upper(),
        doc_server_host=args.doc_server_host,
        doc_server_port=args.doc_server_port,
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
    ticks: list[Tick] = field(default_factory=list)
    wall_seconds: float = 0.0
    started_at: float = 0.0


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
        if st.oom_events:
            # In-container OOM-kill(s) (e.g. a Celery child) -- the container
            # may still be running, but the kernel killed a process inside it.
            flags.append(f"oom-events={st.oom_events}")
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
    print(f"  attachment type:     {cfg.attachment_type}")
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
        print(
            "RESULT: OOM detected (container OOM-killed / exit 137, or an "
            "in-container OOM-kill event). Reproduced!"
        )
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

    # Non-zero exit if we OOM'd or failed to fully complete, so this is
    # CI/script friendly. An OOM-kill (container- or process-level) always
    # fails the run even if every callback somehow still arrived.
    if oom_detected:
        return 1
    return 0 if (accepted and len(report.callbacks) == accepted and not errored) else 1


# --------------------------------------------------------------------------- #
# Artifacts: time-series CSV, JSON summary, and a dependency-free SVG graph
# --------------------------------------------------------------------------- #

# Stable colors for the known containers so graphs are comparable across runs.
_CONTAINER_COLORS = {
    "bc-load-api": "#1f77b4",
    "bc-load-worker": "#d62728",
    "bc-load-redis": "#2ca02c",
}
# Fallback palette for anything else (e.g. run series in compare mode).
_PALETTE = [
    "#1f77b4",
    "#d62728",
    "#2ca02c",
    "#9467bd",
    "#ff7f0e",
    "#17becf",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
]


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _nice_ticks(vmax: float, count: int = 5) -> list[float]:
    """Return ~`count` evenly spaced 'nice' tick values from 0..>=vmax."""
    if vmax <= 0:
        return [0.0]
    raw = vmax / count
    mag = 10 ** math.floor(math.log10(raw))
    norm = raw / mag
    if norm < 1.5:
        step = 1.0
    elif norm < 3:
        step = 2.0
    elif norm < 7:
        step = 5.0
    else:
        step = 10.0
    step *= mag
    ticks: list[float] = []
    v = 0.0
    while v <= vmax + step * 0.5:
        ticks.append(round(v, 6))
        v += step
    return ticks


@dataclass
class ChartLine:
    label: str
    color: str
    points: list[tuple[float, float]]
    dashed: bool = False


def _line_chart_svg(
    lines: list[ChartLine],
    *,
    title: str,
    subtitle: str,
    x_label: str,
    y_label: str,
    path: Path,
    plot_w: int = 760,
    height: int = 560,
) -> None:
    """Render a multi-series line chart as a self-contained SVG (no deps)."""
    m_left, m_top, m_bottom = 72, 74, 56
    # Size the legend column to the longest label so nothing gets clipped.
    longest = max((len(ln.label) for ln in lines), default=0)
    m_right = max(150, min(420, 52 + int(longest * 6.6)))
    width = m_left + plot_w + m_right
    plot_h = height - m_top - m_bottom

    x_max = max((x for ln in lines for x, _ in ln.points), default=1.0) or 1.0
    y_max = max((y for ln in lines for _, y in ln.points), default=1.0) or 1.0
    x_max *= 1.02
    y_max *= 1.10

    def sx(x: float) -> float:
        return m_left + (x / x_max) * plot_w

    def sy(y: float) -> float:
        return m_top + plot_h - (y / y_max) * plot_h

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        'font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif">'
    )
    parts.append(f'<rect width="{width}" height="{height}" fill="#ffffff"/>')
    parts.append(
        f'<text x="{m_left}" y="28" font-size="18" font-weight="600" '
        f'fill="#111">{esc(title)}</text>'
    )
    if subtitle:
        parts.append(
            f'<text x="{m_left}" y="48" font-size="12" fill="#666">'
            f"{esc(subtitle)}</text>"
        )

    # Plot border
    parts.append(
        f'<rect x="{m_left}" y="{m_top}" width="{plot_w}" height="{plot_h}" '
        'fill="#fafafa" stroke="#ddd"/>'
    )

    # Y grid + labels
    for ty in _nice_ticks(y_max / 1.10):
        y = sy(ty)
        parts.append(
            f'<line x1="{m_left}" y1="{y:.1f}" x2="{m_left + plot_w}" '
            f'y2="{y:.1f}" stroke="#eee"/>'
        )
        parts.append(
            f'<text x="{m_left - 8}" y="{y + 4:.1f}" font-size="11" '
            f'fill="#666" text-anchor="end">{ty:g}</text>'
        )

    # X grid + labels
    for tx in _nice_ticks(x_max / 1.02):
        x = sx(tx)
        parts.append(
            f'<line x1="{x:.1f}" y1="{m_top}" x2="{x:.1f}" '
            f'y2="{m_top + plot_h}" stroke="#eee"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{m_top + plot_h + 18:.1f}" font-size="11" '
            f'fill="#666" text-anchor="middle">{tx:g}</text>'
        )

    # Axis labels
    parts.append(
        f'<text x="{m_left + plot_w / 2:.1f}" y="{height - 14}" font-size="12" '
        f'fill="#444" text-anchor="middle">{esc(x_label)}</text>'
    )
    ylx, yly = 20, m_top + plot_h / 2
    parts.append(
        f'<text x="{ylx}" y="{yly:.1f}" font-size="12" fill="#444" '
        f'text-anchor="middle" transform="rotate(-90 {ylx} {yly:.1f})">'
        f"{esc(y_label)}</text>"
    )

    # Series
    for ln in lines:
        if not ln.points:
            continue
        pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in ln.points)
        dash = ' stroke-dasharray="6 5"' if ln.dashed else ""
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{ln.color}" '
            f'stroke-width="2"{dash}/>'
        )

    # Legend
    lx = m_left + plot_w + 24
    ly = m_top + 4
    for ln in lines:
        dash = ' stroke-dasharray="6 5"' if ln.dashed else ""
        parts.append(
            f'<line x1="{lx}" y1="{ly + 4}" x2="{lx + 22}" y2="{ly + 4}" '
            f'stroke="{ln.color}" stroke-width="3"{dash}/>'
        )
        parts.append(
            f'<text x="{lx + 30}" y="{ly + 8}" font-size="12" fill="#333">'
            f"{esc(ln.label)}</text>"
        )
        ly += 22

    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_artifacts(cfg: LoadConfig, report: RunReport) -> Path:
    """Persist CSV + JSON (+ optional SVG graph) for this run; return the dir."""
    stamp = datetime.fromtimestamp(report.started_at or time.time()).strftime(
        "%Y%m%d-%H%M%S"
    )
    name = f"{stamp}-{_slugify(cfg.label)}" if cfg.label else stamp
    run_dir = cfg.results_dir / name
    run_dir.mkdir(parents=True, exist_ok=True)

    # --- memory.csv: wide format, one row per sample tick --------------------
    csv_path = run_dir / "memory.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        header = ["elapsed_s"]
        for c in CONTAINERS:
            header += [f"{c}_mem_mb", f"{c}_cpu_pct"]
        w.writerow(header)
        for t in report.ticks:
            row: list[str] = [f"{t.elapsed:.2f}"]
            for c in CONTAINERS:
                mem = t.mem_bytes.get(c)
                cpu = t.cpu_percent.get(c)
                row.append(f"{mem / 1e6:.2f}" if mem is not None else "")
                row.append(f"{cpu:.2f}" if cpu is not None else "")
            w.writerow(row)

    # --- summary.json: config + per-container peaks + outcome ----------------
    accepted = sum(1 for r in report.results if r.status_code == 201)
    summary: dict[str, Any] = {
        "label": cfg.label,
        "started_at": report.started_at,
        "timestamp": stamp,
        "wall_seconds": report.wall_seconds,
        "config": {
            "concurrency": cfg.concurrency,
            "num_requests": cfg.num_requests or len(report.results),
            "output_format": cfg.output_format,
            "attachment_type": cfg.attachment_type,
        },
        "requests": {
            "submitted": len(report.results),
            "accepted": accepted,
            "callbacks": len(report.callbacks),
        },
        "containers": {
            name: {
                "peak_mem_mb": round(st.peak_mem_bytes / 1e6, 2),
                "mem_limit_mb": round(st.mem_limit_bytes / 1e6, 2),
                "oom_killed": st.oom_killed,
                "oom_events": st.oom_events,
                "exit_code": st.exit_code,
                "samples": st.samples,
            }
            for name, st in report.container_stats.items()
        },
        "files": {"memory_csv": csv_path.name},
    }
    graph_path = run_dir / "memory.svg"
    if cfg.write_graph and report.ticks:
        _render_run_graph(cfg, report, graph_path)
        summary["files"]["memory_svg"] = graph_path.name
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return run_dir


def _render_run_graph(cfg: LoadConfig, report: RunReport, path: Path) -> None:
    """Build the per-run memory-over-time graph (one line per container)."""
    lines: list[ChartLine] = []
    for c in CONTAINERS:
        pts = [
            (t.elapsed, t.mem_bytes[c] / 1e6) for t in report.ticks if c in t.mem_bytes
        ]
        if not pts:
            continue
        color = _CONTAINER_COLORS.get(c, "#333")
        lines.append(ChartLine(label=c, color=color, points=pts))
        # Memory-limit reference line (dashed) so headroom is obvious.
        st = report.container_stats.get(c)
        if st and st.mem_limit_bytes > 0:
            limit_mb = st.mem_limit_bytes / 1e6
            x_max = max(t.elapsed for t in report.ticks)
            lines.append(
                ChartLine(
                    label=f"{c} limit ({limit_mb:.0f}MB)",
                    color=color,
                    points=[(0.0, limit_mb), (x_max, limit_mb)],
                    dashed=True,
                )
            )

    oom = [
        n
        for n, st in report.container_stats.items()
        if st.oom_killed or st.oom_events or st.exit_code == 137
    ]
    title = (
        f"Container memory over time — {cfg.label}"
        if cfg.label
        else ("Container memory over time")
    )
    subtitle = (
        f"concurrency={cfg.concurrency}  format={cfg.output_format}  "
        f"attachment={cfg.attachment_type}  "
        f"requests={cfg.num_requests or len(report.results)}  "
        f"wall={report.wall_seconds:.0f}s"
    )
    if oom:
        subtitle += f"  OOM-KILLED: {', '.join(oom)}"
    _line_chart_svg(
        lines,
        title=title,
        subtitle=subtitle,
        x_label="elapsed (s)",
        y_label="memory (MB)",
        path=path,
    )


# --------------------------------------------------------------------------- #
# Compare mode: overlay one container's memory across multiple runs
# --------------------------------------------------------------------------- #


def _load_run_series(run_dir: Path, container: str) -> list[tuple[float, float]]:
    csv_path = run_dir / "memory.csv"
    if not csv_path.is_file():
        return []
    col = f"{container}_mem_mb"
    pts: list[tuple[float, float]] = []
    with csv_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or col not in reader.fieldnames:
            return []
        for row in reader:
            raw = row.get(col, "")
            if not raw:
                continue
            try:
                pts.append((float(row["elapsed_s"]), float(raw)))
            except ValueError, KeyError:
                continue
    return pts


def run_compare(results_dir: Path, container: str, output: Path | None) -> int:
    """Overlay `container` memory across every run found in `results_dir`."""
    if not results_dir.is_dir():
        raise SystemExit(f"Results dir not found: {results_dir}")
    run_dirs = sorted(d for d in results_dir.iterdir() if (d / "memory.csv").is_file())
    if not run_dirs:
        raise SystemExit(f"No runs (memory.csv) found under {results_dir}")

    lines: list[ChartLine] = []
    for i, d in enumerate(run_dirs):
        pts = _load_run_series(d, container)
        if not pts:
            continue
        label = d.name
        summ = d / "summary.json"
        if summ.is_file():
            try:
                meta = json.loads(summ.read_text())
                peak = meta.get("containers", {}).get(container, {}).get("peak_mem_mb")
                if peak is not None:
                    label = f"{d.name} (peak {peak:.0f}MB)"
            except Exception:
                pass
        lines.append(
            ChartLine(label=label, color=_PALETTE[i % len(_PALETTE)], points=pts)
        )

    if not lines:
        raise SystemExit(
            f"No data for container '{container}' in any run under {results_dir}"
        )

    out = output or (
        results_dir / f"compare-{container}-"
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.svg"
    )
    _line_chart_svg(
        lines,
        title=f"{container}: memory over time across {len(lines)} run(s)",
        subtitle="Compare runs to see the effect of fixes on memory usage.",
        x_label="elapsed (s)",
        y_label="memory (MB)",
        path=out,
    )
    print(f"Wrote comparison graph: {out}")
    return 0


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def _maybe_run_compare(argv: list[str]) -> int | None:
    """If invoked with --compare, render an overlay graph and return an exit code.

    Returns None when --compare is not present, so normal load testing proceeds.
    """
    if "--compare" not in argv:
        return None
    p = argparse.ArgumentParser(
        prog="run_load.py --compare",
        description="Overlay one container's memory across previously recorded runs.",
    )
    p.add_argument("--compare", action="store_true")
    p.add_argument(
        "--results-dir",
        default=os.environ.get(
            "BC_LOAD_RESULTS_DIR", str(Path(__file__).parent / "results")
        ),
        help="Directory containing per-run subdirectories.",
    )
    p.add_argument(
        "--container",
        default="bc-load-worker",
        choices=CONTAINERS,
        help="Which container's memory series to overlay (default worker).",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Output SVG path (default: <results-dir>/compare-<container>-<ts>.svg).",
    )
    args = p.parse_args(argv)
    return run_compare(
        Path(args.results_dir).expanduser(),
        args.container,
        Path(args.output).expanduser() if args.output else None,
    )


def _emit_artifacts(cfg: LoadConfig, report: RunReport) -> None:
    try:
        run_dir = write_artifacts(cfg, report)
    except Exception as e:
        print(f"\nWARNING: failed to write artifacts: {type(e).__name__}: {e}")
        return
    print(f"\nArtifacts written to: {run_dir}")
    graph = run_dir / "memory.svg"
    if graph.is_file():
        print(f"  memory graph: {graph}")
    print(
        "  compare runs: uv run python tests/load/run_load.py --compare "
        f"--results-dir {cfg.results_dir}"
    )


def _print_plan(cfg: LoadConfig, docs: list[Path], num_requests: int) -> None:
    print(f"Discovered {len(docs)} document(s) in {cfg.docs_dir}:")
    for d in docs:
        print(f"  - {d.name} ({bytes_to_human(d.stat().st_size)})")
    print(
        f"Will send {num_requests} request(s) at concurrency {cfg.concurrency} "
        f"via {cfg.attachment_type} attachments."
    )


def _ensure_api_healthy(cfg: LoadConfig) -> None:
    print(f"Waiting for API at {cfg.api_url} ...")
    if not wait_for_api(cfg.api_url):
        raise SystemExit(
            f"API at {cfg.api_url} did not become healthy. Is the stack up? "
            f"(docker compose -f tests/load/docker-compose.load.yml up --build -d)"
        )
    print("API is healthy.")


def _prepare_attachments(
    cfg: LoadConfig, docs: list[Path]
) -> tuple[dict[str, str], DocServer | None]:
    """Prepare document payloads for the configured attachment mode.

    In BASE64 mode, pre-encode each document once and reuse across requests.
    In LINK mode we skip encoding entirely and serve the docs over HTTP.
    """
    if cfg.is_link:
        doc_server = DocServer(cfg.doc_server_host, cfg.doc_server_port, cfg.docs_dir)
        doc_server.start()
        print(
            f"Serving docs over HTTP on port {doc_server.port}; worker will "
            f"pull from http://{cfg.callback_url_host}:{cfg.doc_server_port}/"
        )
        return {}, doc_server

    encoded = {d.name: base64.b64encode(d.read_bytes()).decode("ascii") for d in docs}
    return encoded, None


def _format_result(cfg: LoadConfig, res: RequestResult) -> str:
    status = "OK" if res.status_code == 201 else f"FAIL({res.error})"
    size_note = (
        f"({bytes_to_human(res.raw_bytes)} via link)"
        if cfg.is_link
        else f"({bytes_to_human(res.raw_bytes)} -> {bytes_to_human(res.b64_bytes)} b64)"
    )
    return (
        f"  [submit {res.index}] {res.doc_name} {size_note} "
        f"{status} in {res.submit_seconds:.2f}s"
    )


def _submit_requests(
    cfg: LoadConfig,
    docs: list[Path],
    encoded: dict[str, str],
    num_requests: int,
) -> tuple[list[RequestResult], set[str]]:
    results: list[RequestResult] = []
    submitted_doc_ids: set[str] = set()
    with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
        futures = []
        for i in range(num_requests):
            doc = docs[i % len(docs)]
            doc_b64 = encoded.get(doc.name, "")
            futures.append(pool.submit(submit_one, cfg, i, doc, doc_b64))
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            if res.status_code == 201:
                submitted_doc_ids.add(res.document_id)
            print(_format_result(cfg, res))
    return results, submitted_doc_ids


def main(argv: list[str]) -> int:
    compare_rc = _maybe_run_compare(argv)
    if compare_rc is not None:
        return compare_rc

    cfg = parse_args(argv)
    docs = discover_docs(cfg)
    num_requests = cfg.num_requests or len(docs)

    _print_plan(cfg, docs, num_requests)
    _ensure_api_healthy(cfg)
    encoded, doc_server = _prepare_attachments(cfg, docs)

    callbacks = CallbackCollector(cfg.callback_host, cfg.callback_port)
    callbacks.start()
    monitor = DockerMonitor(CONTAINERS)
    monitor.start()

    results: list[RequestResult] = []
    started_at = time.time()
    t0 = time.monotonic()
    try:
        results, submitted_doc_ids = _submit_requests(cfg, docs, encoded, num_requests)
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
        if doc_server is not None:
            doc_server.stop()

    report = RunReport(
        results=results,
        callbacks=callbacks.snapshot(),
        container_stats=monitor.snapshot(),
        ticks=monitor.ticks(),
        wall_seconds=wall,
        started_at=started_at,
    )
    rc = print_report(cfg, report)
    _emit_artifacts(cfg, report)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
