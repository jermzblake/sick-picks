| Model        | Purpose                                         | Key fields                                           | Relationships | Important constraints                                                     |
| ------------ | ----------------------------------------------- | ---------------------------------------------------- | ------------- | ------------------------------------------------------------------------- |
| **Team**     | A team participating in a league                | name, abbreviation                                   | → League      | unique abbreviation                                                       |
| **Game**     | Scheduled NFL game                              | week, teams, start, score, status                    | → Team ×2     | home ≠ away                                                               |
| **GameLine** | Betting line available for game                 | game, spread, source, captured_at                    | → Game        | immutable line history                                                    |
| **Pick**     | User's submitted selection                      | type, selection, line snapshot, submitted_at, result | → GameLine    | user can have one pick type per game. Can't be submitted after game start |
| **League**   | Represents the league the teams/games belong to | name, sport                                          | —             | unique name & abbreviation                                                |
| **Season**   | Represents a particular season within a league. | league_id, year                                      | → League      | UNIQUE(league_id, year)                                                   |

---

### MVP Django Models:

```
accounts/
    User

sports/
    League
    Season
    Team
    Game
    GameLine

picks/
    Pick
```
