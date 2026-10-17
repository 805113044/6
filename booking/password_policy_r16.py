"""Validation only for setting a NEW password, never for login/old password verification.

Passwords are hashed by Django's configured R14/R15 salted, peppered PBKDF2 hasher.
Existing credentials remain verifiable with legacy hashers.
"""
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_new_password(value):
    if (not isinstance(value, str) or len(value) < 6 or
        not any('a' <= c <= 'z' or 'A' <= c <= 'Z' for c in value) or
        not any('0' <= c <= '9' for c in value)):
        raise ValidationError(
            _('新密码至少6位，并须同时包含英文字母（大小写均可）与数字。'),
            code='letters_and_digits_required',
        )


class LettersDigitsPasswordValidator:
    """Django standard admin create/change password validator."""
    def validate(self, password, user=None):
        validate_new_password(password)

    def get_help_text(self):
        return _('至少6位，同时包含英文字母（大小写均可）和数字。')
