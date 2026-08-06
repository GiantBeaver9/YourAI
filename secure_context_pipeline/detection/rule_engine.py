"""Native rule-engine detector — the degraded fallback when Presidio isn't installed.

Deterministic and dependency-free so tests/CI always run. Covers: formatted identifiers
(regex + validators), label-driven values (the label types the value), the magnitude rule for
bare numbers (with the clinical-unit carve-out), and clinical quasi-identifiers (ethnicity /
sex / age) so the policy can PRESERVE them explicitly. Free-text *name* NER is Presidio's job;
here names are label-driven + the Title-Case run after a name label.
"""

from __future__ import annotations

import re

from ..config import ObfuscationPolicy
from ..entities import DetectedEntity, EntityType
from .base import RuleSpec
from .clustering import assign_clusters
from .rules import (
    CLINICAL_UNITS,
    ETHNICITY_TERMS,
    LABEL_RULES,
    SEX_TERMS,
    STANDARD_RULES,
)

_MAGNITUDE = re.compile(r"\b\d{5,}\b|\b0\d{3,}\b")       # 5+ digit run OR leading-zero run (4+ digits)
_TITLECASE_RUN = re.compile(r"[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,3}")  # one line — never cross \n
_ALNUM_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")
_DATE_VALUE = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\b(?:1[89]\d\d|20\d\d)\b")
_AGE = re.compile(r"\b(\d{1,3})[\s-]*(?:year[\s-]*old|y\.?o\.?|yo)\b|\bage\s*[:#]?\s*(\d{1,3})\b", re.I)


class RuleEngineDetector:
    def __init__(
        self,
        rules: list[RuleSpec] | None = None,
        label_rules: list | None = None,
    ) -> None:
        self.rules = rules if rules is not None else STANDARD_RULES
        self.label_rules = label_rules if label_rules is not None else LABEL_RULES

    async def detect(
        self, text: str, policy: ObfuscationPolicy
    ) -> list[DetectedEntity]:
        ents: list[DetectedEntity] = []
        ents += self._regex_rules(text)
        ents += self._label_rules(text)
        ents += self._propagate_names(text, ents)  # find free-text mentions of known names
        ents += self._magnitude(text)
        ents += self._clinical(text)
        assign_clusters(ents)
        return ents

    def _propagate_names(self, text: str, found: list[DetectedEntity]) -> list[DetectedEntity]:
        """Deterministic coreference: once a name is known (from a label), find its other
        mentions — the full name and each of its word-parts — and tokenize them into the same
        cluster. Closes the free-text-mention gap without NER (the 'advanced find-and-replace')."""
        name_ents = [e for e in found if e.entity_type is EntityType.NAME]
        taken = {(e.start, e.end) for e in found}
        out: list[DetectedEntity] = []
        seen: set[str] = set()
        for e in name_ents:
            for word in [e.text, *e.text.split()]:
                w = word.strip(" ,.;:'\"")
                if len(w) < 2 or w.lower() in seen:
                    continue
                seen.add(w.lower())
                for m in re.finditer(rf"\b{re.escape(w)}\b", text):
                    span = (m.start(), m.end())
                    if span in taken:
                        continue
                    taken.add(span)
                    out.append(DetectedEntity(m.start(), m.end(), EntityType.NAME, m.group(), 0.75, "propagate"))
        return out

    # --- formatted identifiers ---
    def _regex_rules(self, text: str) -> list[DetectedEntity]:
        out = []
        for rule in self.rules:
            pat = rule.pattern()
            if not pat:
                continue
            for m in pat.finditer(text):
                conf = rule.confidence
                if rule.validator and not rule.validator(m.group()):
                    conf *= 0.6  # validation failure lowers confidence; NEVER a pass-through gate
                out.append(DetectedEntity(m.start(), m.end(), rule.entity_type, m.group(), conf, rule.name or "rules"))
        return out

    # --- label-driven: the label types the value; capture to a sane boundary ---
    def _label_rules(self, text: str) -> list[DetectedEntity]:
        out = []
        for label_re, et, conf in self.label_rules:
            for m in re.finditer(label_re, text):
                start = m.end()
                tail = text[start:start + 80]
                if et is EntityType.NAME:
                    vm = _TITLECASE_RUN.match(tail)
                elif et is EntityType.DOB:
                    vm = _DATE_VALUE.search(tail)
                else:
                    vm = _ALNUM_VALUE.match(tail)
                if not vm:
                    continue
                vs, ve = start + vm.start(), start + vm.end()
                out.append(DetectedEntity(vs, ve, et, text[vs:ve], conf, "label"))
        return out

    # --- magnitude rule: bare identifier-numbers, with the clinical-unit carve-out ---
    def _magnitude(self, text: str) -> list[DetectedEntity]:
        out = []
        for m in _MAGNITUDE.finditer(text):
            trailing = text[m.end():m.end() + 12].lower().lstrip()
            if any(trailing.startswith(u) for u in CLINICAL_UNITS):
                continue  # a lab value (viral load / cell count), not an identifier — keep
            out.append(DetectedEntity(m.start(), m.end(), EntityType.GENERIC_ID, m.group(), 0.7, "magnitude"))
        return out

    # --- clinical quasi-identifiers: detect so policy can PRESERVE them by design ---
    def _clinical(self, text: str) -> list[DetectedEntity]:
        out = []
        low = text.lower()
        for term in ETHNICITY_TERMS:
            start = 0
            while (i := low.find(term, start)) != -1:
                out.append(DetectedEntity(i, i + len(term), EntityType.ETHNICITY, text[i:i + len(term)], 0.8, "lexicon"))
                start = i + len(term)
        for term in SEX_TERMS:
            for m in re.finditer(rf"\b{re.escape(term)}\b", text, re.I):
                out.append(DetectedEntity(m.start(), m.end(), EntityType.SEX, m.group(), 0.6, "lexicon"))
        for m in _AGE.finditer(text):
            g = m.group(1) or m.group(2)
            gs = m.start(1) if m.group(1) else m.start(2)
            out.append(DetectedEntity(gs, gs + len(g), EntityType.AGE, g, 0.8, "lexicon"))
        return out
