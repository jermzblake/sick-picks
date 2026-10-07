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

Local Django commands read `DATABASE_URL` from `.env`. Copy `.env.example` and start Postgres with `docker compose up -d` before tests or `make check`.
