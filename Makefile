SHELL := /bin/bash
export DATABASE_URL ?= postgresql://postgres@127.0.0.1:55432/moveai
export PYTHONPATH := $(CURDIR)

.PHONY: db-up migrate reset seed test lint api worker demo openapi ui ui-test

db-up:            ## start local dev postgres (prints URL)
	@./scripts/dev_pg.sh

migrate: db-up    ## apply db/migrations additively
	@uv run python scripts/migrate.py

reset:            ## drop + recreate schema (dev only)
	@uv run python scripts/migrate.py --reset

seed: migrate     ## load fixtures + content packs
	@uv run python scripts/seed.py

test: db-up       ## run python test suite against a dedicated <db>_test database (reset each run)
	@uv run pytest -q

lint:
	@uv run ruff check . && uv run ruff format --check . && uv run mypy packages services adapters

api:              ## run API on loopback
	@uv run uvicorn moveai_api.main:app --host 127.0.0.1 --port 8000 --reload

worker:           ## run ingestion worker
	@uv run python -m moveai_ingestion.worker

demo: migrate     ## end-to-end acceptance demonstration (installs the non-clinical demo pack)
	@uv run python scripts/seed.py --with-demo-pack
	@uv run python scripts/demo.py

openapi:          ## export OpenAPI + regenerate TS client
	@uv run python scripts/export_openapi.py && pnpm -C apps/reviewer gen:api

ui:
	@pnpm -C apps/reviewer dev

ui-test:
	@pnpm -C apps/reviewer test
