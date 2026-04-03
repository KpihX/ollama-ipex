# AGENTS.md — ollama-ipex

> Project context for agents working in this repository.

## Overview

| Field | Value |
|-------|-------|
| Purpose | Switch cleanly between native Ollama and the IPEX Docker backend while keeping the public endpoint on `127.0.0.1:11434` |
| Stack | Python CLI (`typer`, `rich`) + Docker Compose |
| Status | 🟡 In progress |

## Source Of Truth

- Runtime defaults: `src/ipex/config.yaml`
- Runtime loader: `src/ipex/config.py`
- Control plane: `src/ipex/cli.py`
- Container template: `docker-compose.yml`
- User-facing docs: `README.md`

## Rules

- Keep plain `ollama ...` as the only model-facing interface.
- `ollama-ipex` / `ipex` only manage backend switching, warmup, diagnostics, and config.
- Do not reintroduce shell wrappers, proxy layers, or alternate ports.
- Native and IPEX backends must both answer on `127.0.0.1:11434`.
- Keep config centralized in `src/ipex/config.yaml`; do not hardcode runtime values in the CLI.
- Prefer loud runtime diagnostics over silent fallback behavior.

## Maintenance

- Any runtime behavior change must update `README.md` and `CHANGELOG.md`.
- Any config-shape change must update `src/ipex/config.yaml` and `config set` / `config edit` behavior together.
- Keep `make push` as the multi-remote push path.
