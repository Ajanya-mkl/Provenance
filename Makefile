ifeq ($(OS),Windows_NT)
VENV_PYTHON := .venv/Scripts/python.exe
VENV_CREATE := py -3.11 -m venv .venv
else
VENV_PYTHON := .venv/bin/python
VENV_CREATE := python -m venv .venv
endif

.PHONY: help setup up down logs ps test lint format clean

help:
	@echo "setup  Create .venv and install development dependencies"
	@echo "up     Build and start the local platform"
	@echo "down   Stop the local platform (preserve volumes)"
	@echo "logs   Follow service logs"
	@echo "ps     Show service status"
	@echo "test   Run pytest"
	@echo "lint   Run Ruff checks"
	@echo "format Format Python files with Ruff"
	@echo "clean  Remove local Python build and test artifacts"

setup:
	$(VENV_CREATE)
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt
	$(VENV_PYTHON) -m pip install -e .

up:
	docker compose --env-file .env -f deploy/docker/docker-compose.yml up -d --build

down:
	docker compose --env-file .env -f deploy/docker/docker-compose.yml down

logs:
	docker compose --env-file .env -f deploy/docker/docker-compose.yml logs -f

ps:
	docker compose --env-file .env -f deploy/docker/docker-compose.yml ps

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

clean:
	python -c "import pathlib, shutil; [shutil.rmtree(path, ignore_errors=True) for path in map(pathlib.Path, ['.venv', '.pytest_cache', '.ruff_cache', 'build', 'dist', 'src/provenance.egg-info'])]"
