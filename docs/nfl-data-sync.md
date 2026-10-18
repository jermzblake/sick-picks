# NFL data seed and sync

Run these by hand. Nothing is scheduled yet.

Local setup is the same as other Django commands: copy `.env.example` to `.env`, start Postgres, and set `ODDS_API_KEY`. `manage.py` uses `config.settings.local`, which loads that file.

## Seed

Create the NFL league, one season, and the 32 catalog teams before syncing:

```sh
uv run python manage.py seed_sports --league NFL --season-year 2026
```

`--league` must be `NFL`. `--season-year` is the season label, not the calendar year of a January game. The 2026 season opens Tuesday, September 8, 2026 Pacific, and runs through the following summer. Seed `2026` again for that season's playoffs. Seed `2027` when the next season opens.

The command is safe to repeat. It updates catalog names, adds the season you pass, and leaves other seasons in place. A provider id already stored on a team is kept. Only `NFL` has a catalog.

## Sync

```sh
uv run python manage.py sync_nfl_data
```

This fetches the current NFL week and writes games and lines onto the league, season, and teams created by `seed_sports`. It does not create the league, season, or team rows.

Repeat runs update games in place and add a line snapshot only when the bookmaker, spread, or prices change. A line already tied to a submitted pick is left as it is.

To sync a different window, pass both bounds. They must be timezone-aware, and `--to` is exclusive:

```sh
python manage.py sync_nfl_data \
  --from 2026-09-08T07:00:00Z \
  --to 2026-09-15T07:00:00Z
```

The command stops before writing if the API key is missing, the request fails, the payload cannot be read, or the NFL league has not been seeded.

The same sync is available in Django Admin. Open Games and choose **Sync NFL data**. Leave both bounds empty for the current week, or set both to sync another window.

A missing season, an unmatched team, or one bad event is printed under `Problems` and that game is skipped. The rest of the slate is still saved. The summary reports games created, updated, unchanged, and skipped, plus lines created.

## Settle

Sync writes final scores. It does not grade picks. Settle the current NFL week after scores are in:

```sh
uv run python manage.py settle_games
```

This grades every final or cancelled game whose kickoff falls in the current week. A final game is scored. A cancelled game voids its picks. Scheduled and in-progress games are left alone. A final game that is missing a score is listed under `Problems`, and the rest of the week still settles. Running it again writes the same results.

To settle a different window, pass both bounds. They must be timezone-aware, and `--to` is exclusive:

```sh
python manage.py settle_games \
  --from 2026-09-08T07:00:00Z \
  --to 2026-09-15T07:00:00Z
```

The same settlement is available in Django Admin. Open Games and choose **Settle games**. Leave both bounds empty for the current week, or set both to settle another window.
