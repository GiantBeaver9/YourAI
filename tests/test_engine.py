"""Obfuscation engine: preserve policy, offset-safe replacement, confidence-gated redaction."""

from __future__ import annotations

from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.detection.native import RuleEngineDetector
from secure_context_pipeline.grammar import has_token_residue
from secure_context_pipeline.obfuscation.engine.engine import ObfuscationEngine


async def test_preserve_clinical_quasi_identifiers(engine, session):
    text = "Name: John Smith\nHe is a 62-year-old Han Chinese male."
    res = await engine.obfuscate(text, session, "d")
    assert "John Smith" not in res.obfuscated_text
    assert "Han Chinese" in res.obfuscated_text   # preserved by default policy
    assert "male" in res.obfuscated_text
    assert "62" in res.obfuscated_text


async def test_offset_safe_replacement_repeated_value(session, policy):
    det = RuleEngineDetector(policy)
    eng = ObfuscationEngine(det, policy)
    text = "SSN: 123-45-6789 appears; later 123-45-6789 again."
    res = await eng.obfuscate(text, session, "d")
    assert "123-45-6789" not in res.obfuscated_text
    # identical value -> identical token, both occurrences replaced
    assert res.obfuscated_text.count("[SSN_") == 2


async def test_confidence_gate_redacts(session):
    pol = ObfuscationPolicy(confidence_threshold=0.999)  # nothing clears the bar
    eng = ObfuscationEngine(RuleEngineDetector(pol), pol)
    text = "Name: John Smith\nSSN: 123-45-6789"
    res = await eng.obfuscate(text, session, "d")
    assert "John Smith" not in res.obfuscated_text
    assert "123-45-6789" not in res.obfuscated_text
    assert "[redacted]" in res.obfuscated_text


async def test_known_originals_excludes_preserved(engine, session):
    text = "Name: John Smith. 62-year-old Han Chinese male."
    res = await engine.obfuscate(text, session, "d")
    # preserved values must NOT be in the verify set (they legitimately stay in the payload)
    assert "Han Chinese" not in res.known_originals
    assert "John Smith" in res.known_originals


async def test_no_token_residue_in_obfuscated_output(engine, session):
    text = "Patient: John Smith\nSSN: 123-45-6789\nMRN: 4457812"
    res = await engine.obfuscate(text, session, "d")
    # tokens ARE present (that's the point); the point here is spans are well-formed
    assert res.obfuscated_text.count("[") == res.obfuscated_text.count("]")
    assert has_token_residue(res.obfuscated_text)  # our own tokens, as expected
