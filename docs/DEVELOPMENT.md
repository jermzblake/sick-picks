# Development

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (fetches Python 3.14 from `.python-version`)
- Docker (Postgres via Compose)
- [Lefthook](https://lefthook.dev/) once (`brew install lefthook`)

## Getting Started

1. Copy the example environment file and set a real `POSTGRES_PASSWORD` (and `ODDS_API_KEY` if you will sync data):

```sh
cp .env.example .env
```

2. Start Postgres:

```sh
docker compose up -d
```

3. Install dependencies from the lockfile:

```sh
uv sync --locked
```

4. Enable git hooks:

```sh
make hooks
```

5. Apply migrations:

```sh
uv run python manage.py migrate
```

6. (Optional) Create an admin user:

```sh
uv run python manage.py createsuperuser
```

7. Start the development server:

```sh
uv run python manage.py runserver
```

The app is at [http://127.0.0.1:8000/](http://127.0.0.1:8000/). Local Django commands read `DATABASE_URL` from `.env`. Keep the same `.env` and Postgres container running for tests and `make check`.

## Quality gates

- **pre-commit** (Lefthook): formats and lints staged Python files
- **pre-push**: runs `make check` (lint, mypy, Django checks, migration check, pytest)
- **CI** (GitHub Actions): installs from `uv.lock` and runs `make ci`, which adds `ruff format --check`

Mirror CI locally with:

```sh
make ci
```

## NFL data

Seed the league/teams, sync games and lines, and settle picks with the commands in [nfl-data-sync.md](nfl-data-sync.md). Set `ODDS_API_KEY` in `.env` before syncing.

## Further reading

- [DOMAIN.md](DOMAIN.md) — product scope, entities, scoring, lifecycles
- [model-design.md](model-design.md) — models and constraints
- [odds-api-mapping.md](odds-api-mapping.md) — The Odds API → domain mapping
- [infrasturcture_architecture_v0.mmd](infrasturcture_architecture_v0.mmd) — deployment topology
