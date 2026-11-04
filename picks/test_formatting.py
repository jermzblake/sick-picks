from decimal import Decimal

import pytest

from picks.formatting import display_username, to_american


@pytest.mark.parametrize(
    ('username', 'expected'),
    [
        ('test@gmail.com', 'test'),
        ('alice', 'alice'),
        ('a.b+tag@example.org', 'a.b+tag'),
        ('@only-domain', '@only-domain'),
        ('', ''),
    ],
)
def test_display_username(username, expected):
    assert display_username(username) == expected


@pytest.mark.parametrize(
    ('decimal_odds', 'expected'),
    [
        (Decimal('2.0'), '+100'),
        (Decimal('2.00'), '+100'),
        (Decimal('1.91'), '-110'),
        (Decimal('2.5'), '+150'),
        (Decimal('1.5'), '-200'),
        (Decimal('3.0'), '+200'),
        (Decimal('1.909'), '-110'),
    ],
)
def test_to_american(decimal_odds, expected):
    assert to_american(decimal_odds) == expected


def test_to_american_none():
    assert to_american(None) == ''


def test_to_american_rejects_invalid():
    with pytest.raises(ValueError):
        to_american(Decimal('1'))
    with pytest.raises(ValueError):
        to_american(Decimal('0.5'))
