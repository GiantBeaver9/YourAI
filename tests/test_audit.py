"""Audit trail is PHI-free and structurally complete."""

from __future__ import annotations

import json

from secure_context_pipeline.audit.audit import AuditLog
from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.detection.native import RuleEngineDetector
from secure_context_pipeline.fixtures import FIXTURE_PII
from secure_context_pipeline.fixtures.clinical_note import FIXTURE_TEXT
from secure_context_pipeline.obfuscation.engine.engine import ObfuscationEngine


async def test_audit_contains_no_pii(session):
    pol = ObfuscationPolicy()
    audit = AuditLog()
    eng = ObfuscationEngine(RuleEngineDetector(pol), pol, audit=audit)
    await eng.obfuscate(FIXTURE_TEXT, session, "doc")
    dump = audit.as_jsonl()
    assert dump  # events were emitted
    for value in FIXTURE_PII.values():
        assert value not in dump, f"audit leaked {value!r}"


async def test_audit_event_shape(session):
    pol = ObfuscationPolicy()
    audit = AuditLog()
    eng = ObfuscationEngine(RuleEngineDetector(pol), pol, audit=audit)
    await eng.obfuscate("SSN: 123-45-6789", session, "doc")
    ev = json.loads(audit.as_jsonl().splitlines()[0])
    assert set(ev) >= {"ts", "session_id", "entity_type", "action", "token", "policy_version"}
    assert ev["policy_version"] == "v1"
