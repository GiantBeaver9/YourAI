"""Canonicalization used before hashing — the HMAC input, so it decides token determinism.

Kept deliberately conservative: casefold + strip leading titles + collapse whitespace. We do
NOT strip aggressively (that would collapse distinct people — "Dr. Smith" vs "Ms. Smith").
"""

from __future__ import annotations

import re

_TITLE_RE = re.compile(r"^(mr|mrs|ms|miss|dr|prof|rev|sir)\.?\s+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def normalize_value(value: str) -> str:
    v = value.strip()
    v = _TITLE_RE.sub("", v)
    v = _WS_RE.sub(" ", v)
    return v.casefold()
