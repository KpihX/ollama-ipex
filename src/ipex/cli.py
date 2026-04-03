from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

from .config import CONFIG_PATH, PROJECT_ROOT, load_config, load_raw_config, save_config


app = typer.Typer(
    help="Manage the local IPEX-backed Ollama runtime.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
config_app = typer.Typer(help="Inspect or adjust runtime defaults.")
app.add_typer(config_app, name="config")
console = Console()


def _config() -> dict[str, Any]:
    return load_config()


def _compose_path() -> Path:
    return PROJECT_ROOT / _config()["docker"]["compose_filename"]


def _docker_service_name() -> str:
    return _config()["docker"]["service_name"]


def _container_name() -> str:
    return _config()["docker"]["container_name"]


def _native_service_name() -> str:
    return _config()["native"]["service_name"]


def _runtime_host() -> str:
    return _config()["runtime"]["host"]


def _runtime_port() -> int:
    return int(_config()["runtime"]["port"])


def _base_url() -> str:
    return f"http://{_runtime_host()}:{_runtime_port()}"


def _models_dir() -> str:
    return _config()["paths"]["models_dir"]


def _config_editor() -> str:
    for candidate in (os.environ.get("VISUAL"), os.environ.get("EDITOR"), "sensible-editor", "nano"):
        if candidate and shutil.which(candidate):
            return candidate
    return "vi"


def _compose_env() -> dict[str, str]:
    config = _config()
    runtime = config["runtime"]
    docker = config["docker"]
    health = docker["healthcheck"]
    env = {
        "OLLAMA_IPEX_CONTAINER_NAME": docker["container_name"],
        "OLLAMA_IPEX_IMAGE": docker["image"],
        "OLLAMA_IPEX_MODELS_DIR": config["paths"]["models_dir"],
        "OLLAMA_IPEX_MEM_LIMIT": str(docker["mem_limit"]),
        "OLLAMA_IPEX_SHM_SIZE": str(docker["shm_size"]),
        "OLLAMA_IPEX_RESTART": str(docker["restart"]),
        "OLLAMA_IPEX_KEEP_ALIVE": str(runtime["keep_alive"]),
        "OLLAMA_IPEX_NUM_PARALLEL": str(runtime["num_parallel"]),
        "OLLAMA_IPEX_MAX_LOADED_MODELS": str(runtime["max_loaded_models"]),
        "OLLAMA_IPEX_NUM_GPU": str(runtime["num_gpu"]),
        "OLLAMA_IPEX_FLASH_ATTENTION": "1" if runtime["flash_attention"] else "0",
        "OLLAMA_IPEX_OMP_THREADS": str(runtime["omp_threads"]),
        "OLLAMA_IPEX_DRI_RENDER": config["devices"]["dri_render"],
        "OLLAMA_IPEX_DRI_CARD": config["devices"]["dri_card"],
        "OLLAMA_IPEX_HEALTH_INTERVAL": str(health["interval"]),
        "OLLAMA_IPEX_HEALTH_TIMEOUT": str(health["timeout"]),
        "OLLAMA_IPEX_HEALTH_RETRIES": str(health["retries"]),
        "OLLAMA_IPEX_HEALTH_START_PERIOD": str(health["start_period"]),
    }
    for key, value in config["environment"].items():
        env[f"OLLAMA_IPEX_ENV_{key}"] = str(value)
    merged = os.environ.copy()
    merged.update(env)
    return merged


def _run(command: list[str], check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True, env=env)


def _docker_compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["docker", "compose", "-f", str(_compose_path()), *args], check=check, env=_compose_env())


def _systemctl(action: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return _run(["systemctl", action, _native_service_name()], check=check)


def _sudo_systemctl(action: str) -> subprocess.CompletedProcess[str]:
    return _run(["sudo", "systemctl", action, _native_service_name()], check=False)


def _request_systemctl(action: str) -> subprocess.CompletedProcess[str]:
    direct = _systemctl(action, check=False)
    if direct.returncode == 0:
        return direct
    return _sudo_systemctl(action)


def _curl_request(path: str, payload: dict[str, Any] | None = None, timeout_seconds: int = 15) -> tuple[int, dict[str, Any], str]:
    command = [
        "curl",
        "-sS",
        "--connect-timeout",
        "2",
        "--max-time",
        str(timeout_seconds),
        "-w",
        "\n%{http_code}",
    ]
    if payload is not None:
        command.extend(
            [
                "-X",
                "POST",
                "-H",
                "Content-Type: application/json",
                "-d",
                json.dumps(payload),
            ]
        )
    command.append(f"{_base_url()}{path}")
    result = _run(command, check=False)
    stdout = (result.stdout or "").strip()
    body, _, status_text = stdout.rpartition("\n")
    response_body = body if status_text.isdigit() else stdout
    status_code = int(status_text) if status_text.isdigit() else (200 if result.returncode == 0 else 0)
    data: dict[str, Any] = {}
    if response_body:
        try:
            data = json.loads(response_body)
        except json.JSONDecodeError:
            data = {}
    return status_code, data, (result.stderr or "").strip() or response_body


def _curl_json(path: str, payload: dict[str, Any] | None = None, timeout_seconds: int = 15) -> dict[str, Any]:
    status_code, data, detail = _curl_request(path, payload=payload, timeout_seconds=timeout_seconds)
    if status_code == 0 or status_code >= 400:
        message = (
            data.get("error")
            or data.get("message")
            or detail
            or f"Request to {_base_url()}{path} failed with HTTP {status_code or 'unknown'}."
        )
        _fail(str(message))
    return data


def _safe_curl_json(path: str, timeout_seconds: int = 5) -> dict[str, Any] | None:
    status_code, data, _detail = _curl_request(path, timeout_seconds=timeout_seconds)
    if status_code == 0 or status_code >= 400:
        return None
    return data


def _probe_interval_seconds() -> float:
    return float(_config()["runtime"]["probe_interval_seconds"])


def _startup_timeout_seconds() -> int:
    return int(_config()["runtime"]["startup_timeout_seconds"])


def _switch_timeout_seconds() -> int:
    return int(_config()["runtime"]["switch_timeout_seconds"])


def _wait_for_health(timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _safe_curl_json("/api/tags", timeout_seconds=4) is not None:
            return True
        time.sleep(_probe_interval_seconds())
    return False


def _docker_health_state() -> str:
    result = _run(
        [
            "docker",
            "inspect",
            "-f",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            _container_name(),
        ],
        check=False,
    )
    if result.returncode != 0:
        return "missing"
    return result.stdout.strip() or "unknown"


def _phase(title: str) -> None:
    console.print()
    console.print(Rule(title))


def _step(message: str) -> None:
    console.print(f"[cyan]•[/cyan] {message}")


def _ok(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def _warn(message: str) -> None:
    console.print(f"[yellow]![/yellow] {message}")


def _fail(message: str) -> None:
    console.print(f"[red]x[/red] {message}")
    raise typer.Exit(code=1)


def _ensure_native_stopped() -> None:
    _step(f"Stopping native service `{_native_service_name()}` if it is active")
    stop_result = _request_systemctl("stop")
    if stop_result.returncode == 0:
        _ok("Native service stop requested")
        return
    status = _systemctl("is-active", check=False)
    if (status.stdout.strip() or "").lower() in {"inactive", "failed", "unknown"}:
        _ok("Native service already inactive")
        return
    message = stop_result.stderr.strip() or stop_result.stdout.strip() or "Failed to stop native Ollama."
    _fail(message)


def _ensure_ipex_down() -> None:
    _step("Removing any previous IPEX container instance")
    result = _docker_compose("down", "--remove-orphans", check=False)
    if result.returncode == 0:
        _ok("Previous IPEX state cleared")
        return
    message = result.stderr.strip() or result.stdout.strip() or "Failed to stop the IPEX container."
    _fail(message)


def _ensure_ipex_up() -> None:
    _step("Starting the IPEX container on the native Ollama endpoint")
    result = _docker_compose("up", "-d", "--force-recreate", "--remove-orphans", check=False)
    if result.returncode == 0:
        _ok("Docker Compose accepted the new IPEX runtime")
        return
    message = result.stderr.strip() or result.stdout.strip() or "Failed to start the IPEX container."
    _fail(message)


def _wait_for_backend(label: str, timeout_seconds: int) -> None:
    _step(f"Waiting for `{label}` to answer on {_base_url()}")
    deadline = time.time() + timeout_seconds
    last_health = "unknown"
    while time.time() < deadline:
        last_health = _docker_health_state() if label == "ipex" else "n/a"
        if _safe_curl_json("/api/tags", timeout_seconds=4) is not None:
            if label == "ipex":
                _ok(f"IPEX backend is reachable on {_base_url()} (container health: {last_health})")
            else:
                _ok(f"Native backend is reachable on {_base_url()}")
            return
        time.sleep(_probe_interval_seconds())

    if label == "ipex":
        logs = _docker_compose("logs", "--tail=80", _docker_service_name(), check=False)
        snippet = logs.stdout.strip() or logs.stderr.strip() or "<no logs>"
        _fail(
            "IPEX container is up but the Ollama API is still unreachable on the host.\n\n"
            f"Host: {_base_url()}\n"
            f"Container health: {last_health}\n\n"
            "Recent logs:\n"
            f"{snippet}"
        )
    _fail(f"Native Ollama did not become reachable on {_base_url()} in time.")


def _timeout_for_model(model: str, timeout_seconds: int | None) -> int:
    if timeout_seconds is not None:
        return timeout_seconds
    preload = _config()["preload"]
    for prefix in preload.get("large_model_prefixes", []):
        if model.startswith(prefix):
            return int(preload["large_timeout_seconds"])
    return int(preload["default_timeout_seconds"])


def _available_model_names() -> list[str]:
    payload = _safe_curl_json("/api/tags", timeout_seconds=5) or {}
    return [item.get("name", "") for item in payload.get("models", []) if item.get("name")]


def _loaded_model_names() -> list[str]:
    payload = _safe_curl_json("/api/ps", timeout_seconds=5) or {}
    return [item.get("name", "") for item in payload.get("models", []) if item.get("name")]


def _resolve_model_name(model: str | None) -> str:
    model_name = model or _config()["runtime"]["default_model"]
    available = _available_model_names()
    if not available:
        return model_name
    if model_name in available:
        return model_name
    if ":" not in model_name:
        latest = f"{model_name}:latest"
        if latest in available:
            return latest
    for candidate in available:
        if candidate.split(":", 1)[0] == model_name:
            return candidate
    _fail(
        f"Model `{model_name}` is not available on the current backend.\n"
        f"Available models: {', '.join(available) or 'none'}"
    )
    return model_name


def _status_rows() -> list[tuple[str, str]]:
    config = _config()
    rows: list[tuple[str, str]] = []
    compose = _docker_compose("ps", "--format", "json", check=False)
    if compose.stdout.strip():
        try:
            items = json.loads(f"[{','.join(line for line in compose.stdout.splitlines() if line.strip())}]")
        except json.JSONDecodeError:
            items = []
        if items:
            for item in items:
                rows.append((f"docker:{item.get('Service', '?')}", item.get("State", "?")))
        else:
            rows.append(("docker", "unknown"))
    else:
        rows.append(("docker", "down"))

    native = _systemctl("is-active", check=False)
    rows.append(("native", native.stdout.strip() or native.stderr.strip() or "unknown"))
    models = _safe_curl_json("/api/ps", timeout_seconds=5)
    if models is None:
        rows.append(("api", "unreachable"))
        rows.append(("loaded_models", "none"))
    else:
        loaded = ", ".join(model.get("name", "?") for model in models.get("models", [])) or "none"
        rows.append(("api", "healthy"))
        rows.append(("loaded_models", loaded))

    rows.append(("host", _base_url()))
    rows.append(("project_root", str(PROJECT_ROOT)))
    rows.append(("compose", str(_compose_path())))
    rows.append(("models_dir", config["paths"]["models_dir"]))
    rows.append(("default_model", config["runtime"]["default_model"]))
    rows.append(("keep_alive", str(config["runtime"]["keep_alive"])))
    rows.append(("num_parallel", str(config["runtime"]["num_parallel"])))
    rows.append(("max_loaded_models", str(config["runtime"]["max_loaded_models"])))
    rows.append(("startup_timeout_seconds", str(config["runtime"]["startup_timeout_seconds"])))
    rows.append(("switch_timeout_seconds", str(config["runtime"]["switch_timeout_seconds"])))
    rows.append(("mem_limit", str(config["docker"]["mem_limit"])))
    rows.append(("shm_size", str(config["docker"]["shm_size"])))
    return rows


def _set_nested_value(config: dict[str, Any], dotted_key: str, raw_value: str) -> None:
    cursor = config
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            _fail(f"Unknown config path: {dotted_key}")
        cursor = cursor[part]
    leaf = parts[-1]
    if leaf not in cursor:
        _fail(f"Unknown config path: {dotted_key}")
    current = cursor[leaf]
    if isinstance(current, bool):
        normalized = raw_value.strip().lower()
        if normalized not in {"true", "false", "1", "0", "yes", "no"}:
            _fail(f"Expected a boolean for {dotted_key}")
        cursor[leaf] = normalized in {"true", "1", "yes"}
    elif isinstance(current, int):
        cursor[leaf] = int(raw_value)
    else:
        cursor[leaf] = raw_value


@app.command()
def start(
    wait: bool = typer.Option(True, help="Wait until the Ollama API is healthy."),
    preload_model: str | None = typer.Option(None, "--preload", help="Warm a model right after startup."),
) -> None:
    """Start the IPEX container on the native Ollama endpoint."""
    _phase("IPEX Startup")
    _ensure_native_stopped()
    _ensure_ipex_down()
    _ensure_ipex_up()
    if wait:
        _wait_for_backend("ipex", timeout_seconds=_startup_timeout_seconds())
    console.print()
    console.print(f"[green]IPEX Ollama is ready[/green] at {_base_url()}")
    console.print("[dim]Use plain `ollama ...` commands exactly as you would with native Ollama.[/dim]")
    if preload_model:
        preload(model=preload_model)


@app.command()
def down() -> None:
    """Stop the IPEX container and the native Ollama service."""
    _phase("Stop All Backends")
    _ensure_ipex_down()
    _ensure_native_stopped()
    console.print()
    console.print("[yellow]All Ollama backends are stopped.[/yellow]")


@app.command()
def restart(wait: bool = typer.Option(True, help="Wait until the API is healthy again.")) -> None:
    """Restart the IPEX container."""
    _phase("IPEX Restart")
    _ensure_ipex_up()
    if wait:
        _wait_for_backend("ipex", timeout_seconds=_startup_timeout_seconds())
    console.print()
    console.print(f"[green]IPEX Ollama restarted[/green] at {_base_url()}")


@app.command()
def native() -> None:
    """Stop the IPEX container and switch back to native Ollama."""
    _phase("Native Switch")
    _ensure_ipex_down()
    _step(f"Starting native service `{_native_service_name()}`")
    result = _request_systemctl("start")
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Failed to start native Ollama."
        _fail(message)
    _ok("Native service start requested")
    _wait_for_backend("native", timeout_seconds=_switch_timeout_seconds())
    console.print()
    console.print(f"[green]Native Ollama is ready[/green] at {_base_url()}")
    console.print("[dim]Use plain `ollama ...` commands exactly as before.[/dim]")


@app.command()
def status() -> None:
    """Display the current backend state and runtime defaults."""
    probe = _safe_curl_json("/api/tags", timeout_seconds=5)
    table = Table(title="IPEX Ollama Status")
    table.add_column("Component")
    table.add_column("State")
    for label, value in _status_rows():
        table.add_row(label, value)
    table.add_row("host_probe", "ok" if probe is not None else "failed")
    console.print(table)


@app.command()
def unload(
    model: str = typer.Argument(None, help="Model to evict from memory."),
    unload_all: bool = typer.Option(False, "--all", help="Evict every currently loaded model."),
) -> None:
    """Request immediate model eviction from Ollama."""
    if unload_all:
        loaded = _loaded_model_names()
        if not loaded:
            console.print("[yellow]No loaded models to unload.[/yellow]")
            return
        for model_name in loaded:
            _curl_json(
                "/api/generate",
                payload={
                    "model": model_name,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": 0,
                },
            )
            _ok(f"Unload requested for {model_name}")
        return
    model_name = _resolve_model_name(model)
    _curl_json(
        "/api/generate",
        payload={
            "model": model_name,
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        },
    )
    console.print(f"[green]{model_name} unload requested[/green]")


@app.command()
def preload(
    model: str = typer.Argument(None, help="Model to warm into memory."),
    use_default: bool = typer.Option(False, "--default", help="Warm the configured default model."),
    timeout_seconds: int | None = typer.Option(None, "--timeout", help="Override the preload timeout in seconds."),
) -> None:
    """Warm a model with a 1-token request."""
    requested_model = None if use_default else model
    model_name = _resolve_model_name(requested_model)
    resolved_timeout = _timeout_for_model(model_name, timeout_seconds)
    started_at = time.perf_counter()
    result = _curl_json(
        "/api/generate",
        payload={
            "model": model_name,
            "prompt": "hi",
            "stream": False,
            "options": {"num_predict": 1},
        },
        timeout_seconds=resolved_timeout,
    )
    load_duration = result.get("load_duration")
    duration = f"{load_duration / 1_000_000_000:.2f}s" if isinstance(load_duration, int) else f"{time.perf_counter() - started_at:.2f}s"
    console.print(f"[green]{model_name} warmed[/green] in {duration}")


@app.command()
def logs(lines: int = typer.Option(120, "--lines", min=1, help="Number of recent container log lines.")) -> None:
    """Show recent container logs."""
    result = _docker_compose("logs", f"--tail={lines}", _docker_service_name(), check=False)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Unable to read container logs."
        _fail(message)
    console.print(Syntax(result.stdout.strip() or "<no logs>", "text", theme="ansi_dark"))


@app.command()
def doctor() -> None:
    """Run a compact runtime health check."""
    table = Table(title="IPEX Ollama Doctor")
    table.add_column("Check")
    table.add_column("State")
    table.add_column("Details")

    config = _config()
    table.add_row("config", "ok", str(CONFIG_PATH))
    table.add_row("docker_cli", "ok" if shutil.which("docker") else "missing", shutil.which("docker") or "not found")
    table.add_row("ollama_cli", "ok" if shutil.which("ollama") else "missing", shutil.which("ollama") or "not found")

    render_ok = Path(config["devices"]["dri_render"]).exists()
    card_ok = Path(config["devices"]["dri_card"]).exists()
    table.add_row("gpu_render", "ok" if render_ok else "missing", config["devices"]["dri_render"])
    table.add_row("gpu_card", "ok" if card_ok else "missing", config["devices"]["dri_card"])

    native = _systemctl("is-active", check=False)
    table.add_row("native_service", native.stdout.strip() or "unknown", _native_service_name())

    compose = _docker_compose("ps", "--format", "json", check=False)
    docker_state = "down"
    if compose.stdout.strip():
        docker_state = "running"
    table.add_row("ipex_container", docker_state, _container_name())

    probe = _safe_curl_json("/api/tags", timeout_seconds=5)
    table.add_row("host_api", "ok" if probe is not None else "failed", _base_url())
    table.add_row("models_visible", str(len(_available_model_names())), ", ".join(_available_model_names()) or "none")
    table.add_row("loaded_models", str(len(_loaded_model_names())), ", ".join(_loaded_model_names()) or "none")
    console.print(table)


@config_app.command("show")
def config_show() -> None:
    """Print the current config file."""
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        console.print(Syntax(handle.read(), "yaml", theme="ansi_dark"))


@config_app.command("edit")
def config_edit() -> None:
    """Open the live YAML config in the default editor."""
    editor = _config_editor()
    console.print(f"[cyan]•[/cyan] Opening {CONFIG_PATH} with `{editor}`")
    result = subprocess.run([editor, str(CONFIG_PATH)], check=False)
    if result.returncode != 0:
        _fail(f"Editor `{editor}` exited with status {result.returncode}.")
    load_raw_config.cache_clear()
    load_config.cache_clear()
    console.print(f"[green]✓[/green] Saved {CONFIG_PATH}")


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Update a single config key using dotted paths."""
    config = load_raw_config()
    _set_nested_value(config, key, value)
    save_config(config)
    console.print(f"[green]Updated[/green] {key} = {value}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
