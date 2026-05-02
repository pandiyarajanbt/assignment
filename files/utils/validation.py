import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.validators import EmailValidator
from django.core.exceptions import ValidationError

from .normalization import normalize_text


PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")
email_validator = EmailValidator()


def require(row, column):
    value = normalize_text(row.get(column))
    if not value:
        raise ValueError("This field is required")
    return value


def validate_length(value, max_length):
    normalized = normalize_text(value)
    if len(normalized) > max_length:
        raise ValueError(f"Value exceeds {max_length} characters")
    return normalized


def optional_text(row, column):
    return normalize_text(row.get(column))


def validate_email(value):
    normalized = normalize_text(value).lower()
    try:
        email_validator(normalized)
    except ValidationError as exc:
        raise ValueError("Invalid email format") from exc
    return normalized


def validate_phone(value):
    normalized = normalize_text(value)
    compact = re.sub(r"[\s()-]", "", normalized)
    if not PHONE_RE.fullmatch(compact):
        raise ValueError("Invalid phone format")
    return compact


def validate_int(value):
    try:
        return int(normalize_text(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid integer value") from exc


def validate_choice(value, allowed_values):
    integer_value = validate_int(value)
    if integer_value not in allowed_values:
        allowed = ", ".join(str(item) for item in sorted(allowed_values))
        raise ValueError(f"Value must be one of: {allowed}")
    return integer_value


def validate_decimal(value, min_value=None, max_value=None):
    try:
        decimal_value = Decimal(normalize_text(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid decimal value") from exc
    if min_value is not None and decimal_value < Decimal(str(min_value)):
        raise ValueError(f"Value must be >= {min_value}")
    if max_value is not None and decimal_value > Decimal(str(max_value)):
        raise ValueError(f"Value must be <= {max_value}")
    return decimal_value


def validate_date(value):
    normalized = normalize_text(value)
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Invalid date format. Expected YYYY-MM-DD") from exc
