#!/usr/bin/env bash
set -o errexit

# Install production dependencies from the committed lockfile
uv sync --frozen --no-dev

# Collect static assets for WhiteNoise
uv run --no-sync python manage.py collectstatic --noinput
