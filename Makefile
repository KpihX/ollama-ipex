.DEFAULT_GOAL := help
.PHONY: help status install-editable check build push log

PY := .venv/bin/python

help:
	@echo "Targets: status install-editable check build push log"

status:
	$(PY) -m ipex.cli status

install-editable:
	uv tool install --editable . --force

check:
	$(PY) -m py_compile src/ipex/*.py

build:
	uv build

push:
	@branch="$$(git branch --show-current)"; \
	for remote in $$(git remote); do git push "$$remote" "$$branch"; done

log:
	git log --oneline --decorate -n 15
