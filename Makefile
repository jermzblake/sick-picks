FILES ?=

.PHONY: format lint format-check typecheck django-check migrations-check test check ci hooks

format:
	uv run ruff format $(or $(FILES),.)

lint:
	uv run ruff check $(or $(FILES),.)

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy .

django-check:
	uv run python manage.py check

migrations-check:
	uv run python manage.py makemigrations --check --dry-run

test:
	uv run pytest

check: lint typecheck django-check migrations-check test

ci: check format-check

hooks:
	lefthook install
