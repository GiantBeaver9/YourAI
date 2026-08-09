"""``PresidioDetector`` — the intended detection substrate.

Our rules-as-data compile into a single custom Presidio ``EntityRecognizer`` (the rule table +
the magnitude/ZIP pass), which we register into Presidio's ``RecognizerRegistry`` alongside its
spaCy-backed ``PERSON`` NER. **Presidio runs detection**; we configure it. This is the whole
point of "harness around the defined tool, not a reimplementation of NER": names come from
Presidio's statistical model (recall our regexes can't match), while every structured
identifier comes from the vetted rule rows — same data the native fallback runs.

Guarded import: if Presidio (or its spaCy model) is unavailable, the factory falls back to the
native detector, so CI never crashes on a missing heavy dependency.
"""

from __future__ import annotations

from ..config import ObfuscationPolicy
from ..entities import DetectedEntity, EntityType
from .magnitude import magnitude_spans
from .rules import STANDARD_RULES, Rule

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

# Presidio's PERSON entity is our NAME.
_PRESIDIO_TO_ENTITY = {"PERSON": EntityType.NAME}
_SPACY_MODEL = "en_core_web_sm"


class _RuleTableRecognizer(EntityRecognizer):
    """Our rules-as-data, presented to Presidio as one recognizer.

    ``analyze`` runs the compiled rule rows plus the magnitude/ZIP pass and returns Presidio
    ``RecognizerResult``\\s, so Presidio's engine orchestrates them next to its own NER."""

    def __init__(self, policy: ObfuscationPolicy, custom_rules: list[Rule] | None = None) -> None:
        self._policy = policy
        self._rules = [r for r in (list(STANDARD_RULES) + list(custom_rules or [])) if r.enabled]
        supported = sorted({r.entity_type.value for r in self._rules} | {EntityType.ZIP.value,
                                                                          EntityType.GENERIC_ID.value})
        super().__init__(supported_entities=supported, name="scp_rule_table")

    def load(self) -> None:  # no model to load — the rules are the state
        return None

    def analyze(self, text, entities, nlp_artifacts=None):  # noqa: ANN001 (Presidio signature)
        results = []
        for rule in self._rules:
            grp = rule.value_group
            for m in rule.compiled().finditer(text):
                start, end = m.span(grp)
                value = m.group(grp)
                stripped = value.rstrip(" \t\n.,;:")
                end -= len(value) - len(stripped)
                if not stripped or (entities and rule.entity_type.value not in entities):
                    continue
                results.append(RecognizerResult(rule.entity_type.value, start, end, rule.confidence))
        for span in magnitude_spans(text, self._policy):
            if entities and span.entity_type.value not in entities:
                continue
            results.append(RecognizerResult(span.entity_type.value, span.start, span.end,
                                            span.confidence))
        return results


class PresidioDetector:
    name = "presidio"

    def __init__(self, policy: ObfuscationPolicy, custom_rules: list[Rule] | None = None) -> None:
        nlp_engine = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": _SPACY_MODEL}],
        }).create_engine()
        self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        self._analyzer.registry.add_recognizer(_RuleTableRecognizer(policy, custom_rules))
        # We only want PERSON from Presidio's predefined/NER stack; our rules own the rest.
        self._entities = sorted(
            {r.entity_type.value for r in STANDARD_RULES}
            | {EntityType.ZIP.value, EntityType.GENERIC_ID.value, "PERSON"}
        )

    def detect(self, text: str) -> list[DetectedEntity]:
        raw = self._analyzer.analyze(text=text, language="en", entities=self._entities)
        out: list[DetectedEntity] = []
        for r in raw:
            et = _PRESIDIO_TO_ENTITY.get(r.entity_type)
            if et is None:
                try:
                    et = EntityType(r.entity_type)
                except ValueError:
                    continue
            out.append(
                DetectedEntity(
                    start=r.start, end=r.end, entity_type=et, text=text[r.start:r.end],
                    confidence=float(r.score), source="presidio",
                )
            )
        return out
