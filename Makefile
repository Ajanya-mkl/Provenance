ifeq ($(OS),Windows_NT)
VENV_PYTHON := .venv/Scripts/python.exe
VENV_CREATE := py -3.12 -m venv .venv
else
VENV_PYTHON := .venv/bin/python
VENV_CREATE := python3.12 -m venv .venv
endif

.PHONY: help setup test lint clean

help:
	@echo "setup  Create .venv and install development dependencies"
	@echo "test   Run pytest (integration tests deselected)"
	@echo "lint   Run Ruff checks"
	@echo "clean  Remove local Python build and test artifacts"

setup:
	$(VENV_CREATE)
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt
	$(VENV_PYTHON) -m pip install -e .

test:
	$(VENV_PYTHON) -m pytest -q

lint:
	$(VENV_PYTHON) -m ruff check .

clean:
	python -c "import pathlib, shutil; [shutil.rmtree(path, ignore_errors=True) for path in map(pathlib.Path, ['.venv', '.pytest_cache', '.ruff_cache', 'build', 'dist', 'src/provenance.egg-info'])]"
