.PHONY: sync api web test lint build schemas schema-check benchmark

sync:
	uv sync
	pnpm install

api:
	uv run uvicorn apps.api.main:create_app --factory --host 127.0.0.1 --port 8000 --reload

web:
	pnpm dev

test:
	uv run pytest
	pnpm test

lint:
	uv run ruff check .
	uv run mypy
	pnpm lint

build:
	pnpm build

schemas:
	uv run python scripts/export_schemas.py
	uv run python -m scripts.export_openapi
	pnpm types

schema-check:
	uv run python scripts/export_schemas.py --check
	uv run python -m scripts.export_openapi --check

benchmark:
	uv run python -m scripts.benchmark_core
