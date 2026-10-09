from django.db import models
from django.db.models import F, Q


class League(models.Model):
    name = models.CharField(max_length=100)
    abbreviation = models.CharField(max_length=16)
    sport = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['name'], name='league_name_uniq'),
            models.UniqueConstraint(
                fields=['abbreviation'],
                name='league_abbreviation_uniq',
            ),
        ]

    def __str__(self):
        return self.name


class Season(models.Model):
    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='seasons',
        db_index=False,
    )
    year = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['league', 'year'],
                name='season_league_year_uniq',
            ),
        ]

    def __str__(self):
        return f'{self.league} {self.year}'


class Team(models.Model):
    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='teams',
        db_index=False,
    )
    external_id = models.CharField(max_length=64)
    name = models.CharField(max_length=100)
    abbreviation = models.CharField(max_length=16)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['league', 'external_id'],
                name='team_league_external_id_uniq',
            ),
            models.UniqueConstraint(
                fields=['league', 'abbreviation'],
                name='team_league_abbreviation_uniq',
            ),
        ]

    def __str__(self):
        return self.name


class Game(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        IN_PROGRESS = 'in_progress', 'In progress'
        FINAL = 'final', 'Final'
        POSTPONED = 'postponed', 'Postponed'
        DELAYED = 'delayed', 'Delayed'
        CANCELLED = 'cancelled', 'Cancelled'

    external_id = models.CharField(max_length=64)
    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name='games',
        db_index=False,
    )
    week = models.PositiveSmallIntegerField(null=True, blank=True)
    home_team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name='home_games',
    )
    away_team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name='away_games',
    )
    kickoff_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status)
    home_score = models.PositiveSmallIntegerField(null=True, blank=True)
    away_score = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['external_id'],
                name='game_external_id_uniq',
            ),
            models.CheckConstraint(
                condition=~Q(home_team=F('away_team')),
                name='game_home_away_differ',
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        'scheduled',
                        'in_progress',
                        'final',
                        'postponed',
                        'delayed',
                        'cancelled',
                    ]
                ),
                name='game_status_known',
            ),
        ]
        indexes = [
            models.Index(fields=['season', 'week'], name='game_season_week_idx'),
        ]

    def __str__(self):
        return f'{self.away_team} at {self.home_team}'


class GameLine(models.Model):
    game = models.ForeignKey(
        Game,
        on_delete=models.CASCADE,
        related_name='lines',
        db_index=False,
    )
    source = models.CharField(max_length=64)
    bookmaker = models.CharField(max_length=64)
    home_spread = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
    )
    away_spread = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
    )
    home_spread_price = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
    )
    away_spread_price = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
    )
    home_moneyline = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
    )
    away_moneyline = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
    )
    captured_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(home_spread__isnull=True, away_spread__isnull=True)
                    | (
                        Q(home_spread__isnull=False, away_spread__isnull=False)
                        & Q(away_spread=-F('home_spread'))
                    )
                ),
                name='gameline_spread_opposites',
            ),
            models.CheckConstraint(
                condition=(
                    Q(home_moneyline__isnull=True, away_moneyline__isnull=True)
                    | (
                        Q(
                            home_moneyline__isnull=False,
                            away_moneyline__isnull=False,
                        )
                        & Q(home_moneyline__gt=1, away_moneyline__gt=1)
                    )
                ),
                name='gameline_moneyline_pair',
            ),
            models.CheckConstraint(
                condition=(
                    (Q(home_spread_price__isnull=True) | Q(home_spread_price__gt=1))
                    & (Q(away_spread_price__isnull=True) | Q(away_spread_price__gt=1))
                ),
                name='gameline_spread_prices_gt_one',
            ),
            models.CheckConstraint(
                condition=(
                    Q(home_spread__isnull=False, away_spread__isnull=False)
                    | Q(home_moneyline__isnull=False, away_moneyline__isnull=False)
                ),
                name='gameline_has_market',
            ),
        ]
        indexes = [
            models.Index(
                fields=['game', '-captured_at'],
                name='gameline_game_captured_idx',
            ),
        ]

    def __str__(self):
        return f'{self.game} at {self.captured_at}'
