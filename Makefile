PYTHON ?= python3
VENV := .venv/bin

.PHONY: setup check test smoke build engineering-release-check
setup:
	$(PYTHON) -m venv .venv
	$(VENV)/python -m pip install -r requirements-dev.lock -e .

check:
	$(VENV)/ruff check src tests tools
	$(VENV)/ruff format --check src tests tools
	$(VENV)/mypy src/memecoin_ai src/memecoin_registry src/memecoin_research src/memecoin_runner/contracts.py src/memecoin_runner/ledger.py src/memecoin_runner/scoring.py src/memecoin_sampling src/memecoin_collector src/memecoin_operational
	$(VENV)/python -m pytest -q
	$(MAKE) cc-python-check

test:
	$(VENV)/python -m pytest -q

smoke:
	$(VENV)/python tools/smoke.py

build:
	$(VENV)/python tools/build_distribution.py

# Explicit controller release gate; the default Brain package checks do not include engineering/.
engineering-release-check:
	$(VENV)/ruff check engineering
	$(VENV)/ruff format --check engineering
	$(VENV)/python -m pytest engineering/tests -q

# Optional ML environment: Python 3.13, isolated from the frozen D43 collector.
MLVENV := .venv-ml/bin
.PHONY: ml-setup ml-check test-ml
ml-setup:
	$(PYTHON) -m venv .venv-ml
	$(MLVENV)/python -m pip install -r requirements-dev.lock -r requirements-ml.lock -e .

test-ml:
	$(MLVENV)/python -m pytest -q

ml-check:
	$(MLVENV)/ruff check src/memecoin_ml tests/ml tools/ml.py tools/ml_smoke.py
	$(MLVENV)/ruff format --check src/memecoin_ml tests/ml tools/ml.py tools/ml_smoke.py
	$(MLVENV)/mypy --python-version 3.13 src/memecoin_ml
	$(MLVENV)/python -m pytest tests/ml -q

.PHONY: runner-check
runner-check:
	$(MLVENV)/mypy --python-version 3.13 src/memecoin_runner
	$(MLVENV)/python -m pytest tests/runner -q

# Read-only Observatory checks; frontend dependencies stay separate from the stdlib runtime.
.PHONY: cc-python-check cc-web-check
cc-python-check:
	$(VENV)/ruff check apps/control-center/adapter apps/control-center/scripts apps/control-center/tests
	$(VENV)/ruff format --check apps/control-center/adapter apps/control-center/scripts apps/control-center/tests
	$(VENV)/python -m unittest discover -s apps/control-center/tests -q
	$(VENV)/python apps/control-center/adapter/export_snapshot.py --check

cc-web-check:
	npm --prefix apps/control-center/web run check
	npm --prefix apps/control-center/web test
	npm --prefix apps/control-center/web run build
