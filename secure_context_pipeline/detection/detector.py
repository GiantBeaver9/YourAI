"""Entity detection subsystem — Presidio substrate with regex fallback.

Presidio is the primary substrate (rules-as-data compile into its RecognizerRegistry).
If Presidio is not installed, fallback to RuleEngineDetector to run keyless CI/tests.
"""

from __future__ import annotations

import re
from typing import Protocol

from ..config import ObfuscationPolicy
from ..entities import DetectedEntity, EntityType

# Clinical units that carve out 5+ digit numbers from being scrubbed as PII/PHI
CLINICAL_UNITS = {
    "copies/ml",
    "mg",
    "mg/dl",
    "iu/ml",
    "cells/mcl",
    "mmol/l",
    "u/l",
    "g/dl",
    "ng/ml",
    "pg/ml",
    "mcg",
    "units/ml",
}


class Detector(Protocol):
    def detect(self, text: str, policy: ObfuscationPolicy) -> list[DetectedEntity]:
        ...


def perform_coreference_clustering(entities: list[DetectedEntity]) -> None:
    """String-level coreference clustering (review finding R1).

    Groups surface forms of the same entity (e.g. "John Smith", "John", "Mr. Smith")
    and assigns a cluster_id equal to the longest surface form in the cluster.
    """
    names = [e for e in entities if e.entity_type is EntityType.NAME]
    if not names:
        return

    names_by_len = sorted(names, key=lambda e: len(e.text), reverse=True)
    clusters: list[tuple[str, list[DetectedEntity]]] = []

    for entity in names_by_len:
        text_clean = entity.text.strip().removeprefix("Mr. ").removeprefix("Ms. ").removeprefix("Dr. ")
        matched_cluster = None
        for canonical, members in clusters:
            canon_clean = canonical.strip().removeprefix("Mr. ").removeprefix("Ms. ").removeprefix("Dr. ")
            if text_clean in canon_clean or canon_clean in text_clean:
                matched_cluster = (canonical, members)
                break
        if matched_cluster:
            matched_cluster[1].append(entity)
            entity.cluster_id = matched_cluster[0]
        else:
            clusters.append((entity.text, [entity]))
            entity.cluster_id = entity.text


def apply_unit_adjacency_carveout(text: str, entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Magnitude rule carve-out: keep a 5+ run beside a clinical unit."""
    surviving = []
    text_lower = text.lower()

    for entity in entities:
        if entity.entity_type in (EntityType.GENERIC_ID, EntityType.ACCOUNT, EntityType.SSN):
            post_text = text_lower[entity.end : entity.end + 20].strip()
            is_clinical = any(post_text.startswith(unit) for unit in CLINICAL_UNITS)
            if is_clinical:
                continue  # Skip scrubbing — it's a lab value!
        surviving.append(entity)
    return surviving


class RuleEngineDetector:
    """Bespoke regex detector fallback (used if Presidio is absent)."""

    PATTERNS: list[tuple[EntityType, re.Pattern, float]] = [
        (EntityType.SSN, re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), 0.95),
        (EntityType.EMAIL, re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), 0.95),
        (EntityType.PHONE, re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), 0.90),
        (EntityType.MRN, re.compile(r"\b(?:MRN|Medical Record Number|Chart)[:\s]*#?\s*([A-Za-z0-9_-]{5,12})\b", re.I), 0.95),
        (EntityType.ACCOUNT, re.compile(r"\b(?:Account|Acct)[:\s]*#?\s*([A-Za-z0-9_-]{5,12})\b", re.I), 0.90),
        (EntityType.ZIP, re.compile(r"\b\d{5}(?:-\d{4})?\b"), 0.85),
        (EntityType.CREDIT_CARD, re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), 0.95),
        (EntityType.IP, re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), 0.90),
        (EntityType.DOB, re.compile(r"\b(?:DOB|Date of Birth|Born)[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", re.I), 0.95),
        (EntityType.DATE, re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4})\b", re.I), 0.85),
        (EntityType.AGE, re.compile(r"\b(?:age\s+)?(\d{1,3})\s*(?:years?\s*old|y/o|yo)\b", re.I), 0.90),
        (EntityType.NAME, re.compile(r"\b(?:Patient|Dr\.|Mr\.|Ms\.|Mrs\.|Subject|Client)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b"), 0.90),
    ]

    MAGNITUDE_PATTERN = re.compile(r"\b(?:0\d+|\d{5,})\b")

    def detect(self, text: str, policy: ObfuscationPolicy) -> list[DetectedEntity]:
        entities: list[DetectedEntity] = []

        for etype, pattern, conf in self.PATTERNS:
            for match in pattern.finditer(text):
                if match.groups():
                    start, end = match.span(1)
                    val = match.group(1)
                else:
                    start, end = match.span()
                    val = match.group(0)
                entities.append(
                    DetectedEntity(
                        start=start,
                        end=end,
                        entity_type=etype,
                        text=val,
                        confidence=conf,
                        source="rule_engine",
                    )
                )

        for match in self.MAGNITUDE_PATTERN.finditer(text):
            start, end = match.span()
            val = match.group(0)
            if not any(e.start <= start and e.end >= end for e in entities):
                entities.append(
                    DetectedEntity(
                        start=start,
                        end=end,
                        entity_type=EntityType.GENERIC_ID,
                        text=val,
                        confidence=0.70,
                        source="magnitude_rule",
                    )
                )

        entities = apply_unit_adjacency_carveout(text, entities)
        perform_coreference_clustering(entities)

        return entities


class PresidioDetector:
    """Primary detector substrate using Microsoft Presidio."""

    def __init__(self) -> None:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer

        self.analyzer = AnalyzerEngine()
        self.fallback = RuleEngineDetector()

        mrn_recognizer = PatternRecognizer(
            supported_entity="MRN",
            patterns=[Pattern("mrn_pattern", r"\b(?:MRN|Medical Record Number|Chart)[:\s]*#?\s*([A-Za-z0-9_-]{5,12})\b", 0.95)],
            supported_language="en",
        )
        account_recognizer = PatternRecognizer(
            supported_entity="ACCOUNT",
            patterns=[Pattern("account_pattern", r"\b(?:Account|Acct)[:\s]*#?\s*([A-Za-z0-9_-]{5,12})\b", 0.90)],
            supported_language="en",
        )
        self.analyzer.registry.add_recognizer(mrn_recognizer)
        self.analyzer.registry.add_recognizer(account_recognizer)

        self.ENTITY_MAPPING = {
            "PERSON": EntityType.NAME,
            "US_SSN": EntityType.SSN,
            "PHONE_NUMBER": EntityType.PHONE,
            "EMAIL_ADDRESS": EntityType.EMAIL,
            "DATE_TIME": EntityType.DATE,
            "LOCATION": EntityType.ADDRESS,
            "IP_ADDRESS": EntityType.IP,
            "CREDIT_CARD": EntityType.CREDIT_CARD,
            "MRN": EntityType.MRN,
            "ACCOUNT": EntityType.ACCOUNT,
            "US_PASSPORT": EntityType.GENERIC_ID,
            "US_DRIVER_LICENSE": EntityType.GENERIC_ID,
        }

    def detect(self, text: str, policy: ObfuscationPolicy) -> list[DetectedEntity]:
        entities: list[DetectedEntity] = []

        try:
            results = self.analyzer.analyze(text=text, language="en")
            for res in results:
                if res.score < 0.1:
                    continue
                etype = self.ENTITY_MAPPING.get(res.entity_type, EntityType.GENERIC_ID)
                val = text[res.start : res.end]
                entities.append(
                    DetectedEntity(
                        start=res.start,
                        end=res.end,
                        entity_type=etype,
                        text=val,
                        confidence=res.score,
                        source="presidio",
                    )
                )
        except Exception:
            return self.fallback.detect(text, policy)

        fallback_entities = self.fallback.detect(text, policy)
        for fe in fallback_entities:
            overlapping = [e for e in entities if e.overlaps(fe)]
            if not overlapping:
                entities.append(fe)
            else:
                max_conf = max(e.confidence for e in overlapping)
                if fe.confidence > max_conf:
                    for e in overlapping:
                        entities.remove(e)
                    entities.append(fe)

        entities = apply_unit_adjacency_carveout(text, entities)
        perform_coreference_clustering(entities)

        return entities


def create_detector() -> Detector:
    try:
        import presidio_analyzer  # noqa: F401

        return PresidioDetector()
    except ImportError:
        return RuleEngineDetector()
