.PHONY: install ingest test lint fmt audit

install:
	uv sync

# Pull fresh raw snapshots from every source into data/raw/<source>/<YYYY-MM-DD>/.
ingest:
	uv run rxradar ingest all

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .
