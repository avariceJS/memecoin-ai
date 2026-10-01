PYTHON ?= python3
VENV := .venv/bin

.PHONY: setup check test smoke build
setup:
	$(PYTHON) -m venv .venv
	$(VENV)/python -m pip install -r requirements-dev.lock -e .

check:
	$(VENV)/ruff check src tests tools
	$(VENV)/ruff format --check src tests tools
	$(VENV)/mypy src
	$(VENV)/pytest -q

test:
	$(VENV)/pytest -q

smoke:
	$(VENV)/python tools/smoke.py

build:
	$(VENV)/python -m build
