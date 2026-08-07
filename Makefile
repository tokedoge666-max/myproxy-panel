.PHONY: sync test lint build check

UV_ENV = UV_PYTHON_INSTALL_DIR=../.runtime/python UV_CACHE_DIR=../.runtime/cache/uv UV_PYTHON_PREFERENCE=only-managed
NPM_ENV = npm_config_cache=../.runtime/cache/npm

sync:
	cd backend && $(UV_ENV) uv sync --all-groups
	cd frontend && $(NPM_ENV) npm ci

test:
	cd backend && $(UV_ENV) uv run pytest ../tests

lint:
	cd backend && $(UV_ENV) uv run ruff check app migrations ../tests
	cd frontend && $(NPM_ENV) npm run lint

build:
	cd frontend && $(NPM_ENV) npm run build

check: test lint build
