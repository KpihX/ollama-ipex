# ollama-ipex

Control plane for the IPEX-backed Ollama runtime.

## Goal

- Keep the same public endpoint as native Ollama: `http://127.0.0.1:11434`
- Switch cleanly between native systemd Ollama and the IPEX Docker backend
- Keep existing scripts using plain `ollama` unchanged
- Replace shell helpers with an installable `uv` tool
- Do not re-wrap native `ollama` commands like `list`, `ps`, or `run`

## Source Of Truth

- `AGENTS.md`: project-level agent contract
- `src/ipex/config.yaml`: runtime defaults
- `docker-compose.yml`: generic container template fed by the CLI config
- `src/ipex/cli.py`: Typer/Rich control plane

## Install

```bash
uv tool install --editable /home/kpihx/Work/AI/ollama/ipex
```

This exposes:
- `ollama-ipex`
- `ipex`

## Usage

```bash
ollama-ipex status
ollama-ipex start
ollama list
ollama ps
ollama run phi4-mini:latest
ollama-ipex preload --default
ollama-ipex preload qwen2.5:latest
ollama-ipex unload --all
ollama-ipex doctor
ollama-ipex logs --lines 200
ollama-ipex native
ollama-ipex down
```

After `ollama-ipex start`, use plain `ollama ...` commands.
After `ollama-ipex native`, use the same plain `ollama ...` commands.

`ollama-ipex start` is intentionally verbose:
- stop native Ollama if needed
- recreate the Docker backend
- wait for `http://127.0.0.1:11434` to answer on the host
- surface recent container logs immediately if the host probe never becomes healthy

## Config

```bash
ollama-ipex config show
ollama-ipex config edit
ollama-ipex config set docker.mem_limit 20g
ollama-ipex config set docker.shm_size 6g
ollama-ipex config set runtime.default_model qwen2.5:latest
```

`config edit` opens the live YAML file with `VISUAL`, then `EDITOR`, then `sensible-editor`, then `nano`, then `vi`.

## Build

```bash
uv build
```
