from django.conf import settings
from django.db import models
from django.db.models import Q
from sports.models import Game, GameLine, Team


class Pick(models.Model):
    class PickType(models.TextChoices):
        SPREAD = 'spread', 'Spread'
        MONEYLINE = 'moneyline', 'Moneyline'

    class Result(models.TextChoices):
        WIN = 'win', 'Win'
        LOSS = 'loss', 'Loss'
        PUSH = 'push', 'Push'
        VOID = 'void', 'Void'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='picks',
        db_index=False,
    )
    game = models.ForeignKey(
        Game,
        on_delete=models.PROTECT,
        related_name='picks',
    )
    game_line = models.ForeignKey(
        GameLine,
        on_delete=models.PROTECT,
        related_name='picks',
    )
    picked_team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name='picked_picks',
    )
    pick_type = models.CharField(max_length=16, choices=PickType)
    line_value = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
    )
    price = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
    )
    result = models.CharField(  # noqa: DJ001 — NULL means not yet scored
        max_length=8,
        choices=Result,
        null=True,
        blank=True,
    )
    points = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'game', 'pick_type'],
                name='pick_user_game_type_uniq',
            ),
            models.CheckConstraint(
                condition=(
                    Q(pick_type='spread', line_value__isnull=False)
                    | Q(pick_type='moneyline', price__isnull=False)
                ),
                name='pick_market_terms_present',
            ),
            models.CheckConstraint(
                condition=(
                    Q(result__isnull=True)
                    | Q(result__in=['win', 'loss', 'push', 'void'])
                ),
                name='pick_result_known',
            ),
        ]
        indexes = [
            models.Index(
                fields=['user', '-submitted_at'],
                name='pick_user_submitted_idx',
            ),
        ]

    def __str__(self):
        return f'{self.user} {self.pick_type} {self.picked_team}'
