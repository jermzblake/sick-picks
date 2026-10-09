# Sick Picks — Domain Design

## 1. Domain Purpose

Enable users to make predictions on sports games in relation to betting lines. The system records the user's submitted prediction, preserves the betting terms associated with that prediction, evaluates the prediction once the game is complete, and maintains a historical record of picks and scoring.

The MVP supports NFL games. The core `Team` and `Game` concepts should remain sport-neutral so that other leagues can be introduced later without requiring an NFL-specific domain model.

### MVP scope

- NFL
- Moneyline (outright) picks
- Against-the-spread (ATS) picks
- Immutable historical game-line snapshots
- Pick grading after a game is final
- Historical pick and scoring records
- No pools or entrants yet
- No over/under market yet

Future possibilities such as NBA, MLB, NHL, over/under, and pools should influence the design only where doing so is inexpensive and does not add unnecessary MVP complexity.

---

## 2. Domain Glossary

### Team

A persistent sports franchise belonging to a league.

### League

The official league a team or game belongs to (e.g. NFL, NBA, MLB).

A team belongs to one league in the current design.

### Season

A particular competition season associated with a league.

The MVP is NFL-focused, but Season should not assume that every sport represents a season as a single calendar year.

### Week

A week within a season.

For the MVP, Week can remain an attribute of `Game`. It is not currently treated as a universal domain concept because different sports may organize games differently.

### Game

A scheduled matchup between two teams.

A Game has a home team, away team, scheduled start time, status, and eventually an official result.

### GameLine

An immutable snapshot of betting-market information associated with a Game at a particular point in time.

A Game may have multiple GameLines as the market changes.

### Pick

A user's submitted prediction for a Game using a particular betting market and the GameLine available at submission.

The MVP supports moneyline and against-the-spread picks.

### Result

The official, verified final result of a Game used to evaluate Picks.

### Scoring Rule

The logic used to calculate the points earned by a Pick.

The MVP has two scoring approaches:

- Against the spread: win = 1 point, push = 0.5 points, loss = 0 points.
- Moneyline: odds are applied against a constant 100-point stake.

Examples:

- -150 odds → 50 points
- +200 odds → 200 points

---

## 3. Core Entities

### Team

Attributes:

- `name` — required full team name
- `abbreviation` — short code used for display
- `status` — active/inactive or equivalent
- `league` — league the team belongs to
- `logo` — optional image representation

Business considerations:

- A team can be renamed without becoming a different team identity.
- A team belongs to one league in the MVP.
- Team should remain sport-neutral rather than being an NFL-specific entity.

### League

Attributes:

- `name`

A League allows teams and games to be associated with a specific competition without introducing a separate `Sport` entity.

Examples:

- NFL
- NBA
- MLB
- NHL

The MVP only needs NFL data, but the concept should not prevent additional leagues later.

### Season

Attributes to consider:

- `league`
- `name` or display label
- `start_date`
- `end_date`

A Season represents a particular league season rather than assuming the season is always a single integer year.

Examples:

- NFL 2026
- NBA 2026-27

For the MVP, this can remain simple while avoiding an assumption that would make other sports awkward later.

### Game

Attributes:

- `home_team`
- `away_team`
- `start_time`
- `status`
- `home_score`
- `away_score`
- `league`
- `season`
- `week`

A Game represents one scheduled matchup between exactly two teams.

### GameLine

Attributes:

- `game`
- `source`
- `bookmaker`
- `home_spread`
- `away_spread`
- `home_outright_odds`
- `away_outright_odds`
- `captured_at`

GameLines are immutable.

A Game can have multiple GameLines representing changes in the betting market over time.

Example:

```text
Game
├── GameLine @ 10:00 — KC -3
├── GameLine @ 14:00 — KC -3.5
└── GameLine @ 18:00 — KC -4
```

The most recently available GameLine at the time a Pick is submitted is the line used for that Pick.

### Pick

Attributes:

- `user`
- `game`
- `game_line`
- `picked_team`
- `market_type` — moneyline or spread
- `spread_at_submission` — required for ATS picks
- `odds_at_submission` — required for moneyline picks
- `result`
- `points`
- `submitted_at`

The Pick preserves the betting terms used when the prediction was submitted.

This is intentionally a snapshot of the user's actual prediction rather than relying on mutable/current GameLine state.

For example:

```text
GameLine at submission:
KC -3.5

Pick:
picked_team = KC
market = spread
spread_at_submission = -3.5
```

Even if the GameLine later moves to -4.5, the Pick remains a prediction against -3.5.

---

## 4. Relationships

```text
League
├── Team
├── Season
└── Game

Team
├── Home Games
└── Away Games

Season
└── Games

Game
└── GameLines

User
└── Picks

Pick
├── Game
├── GameLine
└── Picked Team
```

A Game has exactly one home team and one away team.

A Pick belongs to one User and one Game and references the GameLine used when the Pick was submitted.

---

## 5. Domain Invariants

### Team

1. Team name is required.
2. Team belongs to exactly one League.
3. A team's identity persists even if its display name changes.

### Game

1. `home_team != away_team`.
2. A Game has exactly one home team.
3. A Game has exactly one away team.
4. Start time is required.
5. A Game belongs to one League.
6. A Game belongs to one Season.
7. The home and away teams must be compatible with the Game's League.
8. A Game progresses through a defined lifecycle.

### GameLine

1. A GameLine belongs to exactly one Game.
2. A GameLine is immutable after creation.
3. A Game may have multiple GameLines.
4. The latest available GameLine is the one used when a Pick is submitted.

The "latest available line" rule is application/domain behavior rather than simply a database constraint.

### Pick

1. A Pick belongs to exactly one User.
2. A Pick belongs to exactly one Game.
3. A Pick references the GameLine used at submission.
4. `picked_team` must be either the Game's home or away team.
5. A Pick's market type determines which betting terms are required.
6. For ATS picks, the submitted spread must correspond to the selected GameLine.
7. For moneyline picks, the submitted odds must correspond to the selected GameLine.
8. A Pick can only be submitted before the applicable Game start time.
9. Once submitted, the Pick is immutable for the MVP.
10. A cancelled Game causes its Pick to become void rather than deleting the Pick.
11. A postponed Game does not invalidate an existing Pick.

### Historical Integrity

The system should be able to answer:

> "What exactly did the user predict when they submitted this Pick?"

Therefore, the relevant betting terms are preserved on the Pick rather than being reconstructed later from a potentially different GameLine.

---

## 6. Pick Markets and Scoring

### Against the Spread

The user selects a team and the spread associated with that team at submission.

Scoring:

| Outcome     | Points |
| ----------- | -----: |
| Win / Cover |    1.0 |
| Push        |    0.5 |
| Loss        |    0.0 |

### Moneyline

The user selects a team and the moneyline odds associated with that team at submission.

The points are calculated against a constant 100-point stake.

For American odds:

- Negative odds: `points = 100 / (abs(odds) / 100)` conceptually resulting in the profit on a 100-point stake.
- Positive odds: `points = odds`

Examples:

| Odds | Points |
| ---: | -----: |
| -150 |     50 |
| +200 |    200 |

The exact implementation should be covered by scoring tests rather than relying on UI behavior.

### Future Markets

Over/under is a potential future market but is not part of the MVP.

The current design should avoid making `Pick` intrinsically synonymous with "picked team against a spread" so that additional market types can be introduced later without redesigning the entire domain.

---

## 7. Game Lifecycle

Primary lifecycle:

```text
Scheduled
    ↓
In Progress
    ↓
Final
```

Additional states:

- `Postponed`
- `Delayed`
- `Cancelled`

### Postponed

A postponed game moves to a different start time/date.

Existing Picks remain valid.

New Picks can be submitted until the new start time.

### Delayed

A delayed game starts later than originally scheduled on the same day.

For the MVP, the original start time remains the Pick lock boundary.

Once the original start time has passed, Picks cannot be submitted or changed.

### Cancelled

A cancelled Game does not count toward user scoring.

Existing Picks are marked/treated as void rather than deleted.

The historical Pick remains available for auditing and historical records.

---

## 8. Pick Lifecycle

For the MVP:

```text
Submitted
    ↓
Locked
    ↓
Scored
```

### Submitted

A valid Pick has been accepted and persisted.

### Locked

A Pick becomes locked once the applicable Game start time is reached.

The MVP does not support editing a submitted Pick.

### Scored

After the Game reaches `Final`, the system evaluates the Pick according to its market and scoring rule and records the result and points.

A cancelled Game results in a void Pick rather than a scored Pick.

---

## 9. Temporal Considerations

The domain is time-sensitive.

The important timestamps are:

- Game `start_time`
- GameLine `captured_at`
- Pick `submitted_at`
- Pick `locked_at` (if explicitly persisted)
- Pick `scored_at` (if explicitly persisted)

A critical rule is:

> The GameLine used by a Pick is determined at Pick submission time.

The system must avoid a race where a new GameLine is captured between the user viewing a line and submitting the Pick without clearly defining which line becomes authoritative.

The submission operation should atomically determine and persist the applicable GameLine and the Pick's betting-term snapshot.

---

## 10. Future Extensibility Boundaries

The MVP should not implement speculative features, but the domain should avoid unnecessary coupling that would make them difficult later.

### Future: Additional Leagues

Potential future leagues include NBA, MLB, and NHL.

`Team`, `Game`, `GameLine`, and `Season` should therefore not contain NFL-specific assumptions.

A separate `Sport` entity is not currently necessary. League is sufficient for the MVP and can be expanded later if a concrete requirement emerges.

### Future: Additional Betting Markets

Potential future market:

- Over/Under

The Pick should therefore represent a prediction against a market rather than permanently assuming every Pick is a team/spread selection.

### Future: Pools

Pools, Entrants, Pick Sheets, and pool-specific Leaderboards are intentionally outside the MVP.

Do not introduce those entities merely to prepare for the possibility.

The current design should avoid spreading assumptions throughout the codebase that would make a future participant/pool concept impossible to introduce.

### Future: Line History

GameLine is already modeled as an immutable snapshot, allowing historical line movement to exist without requiring a redesign.

---

## 11. Deliberate Non-Goals for MVP

The following are intentionally not modeled yet:

- Pools
- Entrants
- Pick Sheets
- Over/Under
- Multiple picks per user/game
- Pick revision history
- Sport as a separate entity
- Complex multi-market betting
- Multiple scoring systems per pool
- Social features

These may become future requirements, but implementing them now would add complexity without current product value.

---

## 12. Recommended Design Principles

1. Model the business concepts, not the external API's schema.
2. Make immutable historical facts immutable.
3. Preserve the exact terms of a user's submitted prediction.
4. Enforce true invariants at the database level where practical.
5. Keep business behavior in the appropriate domain/application layer.
6. Avoid deleting historical facts merely because they no longer affect scoring.
7. Design core concepts to be sport-neutral where doing so is inexpensive.
8. Do not build speculative abstractions solely for possible future requirements.
9. Prefer explicit domain rules over implicit assumptions.
10. Treat time and state transitions as first-class concerns in a prediction system.

---

## 13. Next Implementation Step

Before creating Django models, convert the domain decisions above into:

1. ~~Model fields and relationships.~~
2. ~~Database-level constraints.~~
3. ~~Database indexes based on expected queries.~~
4. ~~Model/domain tests for important invariants.~~
5. ~~Scoring tests for ATS and moneyline calculations.~~
6. ~~Migration.~~

The goal is not to produce a perfect schema for every future feature. The goal is to create a **small, coherent domain model that accurately represents the MVP while leaving sensible paths for evolution.**
