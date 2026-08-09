"""Detection: identifier coverage (both substrates), magnitude rule, coreference."""

from __future__ import annotations

from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.detection.coreference import (
    cluster_names,
    propagate_names,
    resolve_overlaps,
)
from secure_context_pipeline.detection.native import RuleEngineDetector
from secure_context_pipeline.entities import EntityType


def _resolved(det, text):
    return resolve_overlaps(det.detect(text))


def test_detects_core_identifiers(detector):
    text = (
        "Patient: John Smith\nSSN: 123-45-6789\nMRN: 4457812\n"
        "Email: john@example.com\nPhone: (415) 555-0132"
    )
    found = {e.entity_type for e in _resolved(detector, text)}
    for et in (EntityType.NAME, EntityType.SSN, EntityType.MRN, EntityType.EMAIL, EntityType.PHONE):
        assert et in found, f"{et} not detected by {detector.name}"


def test_magnitude_keeps_unit_adjacent_value(policy):
    det = RuleEngineDetector(policy)
    ents = _resolved(det, "HIV-1 RNA 100000 copies/mL. Account No: 998877665")
    # The viral load (unit-adjacent) is NOT scrubbed; the bare long number IS.
    assert not any(e.text == "100000" for e in ents)
    assert any("998877665" in e.text for e in ents)


def test_magnitude_scrubs_leading_zero_and_keeps_short_dose(policy):
    det = RuleEngineDetector(policy)
    ents = _resolved(det, "Badge 0042 issued. Dose 500 mg administered.")
    texts = {e.text for e in ents}
    assert "0042" in texts        # leading zero -> identifier
    assert "500" not in texts     # short, unit-adjacent dose -> kept


def test_coreference_collapses_mentions(policy):
    det = RuleEngineDetector(policy)
    text = "Patient: John Smith\nMr. Smith arrived early. John was seen at noon."
    ents = resolve_overlaps(propagate_names(text, _resolved(det, text)))
    canon = cluster_names(ents)
    name_clusters = {e.cluster_id for e in ents if e.entity_type is EntityType.NAME}
    assert len(name_clusters) == 1                     # all mentions -> one person
    assert list(canon.values()) == ["John Smith"]      # canonical is the full name


def test_coreference_does_not_merge_distinct_people(policy):
    det = RuleEngineDetector(policy)
    text = "Patient: John Smith. Contact: Jane Smith. Smith called later."
    ents = resolve_overlaps(propagate_names(text, _resolved(det, text)))
    canon = cluster_names(ents)
    values = set(canon.values())
    assert "John Smith" in values and "Jane Smith" in values
    # the ambiguous bare "Smith" gets its OWN cluster, never bridging the two people
    assert len({v for v in canon.values()}) >= 3


def test_propagation_catches_bare_surname_in_prose(policy):
    det = RuleEngineDetector(policy)
    text = "Patient: John Smith\nSmith tolerated the regimen well."
    before = _resolved(det, text)
    after = resolve_overlaps(propagate_names(text, before))
    # the second "Smith" (missed initially) is now a NAME span
    smith_spans = [e for e in after if e.entity_type is EntityType.NAME and "Smith" in e.text]
    assert len(smith_spans) >= 2
