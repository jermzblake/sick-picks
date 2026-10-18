# Odds API mapping

The NFL client in `sports/providers/the_odds_api/` is the only place that knows Odds API field names. Models and `sync_nfl_data` consume the normalized structures below, or the Django fields those structures line up with. `seed_sports` creates the league, season, and teams. Sync links provider ids onto those teams and upserts games and lines. It does not create a league, season, or team.

Requests use `https://api.the-odds-api.com` and `oddsFormat=decimal`, because `GameLine` moneylines are decimal prices greater than 1 and moneyline scoring is `(price - 1) * 100`.

## League, season, and teams

| Domain | Where it comes from |
| --- | --- |
| League | Seeded by `seed_sports`. The client only calls `americanfootball_nfl`, which corresponds to `League.abbreviation` `NFL`. |
| Team | Seeded by `seed_sports` with name and abbreviation. Sync matches `full_name`, then stores participants `id` on `Team.external_id`. |
| Season | Seeded by `seed_sports --season-year`. The 2026 season opens Tuesday, September 8, 2026 Pacific. Later seasons open 52 weeks after the previous opening. A January kickoff stays in that season. Week numbers use a separate Tuesday calendar and are not the season boundary. |

`NormalizedTeam` is `external_id` plus `name`. Abbreviation comes from the seed catalog, not the provider.

## Games and lines

| Provider field | Normalized field | Model field |
| --- | --- | --- |
| odds or scores `id` | `NormalizedGame.external_id` | `Game.external_id` |
| participants `id` matched on `full_name` | `home_team_external_id`, `away_team_external_id` | `Team.external_id` |
| `commence_time` | `kickoff_at` | `Game.kickoff_at` |
| derived from `commence_time` | `week` | `Game.week` |
| scores `completed` and `scores` | `status`, `home_score`, `away_score` | `Game.status`, `Game.home_score`, `Game.away_score` |
| constant `the_odds_api` | `NormalizedGameLine.source` | `GameLine.source` |
| chosen bookmaker `key` | `bookmaker` | `GameLine.bookmaker` |
| spreads `point` | `home_spread`, `away_spread` | `GameLine.home_spread`, `GameLine.away_spread` |
| spreads `price` | `home_spread_price`, `away_spread_price` | `GameLine.home_spread_price`, `GameLine.away_spread_price` |
| h2h `price` | `home_moneyline`, `away_moneyline` | `GameLine.home_moneyline`, `GameLine.away_moneyline` |
| totals `point` | `game_total` | `GameLine.game_total` |
| totals `price` | `over_price`, `under_price` | `GameLine.over_price`, `GameLine.under_price` |
| bookmaker `last_update` | `captured_at` | `GameLine.captured_at` |

Spread and moneyline outcomes are matched to home and away by team name. A total is matched by the outcome names `Over` and `Under`. Prices quantize to three decimal places. Spread points and the total quantize to one decimal place.

A total is kept only when both prices are greater than 1 and both points quantize to the same number greater than 0. A missing total, a one-sided total, mismatched points, or a non-positive total is stored as null. That does not drop the spread or the moneyline.

## Week

NFL weeks are half-open intervals of seven days starting Tuesday at midnight `America/Los_Angeles`. Week 6 starts Tuesday, October 13, 2026. Week 7 starts Tuesday, October 20, 2026. A Monday night kickoff stays in the week that started the previous Tuesday.

The default odds request uses the week that contains the request time. Callers can pass `commence_time_from` and `commence_time_to` instead. Those bounds are sent as `commenceTimeFrom` and `commenceTimeTo`. Because the API treats `commenceTimeTo` as inclusive, the client sends one second before the exclusive end. Games are still filtered locally with a half-open window.

## One bookmaker

The odds request asks for `h2h`, `spreads`, and `totals`, and only for `fanduel`, `draftkings`, and `betmgm`. For each game the client keeps the first of those books that has both a complete moneyline and a complete spread:

1. FanDuel
2. DraftKings
3. BetMGM

A market is complete when both teams have a price greater than 1 after quantization. A spread also needs opposite points. If none of the three books qualify, the game has no line. Markets from two books are not combined. A missing or unusable total does not disqualify a book and does not cause a fallback to the next book. The total, when kept, comes from that same book.

## Status

| Feed | Normalized status |
| --- | --- |
| No score event, or `completed: false` and no scores | `scheduled` |
| `completed: false` with both team scores | `in_progress` |
| `completed: true` with both team scores | `final` |

`postponed`, `delayed`, and `cancelled` are not derived from this API. Those statuses are an admin concern.

The scores endpoint cannot filter by commence time. The client requests `daysFrom=3` and then keeps games inside the week window. One scores call cannot return every final from a full week.

## Representative fixture

`sports/providers/the_odds_api/fixtures/nfl_week.json` is an optional local capture of a trimmed live response: the fetched week window, the participants for the saved games, up to two odds events limited to the three priority bookmakers, and score events for those same games when the feed included them. It is gitignored because the slate changes with each request. When that file is present, `test_saved_nfl_fixture_normalizes_without_provider_field_names` normalizes it without calling the network. The rest of the client tests do not need it.
