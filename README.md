# sick-picks

## Development

Install dependencies from the lockfile:

```sh
uv sync --locked
```

Install [Lefthook](https://lefthook.dev/) once (`brew install lefthook`), then enable the git hooks:

```sh
make hooks
```

`pre-commit` formats and lints staged Python files. `pre-push` runs `make check` (lint, mypy, Django checks, migration check, and pytest) against the local environment. GitHub Actions installs from `uv.lock` and runs `make ci`, which adds `ruff format --check`.

Local Django commands read `DATABASE_URL` from `.env`. and run the development server:

1. Copy the example environment file

```sh
cp .env.example .env
```

2. Start Postgres

```sh
docker compose up -d
```

3. Install dependencies if needed

```sh
uv sync --locked
```

4. Apply migrations

```sh
uv run python manage.py migrate
```

5. Start server

```sh
uv run python manage.py runserver
```

The app is at [http://127.0.0.1:8000/](http://127.0.0.1:8000/). Use the same `.env` and Postgres container before tests or `make check`.

Seed and sync NFL games with the commands in [docs/nfl-data-sync.md](docs/nfl-data-sync.md). Set `ODDS_API_KEY` in `.env` before syncing.
