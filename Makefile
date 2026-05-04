.PHONY: fmt lint test install install-core install-dev install-gateway install-full smoke-core smoke-core-wheel schema-check changelog build-ui build-bridge build-core build-full

fmt:
	uv run pre-commit run --all-files

lint:
	uv run ruff check .

test:
	uv run pytest

install: install-core

install-core:
	uv sync

install-dev:
	uv sync --group dev
	uv run pre-commit install

install-gateway:
	uv sync --extra gateway

install-full:
	$(MAKE) build-ui
	$(MAKE) build-bridge
	uv sync --all-extras --group dev
	uv run pre-commit install

smoke-core:
	uv run python scripts/smoke_core_runtime.py

smoke-core-wheel: build-core
	uv run python scripts/smoke_core_wheel.py

schema-check:
	uv run python scripts/check_config_schema.py check

changelog:
	python3 hooks/changelog.py .

build-ui:
	cd ui && npm install && npm run build

build-bridge:
	cd bridge && npm install && npm run build

build-core:
	uv build --wheel

build-full: build-ui build-bridge
	uv build
