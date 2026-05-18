"""Shared normalizers for dedupe/cluster matching.

Used by routes/contacts.py, routes/import_.py, routes/duplicates.py.
"""
import re


def normalize_phone(s: str | None) -> str:
    """Digits-only normalized form. Used for dedupe and clustering only."""
    if not s:
        return ""
    return re.sub(r"\D+", "", s)


def normalize_email(s: str | None) -> str:
    if not s:
        return ""
    return s.strip().lower()


LABEL_MAP_PHONE = {
    "cell": "mobile",
    "mobile": "mobile",
    "iphone": "mobile",
    "home": "home",
    "work": "work",
    "main": "work",
    "voice": None,
    "fax": "other",
}


def canonical_phone_label(types: list[str] | None) -> str | None:
    """Map iPhone vCard TYPE params (CELL, HOME, etc.) to our labels."""
    if not types:
        return None
    for t in types:
        key = (t or "").lower().strip()
        if key in LABEL_MAP_PHONE:
            val = LABEL_MAP_PHONE[key]
            if val:
                return val
    return "other"


def canonical_email_label(types: list[str] | None) -> str | None:
    if not types:
        return None
    for t in types:
        key = (t or "").lower().strip()
        if key in ("home", "work"):
            return key
    return "other"
