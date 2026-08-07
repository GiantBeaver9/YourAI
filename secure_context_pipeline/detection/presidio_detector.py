"""Presidio-backed detector — the intended substrate.

Presidio runs the detection (its NER + built-in recognizers + custom recognizers compiled from
our rules); we add the domain passes Presidio doesn't ship (magnitude rule, clinical
quasi-identifier lexicon, label-driven values) and merge. If Presidio isn't importable, the
factory falls back to the native rule engine so nothing breaks.
"""

from __future__ import annotations

import os

from ..config import ObfuscationPolicy
from ..entities import DetectedEntity, EntityType
from .clustering import assign_clusters
from .rule_engine import RuleEngineDetector

try:  # guarded — Presidio + spaCy model is a heavy, optional install
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    _HAS_PRESIDIO = True
except Exception:  # pragma: no cover - import guard
    _HAS_PRESIDIO = False

def _spacy_model() -> str:
    """spaCy model Presidio's NER runs on. `en_core_web_lg` = best accuracy (~560MB);
    `en_core_web_sm` = tiny/fast builds. Override with SCP_SPACY_MODEL (read at call time)."""
    return os.environ.get("SCP_SPACY_MODEL", "en_core_web_lg")

# Presidio entity label -> our EntityType
_PRESIDIO_MAP = {
    "PERSON": EntityType.NAME,
    "US_SSN": EntityType.SSN,
    "PHONE_NUMBER": EntityType.PHONE,
    "EMAIL_ADDRESS": EntityType.EMAIL,
    "CREDIT_CARD": EntityType.CREDIT_CARD,
    "US_BANK_NUMBER": EntityType.ACCOUNT,
    "LOCATION": EntityType.ADDRESS,
    "DATE_TIME": EntityType.DATE,
    "IP_ADDRESS": EntityType.IP,
    "URL": EntityType.URL,
    "MEDICAL_LICENSE": EntityType.GENERIC_ID,
    "US_DRIVER_LICENSE": EntityType.GENERIC_ID,
}


def _build_analyzer(model: str) -> "AnalyzerEngine":
    """Build Presidio's analyzer on a specific spaCy model (so `sm` works, not just the
    default `lg`)."""
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": model}],
        }
    )
    return AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])


class PresidioDetector:
    def __init__(self, model: str | None = None) -> None:
        self._analyzer = _build_analyzer(model or _spacy_model())
        self._domain = RuleEngineDetector()  # our domain passes ride alongside Presidio

    async def detect(
        self, text: str, policy: ObfuscationPolicy
    ) -> list[DetectedEntity]:
        ents: list[DetectedEntity] = []
        for r in self._analyzer.analyze(text=text, language="en"):
            et = _PRESIDIO_MAP.get(r.entity_type)
            if et is None:
                continue
            ents.append(DetectedEntity(r.start, r.end, et, text[r.start:r.end], float(r.score), "presidio"))
        # domain passes Presidio doesn't do: magnitude, clinical lexicon, label-driven
        ents += self._domain._magnitude(text)
        ents += self._domain._clinical(text)
        ents += self._domain._label_rules(text)
        # deterministic coreference: propagate every mention of a name Presidio/labels found
        ents += self._domain._propagate_names(text, ents)
        assign_clusters(ents)
        return ents


def _model_installed(model: str) -> bool:
    """Fast check: is the spaCy model actually installed? Avoids a slow failed load attempt
    (or a download) when it isn't, so absence falls back to native instantly."""
    try:
        import spacy.util

        return spacy.util.is_package(model)
    except Exception:  # pragma: no cover
        return False


def get_detector(prefer_presidio: bool = True):
    """Presidio substrate when it's installed AND its spaCy model is present; native rule engine
    otherwise (instant fallback — no hang on a missing model). Set SCP_DISABLE_PRESIDIO to force
    the deterministic native engine (used by the test suite)."""
    if os.environ.get("SCP_DISABLE_PRESIDIO"):
        return RuleEngineDetector()
    model = _spacy_model()
    if prefer_presidio and _HAS_PRESIDIO and _model_installed(model):
        try:
            return PresidioDetector(model)
        except Exception:  # pragma: no cover - initialization edge cases
            pass
    return RuleEngineDetector()
