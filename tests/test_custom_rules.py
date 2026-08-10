"""Operator-authored custom rules: loading, validation, and effect on detection."""

from __future__ import annotations

import json

from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.detection.coreference import resolve_overlaps
from secure_context_pipeline.detection.native import RuleEngineDetector
from secure_context_pipeline.detection.rules import load_custom_rules
from secure_context_pipeline.entities import EntityType


def _write(tmp_path, obj) -> str:
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_load_valid_rules(tmp_path):
    path = _write(tmp_path, {"rules": [
        {"id": "proj", "entity_type": "GENERIC_ID", "regex": r"PRJ-\d{4}"},
    ]})
    rules = load_custom_rules(path)
    assert len(rules) == 1 and rules[0].source == "custom"
    assert rules[0].entity_type is EntityType.GENERIC_ID


def test_bad_regex_is_skipped_not_crash(tmp_path):
    path = _write(tmp_path, {"rules": [
        {"id": "bad", "regex": "("},                 # unbalanced -> skipped
        {"id": "good", "regex": r"OK-\d+"},
    ]})
    rules = load_custom_rules(path)
    assert [r.id for r in rules] == ["good"]


def test_unknown_entity_type_falls_back_to_generic(tmp_path):
    path = _write(tmp_path, {"rules": [{"id": "x", "entity_type": "NOPE", "regex": "z+"}]})
    assert load_custom_rules(path)[0].entity_type is EntityType.GENERIC_ID


def test_missing_rules_file_does_not_crash_pipeline():
    # A misconfigured SCP_CUSTOM_RULES_PATH must not take down the service.
    from secure_context_pipeline import SecureContextPipeline, Settings
    from secure_context_pipeline.llm.provider import MockProvider

    settings = Settings(master_key=b"\x0a" * 32, custom_rules_path="does_not_exist.json")
    pipe = SecureContextPipeline(
        settings=settings, detector=RuleEngineDetector(ObfuscationPolicy()),
        provider=MockProvider(),
    )
    assert pipe.custom_rules == []


def test_custom_rule_detects_new_pattern(tmp_path):
    path = _write(tmp_path, {"rules": [
        {"id": "proj", "entity_type": "GENERIC_ID", "regex": r"PRJ-\d{4}", "confidence": 0.9},
    ]})
    rules = load_custom_rules(path)
    det = RuleEngineDetector(ObfuscationPolicy(), custom_rules=rules)
    ents = resolve_overlaps(det.detect("Ref PRJ-2041 filed."))
    assert any(e.text == "PRJ-2041" for e in ents)
