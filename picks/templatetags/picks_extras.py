from decimal import Decimal

from django import template

from picks.formatting import to_american

register = template.Library()


@register.filter(name='american')
def american(value: Decimal | None) -> str:
    if value is None or value == '':
        return ''
    try:
        return to_american(Decimal(value) if not isinstance(value, Decimal) else value)
    except ValueError, ArithmeticError, TypeError:
        return ''
