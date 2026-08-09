"""``RuleEngineDetector`` — the native-regex substrate.

This is the **degraded fallback**, used only when Presidio is not installed, so keyless CI and a
zero-heavy-dependency demo still run. It executes the very same :data:`STANDARD_RULES` rows
(and any custom rows) plus the magnitude/ZIP pass and a conservative name heuristic. It is
*not* the intended production substrate — Presidio is — but it exercises the identical rule
data, so the rules-as-data thesis holds on either engine.
"""

from __future__ import annotations

import re

from ..config import ObfuscationPolicy
from ..entities import DetectedEntity, EntityType
from .magnitude import magnitude_spans
from .rules import STANDARD_RULES, Rule

# Capitalized bigram -> a possible unlabeled name. Low confidence; over-detection here is
# harmless (a false-positive name still round-trips correctly via its own vault token).
_NAME_BIGRAM = re.compile(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b")
_NAME_STOP = {
    "medical", "record", "blood", "pressure", "chief", "complaint", "date", "birth",
    "social", "security", "phone", "email", "address", "history", "present", "illness",
    "physical", "exam", "assessment", "plan", "review", "systems", "family", "patient",
    "progress", "note", "discharge", "summary", "vital", "signs", "heart", "rate",
    "attending", "physician", "provider", "primary", "care", "emergency", "contact",
    "united", "states", "new", "york", "san", "los", "case", "number", "account",
    "policy", "member", "insurance", "north", "south", "east", "west",
}


class RuleEngineDetector:
    name = "native-regex"

    def __init__(
        self, policy: ObfuscationPolicy, custom_rules: list[Rule] | None = None
    ) -> None:
        self._policy = policy
        self._rules = [r for r in (list(STANDARD_RULES) + list(custom_rules or [])) if r.enabled]

    def detect(self, text: str) -> list[DetectedEntity]:
        spans: list[DetectedEntity] = []
        for rule in self._rules:
            pattern = rule.compiled()
            grp = rule.value_group
            for m in pattern.finditer(text):
                start, end = m.span(grp)
                value = m.group(grp)
                stripped = value.rstrip(" \t\n.,;:")  # trim trailing whitespace/punctuation
                end -= len(value) - len(stripped)
                if not stripped:
                    continue
                spans.append(
                    DetectedEntity(
                        start=start, end=end, entity_type=rule.entity_type,
                        text=stripped, confidence=rule.confidence, source=f"rule:{rule.id}",
                    )
                )
        spans.extend(magnitude_spans(text, self._policy))
        spans.extend(self._name_heuristic(text))
        return spans

    def _name_heuristic(self, text: str) -> list[DetectedEntity]:
        out: list[DetectedEntity] = []
        for m in _NAME_BIGRAM.finditer(text):
            w1, w2 = m.group(1).lower(), m.group(2).lower()
            if w1 in _NAME_STOP or w2 in _NAME_STOP:
                continue
            out.append(
                DetectedEntity(
                    start=m.start(), end=m.end(), entity_type=EntityType.NAME,
                    text=m.group(0), confidence=0.55, source="heuristic:name",
                )
            )
        return out
