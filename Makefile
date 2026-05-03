.PHONY: fmt lint test install install-core install-dev install-gateway install-full schema-check changelog build-ui

fmt:
	uv run pre-commit run --all-files

lint:
	uv run ruff check .

test:
	uv run pytest

install: install-dev

install-core:
	uv sync

install-dev:
	uv sync --group dev
	uv run pre-commit install

install-gateway:
	uv sync --extra gateway

install-full:
	cd ui && npm install && npm run build
	uv sync --all-extras --group dev
	uv run pre-commit install

schema-check:
	uv run python scripts/check_config_schema.py check

changelog:
	python3 hooks/changelog.py .

build-ui:
	cd ui && npm install && npm run build
