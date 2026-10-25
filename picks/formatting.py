from decimal import ROUND_HALF_UP, Decimal

_EVEN = Decimal('2')
_HUNDRED = Decimal('100')


def to_american(decimal_odds: Decimal | None) -> str:
    """Convert decimal odds to an American odds string (e.g. '+150', '-110')."""
    if decimal_odds is None:
        return ''
    if decimal_odds <= 1:
        raise ValueError('Decimal odds must be greater than 1.')
    if decimal_odds >= _EVEN:
        american = ((decimal_odds - 1) * _HUNDRED).quantize(
            Decimal('1'),
            rounding=ROUND_HALF_UP,
        )
        return f'+{american}'
    american = (-_HUNDRED / (decimal_odds - 1)).quantize(
        Decimal('1'),
        rounding=ROUND_HALF_UP,
    )
    return f'{american}'
