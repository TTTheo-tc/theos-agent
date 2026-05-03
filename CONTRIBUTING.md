# Contributing to TheOS

Thank you for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/TTTheo-tc/theos-agent
cd theos-agent
make install    # uv sync --group dev && pre-commit install
make test       # uv run pytest
make lint       # uv run ruff check .
make fmt        # uv run pre-commit run --all-files
```

## Before Submitting a PR

1. Run `make fmt && make lint && make test`
2. Follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`
3. Keep changes focused — one logical change per PR
4. Add tests for new functionality
5. Update documentation if behavior changes

## Code Style

- Python 3.11+, line length 100 (black + ruff)
- `from __future__ import annotations` always first
- `X | None` not `Optional[X]`
- Tools return error strings, never raise
- See `STYLE.md` for full conventions

## Architecture

Start with `BOT.md` for development rules and document routing, then use
`docs/index.md` and `docs/core/runtime.md` for internal architecture detail.

## Reporting Issues

- **Bugs**: Use the [bug report template](https://github.com/TTTheo-tc/theos-agent/issues/new?template=bug_report.yml)
- **Features**: Use the [feature request template](https://github.com/TTTheo-tc/theos-agent/issues/new?template=feature_request.yml)
- **Security**: See [SECURITY.md](SECURITY.md)
