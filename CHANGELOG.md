# Changelog

## 0.2.0 - 2026-04-04

- Move the IPEX backend onto the same public endpoint as native Ollama: `127.0.0.1:11434`.
- Make `start` and `native` wait for the host API with phase-by-phase Rich output.
- Keep plain `ollama list`, `ollama ps`, and `ollama run` as the only model-facing commands.
- Install the control plane as an editable `uv tool`.

## 0.1.0 - 2026-04-03

- Initialize the `ipex` package with a Typer CLI and Rich output.
- Move the IPEX Docker Compose file into the project as the new source of truth.
- Add runtime commands for start, down, restart, native, status, preload, unload, and info.
