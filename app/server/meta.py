import logging
import os
import platform
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TypedDict

import kombu.utils.json as json
import psutil
import tomllib
import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from psutil._common import snetio
from sqlalchemy import text

from ..logo import api_logo, cpl_logo
from .config import config
from .tasks.queue import queue
from .time import utcnow

log = logging.getLogger(__name__)

meta_router = APIRouter()


@dataclass
class SystemInfo:
    healthy: bool
    errors: list[str]
    warnings: list[str]
    cpu_count: int
    cpu_freq: float
    cpu_percent: float
    cpu_temp: float
    mem_total: float
    mem_used: float
    mem_percent: float
    swap_total: float
    swap_used: float
    swap_percent: float
    disk_total: float
    disk_used: float
    disk_percent: float
    net_io: snetio


# Taken from:
# https://docs.celeryq.dev/en/stable/reference/celery.app.control.html#celery.app.control.Inspect.query_task
CeleryTaskInfo = TypedDict(
    "CeleryTaskInfo",
    {
        "id": str,
        "name": str,
        "args": list,
        "kwargs": dict,
        "type": str,
        "hostname": str,
        "time_start": datetime,
        "acknowledged": bool,
        "delivery_info": dict,
        "worker_pid": int,
    },
)

CeleryScheduledTaskInfo = TypedDict(
    "CeleryScheduledTaskInfo",
    {
        "eta": str,
        "priority": int,
        "request": CeleryTaskInfo,
    },
)


@dataclass
class WorkerInfo:
    name: str
    healthy: bool
    errors: list[str]
    warnings: list[str]
    registered_tasks: list[str]
    report: str
    uptime: int
    usage: dict[str, int]
    active: list[CeleryTaskInfo]
    scheduled: list[CeleryScheduledTaskInfo]
    revoked: list[str]


@dataclass
class QueueInfo:
    errors: list[str]
    warnings: list[str]
    healthy: bool
    total_workers: int
    healthy_workers: int
    unhealthy_workers: int
    workers: list["WorkerInfo"]
    broker: str
    broker_host: str


@dataclass
class PlatformInfo:
    py_version: str
    os: str
    os_version: str
    hostname: str
    processor: str


@dataclass
class ApiHealth:
    healthy: bool
    errors: list[str]
    warnings: list[str]
    startup_time: str
    current_time: str
    api_version: str
    schema_version: str


@dataclass
class DbHealth:
    healthy: bool
    errors: list[str]
    warnings: list[str]
    engine: str


@dataclass
class ApiMeta:
    platform: PlatformInfo
    api: ApiHealth
    queue: QueueInfo
    system: SystemInfo
    db: DbHealth

    def healthy(self) -> bool:
        return all(
            [self.api.healthy, self.queue.healthy, self.system.healthy, self.db.healthy]
        )

    def errors(self) -> list[str]:
        return self.api.errors + self.queue.errors + self.system.errors + self.db.errors


def _get_system_info() -> SystemInfo:
    cpu_count = psutil.cpu_count(logical=False)
    cpu_freq = psutil.cpu_freq().current
    cpu_percent = psutil.cpu_percent()
    cpu_temp = -1
    if hasattr(psutil, "sensors_temperatures"):
        temps = psutil.sensors_temperatures()
        if "coretemp" in temps:
            core_temps = temps["coretemp"]
            if core_temps:
                cpu_temp = core_temps[0].current

    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    net_io = psutil.net_io_counters()

    warnings = list[str]()
    if cpu_temp == -1:
        warnings.append("CPU temperature not available.")

    return SystemInfo(
        healthy=True,
        errors=[],
        warnings=warnings,
        cpu_count=cpu_count,
        cpu_freq=cpu_freq,
        cpu_percent=cpu_percent,
        cpu_temp=cpu_temp,
        mem_total=mem.total,
        mem_used=mem.used,
        mem_percent=mem.percent,
        swap_total=swap.total,
        swap_used=swap.used,
        swap_percent=swap.percent,
        disk_total=disk.total,
        disk_used=disk.used,
        disk_percent=disk.percent,
        net_io=net_io,
    )


def _get_root_dir() -> str:
    this_dir = os.path.dirname(__file__)
    return os.path.abspath(os.path.join(this_dir, "..", ".."))


def _get_api_version() -> str:
    pyproject_toml = os.path.join(_get_root_dir(), "pyproject.toml")
    with open(pyproject_toml, "r") as f:
        data = f.read()
        project = tomllib.loads(data)
        return project["project"]["version"]


def _get_schema_version() -> str:
    openai_yaml = os.path.join(_get_root_dir(), "app", "schema", "openapi.yaml")
    with open(openai_yaml, "r") as f:
        data = f.read()
        schema = yaml.safe_load(data)
        return schema["info"]["version"]


async def _get_queue_health(request: Request, max_attempts: int = 2) -> QueueInfo:
    broker_ok = False
    broker_error = None
    async with request.app.state.queue_store.tx() as tx:
        try:
            broker_ok = await tx.ping()
        except Exception as e:
            broker_ok = False
            broker_error = str(e)

    all_workers = list[WorkerInfo]()
    healthy = 0
    unhealthy = 0

    # NOTE(jnu): We allow multiple attempts because there is a
    # known issue on Azure where expired connections used during
    # `ping` raise an exception, even though the broker is healthy
    # and the queue is fine.
    #
    # This doesn't seem to be a real issue, so if it works with an
    # automatic retry, we'll consider it healthy.
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        try:
            workers = queue.control.ping()
            inspection = queue.control.inspect()
            stats = inspection.stats()
            report = inspection.report()
            registered = inspection.registered()
            scheduled = inspection.scheduled()
            active = inspection.active()
            revoked = inspection.revoked()

            for worker in workers:
                for key in worker.keys():
                    worker_status = worker[key].get("ok")
                    is_healthy = worker_status == "pong"
                    if is_healthy:
                        healthy += 1
                    else:
                        unhealthy += 1
                    all_workers.append(
                        WorkerInfo(
                            healthy=is_healthy,
                            errors=[worker_status or "unknown error"]
                            if not is_healthy
                            else [],
                            name=key,
                            warnings=[],
                            registered_tasks=registered.get(key, []),
                            report=report.get(key, "").get("ok", ""),
                            uptime=stats.get(key, {}).get("uptime", 0),
                            usage=stats.get(key, {}).get("total", {}),
                            active=active.get(key, []),
                            scheduled=scheduled.get(key, []),
                            revoked=revoked.get(key, []),
                        )
                    )
            # Break out of the retry loop if we successfully inspect workers.
            break
        except Exception as e:
            log.exception("Error inspecting workers: %s", e)
            broker_error = str(e)

    errors = list[str]()
    warnings = list[str]()
    if not broker_ok:
        errors.append(f"Broker error: {broker_error}")
    elif broker_error:
        warnings.append(f"Broker warning: {broker_error}")
    if healthy == 0:
        errors.append("No healthy workers found.")

    return QueueInfo(
        total_workers=len(all_workers),
        healthy_workers=healthy,
        unhealthy_workers=unhealthy,
        broker=config.queue.broker.engine,
        broker_host=config.queue.broker.host,
        workers=all_workers,
        errors=errors,
        warnings=warnings,
        healthy=not errors,
    )


def _get_platform_info() -> PlatformInfo:
    return PlatformInfo(
        py_version=sys.version,
        os=platform.platform(),
        os_version=platform.version(),
        hostname=platform.node(),
        processor=platform.machine(),
    )


def _get_api_health(startup_time: datetime) -> ApiHealth:
    return ApiHealth(
        healthy=True,
        errors=[],
        warnings=[],
        startup_time=startup_time.isoformat(),
        current_time=utcnow().isoformat(),
        api_version=_get_api_version(),
        schema_version=_get_schema_version(),
    )


async def _get_db_health(request: Request) -> DbHealth:
    if not config.experiments.store:
        return DbHealth(
            healthy=True, errors=[], warnings=["No database configured."], engine=""
        )

    engine = config.experiments.store.engine
    async with request.app.state.db.async_session_with_args(
        pool_pre_ping=True
    )() as session:
        try:
            await session.execute(text("SELECT 1"))
            return DbHealth(healthy=True, errors=[], warnings=[], engine=engine)
        except Exception as e:
            return DbHealth(healthy=False, errors=[str(e)], warnings=[], engine=engine)


async def inspect_api(request: Request) -> ApiMeta:
    startup = request.app.state.startup_time
    return ApiMeta(
        platform=_get_platform_info(),
        api=_get_api_health(startup),
        queue=await _get_queue_health(request),
        system=_get_system_info(),
        db=await _get_db_health(request),
    )


def _format_meta_html(content: str) -> str:
    api_version = _get_api_version()
    schema_version = _get_schema_version()
    return f"""
    <html>
        <head>
            <title>Blind Charging API</title>
            <style type="text/css">
            * {{ margin: 0; box-sizing: border-box; }}
            html, body {{ min-height: 100vh; }}
            body {{
                font-family: sans-serif;
                text-align: center;
                color: #333;
                display: flex;
                flex-direction: column;
                justify-content: space-between;
            }}
            header {{
                padding-top: 2rem;
            }}
            header pre {{
                margin-bottom: 0.5rem;
            }}
            .with-sidebar {{
                display: flex;
                flex-direction: row;
                padding: 0 2rem;
            }}
            .sidebar {{
                flex: 1;
                padding: 1rem;
                width: 20%;
                max-width: 25%;
                flex-basis: 25%;
            }}
            .sidebar ul {{
                list-style-type: none;
                padding: 0;
            }}
            .sidebar li {{
                cursor: pointer;
                padding: 0.5rem;
                border-bottom: 1px solid #ccc;
                text-wrap: nowrap;
                overflow-x: hidden;
                text-overflow: ellipsis;
            }}
            .sidebar li:hover {{
                background-color: #f8f9fa;
            }}
            .sidebar li:last-child {{
                border-bottom: none;
            }}
            .sidebar li.active {{
                background-color: #e5fc96;
            }}
            .sidebar li.current {{
                background-color: #fc96e5;
            }}
            .mainbar {{
                text-align: left;
            }}
            .dirty {{
                border: 5px solid #f22;
            }}
            aside {{
                font-size: 0.8rem;
                color: #666;
                padding: 2rem;
                text-align: left;
            }}
            aside p {{
                padding: 0.25rem 0;
                margin: 0;
            }}
            h2, h3 {{ margin: 1rem 0; }}
            input, textarea, button {{
                margin: 0.5rem 0;
            }}
            p {{ margin-bottom: 2rem; }}
            a {{
                color: #007BFF;
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            .yay {{
                color: #28a745;
                font-weight: bold;
            }}
            .nay {{
                color: #dc3545;
                font-weight: bold;
            }}
            .warn {{
                color: #ffc107;
                font-weight: bold;
            }}
            .divider {{
                border: 0;
                height: 1px;
                margin: 2rem 0;
                background-image: linear-gradient(to right,
                    rgba(0, 0, 0, 0),
                    rgba(0, 0, 0, 0.75),
                    rgba(0, 0, 0, 0));
            }}
            .links {{
                width: 50%;
                margin: auto;
            }}
            .links li {{
                text-align: left;
                margin-top: 1rem;
            }}
            .next_steps {{
                color: #666;
                font-style: italic;
            }}
            footer {{
                width: 100%;
                background-color: #f8f9fa;
                padding-bottom: 1rem;
                color: #666;
                font-size: 0.8rem;
                text-align: center;
            }}
            footer div {{
                margin-top: 1rem;
            }}
            .cpl {{
                font-size: 2px;
                letter-spacing: 0.1em;
                color: #666;
            }}
            table td:first-child {{
                text-align: right;
                padding-right: 1rem;
            }}
            .status_grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(max(100%/4), 1fr));
                gap: 1rem;
                margin: 1rem;
                max-width: 100vw;
            }}
            .worker_info {{
                max-width: 100vw;
            }}
            h3, h4 {{
                margin-top: 2rem;
            }}
            </style>
        </head>
        <body>
            <header>
            <pre>{api_logo}</pre>
            <pre>API v{api_version}, Schema v{schema_version}</pre>
            </header>
            <main>{content}</main>
            <footer>
                <div>Need help? Email us at
                <a href="mailto:blind_charging@hks.harvard.edu">
                blind_charging@hks.harvard.edu</a>.
                </div>
                <div>© 2024 Computational Policy Lab, Harvard Kennedy School</div>
                <div><pre class="cpl">{cpl_logo}</pre></div>
            </footer>
        </body>
    </html>
    """


def _bytes_to_gb(b: float) -> float:
    return b / (1024**3)


def _format_worker_info(worker: WorkerInfo) -> str:
    return f"""
    <h4>{worker.name}</h4>
    <table>
        <tr><td>Healthy</td><td>{worker.healthy}</td></tr>
        <tr><td>Errors</td><td>
        <pre>{"\n".join(worker.errors) or "-"}</pre></td></tr>
        <tr><td>Warnings</td><td>
        <pre>{"\n".join(worker.warnings) or "-"}</pre></td></tr>
        <tr><td>Registered Tasks</td><td>
        <pre>{"\n".join(worker.registered_tasks) or "-"}</pre></td></tr>
        <tr><td>Report</td><td>{worker.report}</td></tr>
        <tr><td>Uptime</td><td>{worker.uptime} seconds</td></tr>
        <tr><td>Usage</td><td>
        <pre>{json.dumps(worker.usage, indent=2)}</pre></td></tr>
        <tr><td>Active Tasks</td><td>
        <pre>{json.dumps(worker.active, indent=2)}</pre></td></tr>
        <tr><td>Scheduled Tasks</td><td>
        <pre>{json.dumps(worker.scheduled, indent=2)}</pre></td></tr>
        <tr><td>Revoked Tasks</td><td>
        <pre>{json.dumps(worker.revoked, indent=2)}</pre></td></tr>
    </table>
    """


@meta_router.get("/status")
async def status(request: Request, format: str | None = None):
    health = await inspect_api(request)
    status_code = 200 if health.healthy() else 500
    health_dict = asdict(health)
    # inspect `accept` header to determine default response format
    accept = request.headers.get("accept", "")
    if format is None:
        if "text/html" in accept:
            format = "html"
        elif "application/json" in accept:
            format = "json"

    if format == "json":
        return JSONResponse(content=health_dict, status_code=status_code)
    elif format == "html":
        errors_content = ""
        for err in health.errors():
            if not errors_content:
                errors_content += "<h3 class='nay'>[Errors]</h3>"
            errors_content += f"<p class='nay'>{err}</p>"
        warnings_content = ""
        for warn in health.system.warnings:
            if not warnings_content:
                warnings_content += "<h3 class='warn'>[Warnings]</h3>"
            warnings_content += f"<p class='warn'>{warn}</p>"
        content = _format_meta_html(f"""
            <h2>API Status</h2>
            <p>The service appears to be {
                "<span class='yay'>healthy</span>"
                if health.healthy() else
                "<span class='nay'>unhealthy<span>"
            }.</p>
            {errors_content}
            {warnings_content}
            <hr class="divider" />

            <div class="status_grid">
            <div>
            <h3>API</h3>
            <table>
                <tr><td>API Version</td><td>{health.api.api_version}</td></tr>
                <tr><td>Schema Version</td><td>{health.api.schema_version}</td></tr>
                <tr><td>Startup Time</td><td>{health.api.startup_time}</td></tr>
                <tr><td>Current Time</td><td>{health.api.current_time}</td></tr>
            </table>
            </div>

            <div>
            <h3>DB</h3>
            <table>
                <tr><td>Engine</td>
                <td>{health.db.engine}</td></tr>
                <tr><td>Healthy</td>
                <td>{health.db.healthy}</td></tr>
                <tr><td>Errors</td>
                <td><pre>{"\n".join(health.db.errors) or "-"}</pre></td></tr>
                <tr><td>Warnings</td>
                <td><pre>{"\n".join(health.db.warnings) or "-"}</pre></td></tr>
            </table>
            </div>

            <div>
            <h3>Queue</h3>
            <table>
                <tr><td>Total Workers</td>
                <td>{health.queue.total_workers}</td></tr>
                <tr><td>Healthy Workers</td>
                <td>{health.queue.healthy_workers}</td></tr>
                <tr><td>Unhealthy Workers</td>
                <td>{health.queue.unhealthy_workers}</td></tr>
                <tr><td>Broker</td>
                <td>{health.queue.broker}</td></tr>
                <tr><td>Broker Host</td>
                <td>{health.queue.broker_host}</td></tr>
            </table>
            </div>

            <div>
            <h3>System</h3>
            <table>
                <tr><td>CPU Count</td>
                <td>{health.system.cpu_count}</td></tr>
                <tr><td>CPU Frequency</td>
                <td>{health.system.cpu_freq} MHz</td></tr>
                <tr><td>CPU Usage</td>
                <td>{health.system.cpu_percent}%</td></tr>
                <tr><td>CPU Temperature</td>
                <td>{health.system.cpu_temp}°C</td></tr>
                <tr><td>Memory Total</td>
                <td>{_bytes_to_gb(health.system.mem_total):.2f} GB</td></tr>
                <tr><td>Memory Used</td>
                <td>{_bytes_to_gb(health.system.mem_used):.2f} GB</td></tr>
                <tr><td>Memory Usage</td>
                <td>{health.system.mem_percent}%</td></tr>
                <tr><td>Swap Total</td>
                <td>{_bytes_to_gb(health.system.swap_total):.2f} GB</td></tr>
                <tr><td>Swap Used</td>
                <td>{_bytes_to_gb(health.system.swap_used):.2f} GB</td></tr>
                <tr><td>Swap Usage</td>
                <td>{health.system.swap_percent}%</td></tr>
                <tr><td>Disk Total</td>
                <td>{_bytes_to_gb(health.system.disk_total):.2f} GB</td></tr>
                <tr><td>Disk Used</td>
                <td>{_bytes_to_gb(health.system.disk_used):.2f} GB</td></tr>
                <tr><td>Disk Usage</td>
                <td>{health.system.disk_percent}%</td></tr>
                <tr><td>Network sent</td>
                <td>{_bytes_to_gb(health.system.net_io.bytes_sent):.2f} GB</td></tr>
                <tr><td>Network received</td>
                <td>{_bytes_to_gb(health.system.net_io.bytes_recv):.2f} GB</td></tr>
            </table>
            </div>

            <div>
            <h3>Platform</h3>
            <table>
                <tr><td>Python Version</td><td>{health.platform.py_version}</td></tr>
                <tr><td>OS</td><td>{health.platform.os}</td></tr>
                <tr><td>OS Version</td><td>{health.platform.os_version}</td></tr>
                <tr><td>Hostname</td><td>{health.platform.hostname}</td></tr>
                <tr><td>Processor</td><td>{health.platform.processor}</td></tr>
            </table>
            </div>

            </div>

            <div class="worker_info">

            <div>
            <h3>Workers</h3>
            {"".join([_format_worker_info(worker) for worker in health.queue.workers])}
            </div>

            </div>

            """)
        return HTMLResponse(content=content, status_code=status_code)
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid format `{format}`. Use `json` or `html`."
        )


@meta_router.get("/", response_class=HTMLResponse)
async def root():
    return _format_meta_html("""
            <p>This server hosts the
                <a href="https://policylab.hks.harvard.edu/"
                    rel="noopener noreferrer"
                    target="_blank">Computational Policy Lab's</a>
                <a href="https://blindcharging.org/"
                    rel="noopener noreferrer"
                    target="_blank">Race-Blind Charging API</a>.</p>
            <p>
            Since you are reading this,
            it means the API has basically started correctly.
                <span class="yay">Congratulations!</span></p>
            <hr class="divider" />
            <p class="next_steps">
            Here are some useful links to get you started ...
            </p>
            <ul class="links">
                <li>Our GitHub repository's
                <a href="https://github.com/stanford-policylab/blind-charging-api"
                    rel="noopener noreferrer"
                    target="_blank">readme file</a>
                includes different tests you can run to start working with the API.</li>
                <li>Our
                <a href="/api/v1/docs"
                    rel="noopener noreferrer"
                    target="_blank">API documentation</a>
                has more detailed information about all of the API endpoints.</li>
                <li>The
                <a href="/status"
                    rel="noopener noreferrer">status page</a>
                will give you more details about the current API status.</li>
            </ul>""")


@meta_router.get("/version", response_class=JSONResponse)
async def version():
    return {"api_version": _get_api_version(), "schema_version": _get_schema_version()}


@meta_router.get("/config", response_class=HTMLResponse)
async def edit_config():
    auth = """return "";"""
    if config.authentication.method == "client_credentials":
        auth = """
            if (!window._client_id) {
                window._client_id = window.prompt("Enter your client ID:");
            }
            if (!window._client_secret) {
                window._client_secret = window.prompt("Enter your client secret:");
            }
            const res = await fetch("/api/v1/oauth2/token", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    grant_type: "client_credentials",
                    client_id: window._client_id,
                    client_secret: window._client_secret,
                }),
            });
            const data = await res.json();
            if (!data.access_token) {
                throw new Error(JSON.stringify(data));
            }
            return data.access_token;
        """
    elif config.authentication.method == "preshared":
        auth = """
            if (!window._preshared) {
                window._preshared = window.prompt("Enter your token:");
            }
            return window._preshared;
        """
    return _format_meta_html(f"""
        <script type="text/javascript">
            async function getToken() {{
                {auth}
            }}

            async function callApi(route, method, data) {{
                const token = await getToken();
                const headers = {{}};
                if (data) {{
                    headers["Content-Type"] = "application/json";
                }}
                if (token) {{
                    headers["Authorization"] = `Bearer ${{token}}`;
                }}
                const res = await fetch(route, {{
                    method: method,
                    headers: headers,
                    body: data ? JSON.stringify(data) : undefined,
                }});
                if (res.status >= 300) {{
                    throw new Error(await res.text());
                }}
                return res.json();
            }}

            async function loadLatestConfig() {{
                const config = await callApi("/api/v1/config", "GET");
                if (!config.hasOwnProperty("blob")) {{
                    document.getElementById("warn").innerText = JSON.stringify(config);
                }} else {{
                    document.getElementById("warn").innerText = "Current config:"
                }}
                document.getElementById("config").value = (config.blob || "");
                document.getElementById("name").value = (config.name || "");
                document.getElementById("created").innerText = (config.createdAt || "");
                window._current = config;
            }}

            async function loadAllConfigs() {{
                const {{configs}} = await callApi("/api/v1/configs", "GET");
                if (!configs) {{
                    throw new Error("No past configs found." + JSON.stringify(configs));
                }}
                const list = document.getElementById("past");
                list.innerHTML = "";
                for (const config of configs) {{
                    const item = document.createElement("li");
                    item.innerText = config.name || config.version;
                    item.onclick = async function() {{
                        if (window._dirty) {{
                            if (!window.confirm(
                            "You have unsaved changes. Discard?"
                            )) {{
                                return;
                            }}
                        }}
                        const res = await callApi(
                            "/api/v1/config/" + config.version,
                            "GET");
                        document.getElementById("config").value = res.blob;
                        document.getElementById("name").value = res.name;
                        document.getElementById("created").innerText = res.createdAt;
                        const isActive = res.active ? "Active" : "Inactive";
                        const label = isActive + " config " + config.version;
                        document.getElementById("warn").innerText = label;
                        document.querySelectorAll("li.current").forEach((li) => {{
                            li.classList.remove("current");
                        }});
                        item.classList.add("current");
                        window._current = res;
                        clearDirty();
                    }};
                    if (config.active) {{
                        item.classList.add("active");
                        window._current = config;
                        clearDirty();
                        document.getElementById("config").value = config.blob;
                        document.getElementById("name").value = config.name;
                        document.getElementById("created").innerText = config.createdAt;
                        const label = "Active config " + config.version;
                        document.getElementById("warn").innerText = label;
                    }}
                    list.appendChild(item);
                }}
            }}

            window.onload = async function() {{
                try {{
                    await loadAllConfigs();
                }} catch (e) {{
                    document.getElementById("warn").innerText = e.toString();
                }}
                document.getElementById("warn").innerText = "";
            }};

            async function saveConfig() {{
                const config = document.getElementById("config").value;
                try {{
                    await callApi("/api/v1/config", "POST", {{
                        blob: document.getElementById("config").value,
                        parent: window._current ? window._current.version : undefined,
                        active: true,
                        name: document.getElementById("name").value || undefined,
                    }})
                    document.getElementById("warn").innerText = "Saved!";
                    await loadAllConfigs();
                }} catch (e) {{
                    document.getElementById("warn").innerText = e.toString();
                }}
            }}

            function markDirty() {{
                window._dirty = true;
                document.getElementById("warn").innerText = "Unsaved changes!";
                document.getElementById("created").innerText = "";
                document.getElementById("config").classList.add("dirty");
            }}

            function clearDirty() {{
                window._dirty = false;
                document.getElementById("config").classList.remove("dirty");
            }}
        </script>
        <div class="with-sidebar">
        <div class="sidebar">
            <h3>Past Configs</h3>
            <ul id="past"></ul>
        </div>
        <div class="mainbar">
        <h3>Blind Review Selection Configuration</h3>
        <div>
            <aside>
                <p>Enter the YAML config for randomization in the text area below,
                then press "activate" to roll it out.</p>
                <p>These configs are immutable. When you press "activate" we will
                create a new revision with the above settings, and set it as the
                currently active revision.</p>
                <p>To roll back a config, select a previous version from the side
                panel and press "activate." This will roll out a new copy of the
                old version.</p>
            </aside>
        </div>
        <form>
            <div id="warn" style="color: red;"></div>
            <div><label for="name">Name: </label>
            <input type="text" id="name" name="name" /></div>
            <div>Created: <span id="created"></span></div>
            <div>
                <textarea id="config" name="config" rows="30" cols="80"
                    onkeypress="markDirty()"></textarea>
            </div>
            <div>
                <button type="button" onclick="saveConfig()">Activate</button>
            </div>
        </form>
        </div>
        </div>
        <script type="text/javascript">
            document.getElementById("warn").innerText = "Loading...";
            document.getElementById("config").value = "";
            document.getElementById("name").value = "";
            document.getElementById("created").innerText = "";
        </script>
    """)
