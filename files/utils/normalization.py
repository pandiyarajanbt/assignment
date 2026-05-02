import re


WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value):
    if value is None:
        return ""
    return WHITESPACE_RE.sub(" ", str(value).strip())


def normalize_key(value):
    return normalize_text(value).casefold()


def normalize_bool(value):
    normalized = normalize_key(value)
    if normalized in {"true", "1", "yes", "y", "active"}:
        return True
    if normalized in {"false", "0", "no", "n", "inactive"}:
        return False
    raise ValueError("Invalid boolean value")
