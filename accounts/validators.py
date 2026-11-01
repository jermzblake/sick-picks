import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class PasswordComplexityValidator:
    """Require mixed case and at least one digit."""

    def validate(self, password, user=None):
        if not re.search(r'[A-Z]', password):
            raise ValidationError(
                _('This password must contain at least one uppercase letter.'),
                code='password_no_upper',
            )
        if not re.search(r'[a-z]', password):
            raise ValidationError(
                _('This password must contain at least one lowercase letter.'),
                code='password_no_lower',
            )
        if not re.search(r'\d', password):
            raise ValidationError(
                _('This password must contain at least one number.'),
                code='password_no_digit',
            )

    def get_help_text(self):
        return _(
            'Your password must contain at least one uppercase letter, '
            'one lowercase letter, and one number.'
        )
