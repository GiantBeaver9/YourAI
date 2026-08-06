"""Generalization — reduce precision. The third primitive: one-way and vault-free.

Dates -> year (Safe Harbor #3): the kept year is truthful, so there is nothing to restore and
no vault entry. Age >= ceiling -> "90+". The age-driven date *branches* (<=5 -> route to human;
>=90 -> delete all dates) are document-level decisions and live in the pipeline, not here.
"""

from __future__ import annotations

import re

from ...entities import EntityType
from .base import ObfuscationStrategy, StrategyContext, TransformResult

_YEAR_RE = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
_AGE_RE = re.compile(r"\d{1,3}")


def extract_year(value: str) -> str | None:
    m = _YEAR_RE.search(value)
    return m.group(0) if m else None


class Generalization(ObfuscationStrategy):
    name = "generalize"

    def transform(
        self, value: str, entity_type: EntityType, ctx: StrategyContext
    ) -> TransformResult:
        if entity_type in (EntityType.DOB, EntityType.DATE):
            year = extract_year(value)
            # no parseable year -> fail closed to a redaction, never pass the raw date through
            replacement = year if year is not None else "[DATE]"
            return TransformResult(replacement=replacement, reversible=False, canonical=value)

        if entity_type is EntityType.AGE:
            m = _AGE_RE.search(value)
            if m and int(m.group(0)) >= ctx.policy.elderly_age_ceiling:
                return TransformResult(replacement="90+", reversible=False, canonical=value)
            return TransformResult(replacement=value, reversible=False, canonical=value)

        # anything else generalized -> redact rather than leak
        return TransformResult(replacement=f"[{entity_type.tag}]", reversible=False, canonical=value)
