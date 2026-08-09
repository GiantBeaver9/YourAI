"""De-obfuscation: round-trip, affix/case tolerance, leftover guard, vault-miss redaction."""

from __future__ import annotations

from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.deobfuscation.deobfuscator import Deobfuscator
from secure_context_pipeline.detection.native import RuleEngineDetector
from secure_context_pipeline.obfuscation.engine.engine import ObfuscationEngine


async def _tok(session, value, tag="NAME", doc="d"):
    token = session.token_for(value, tag, doc)
    await session.vault.store(token, value, tag)
    return token


async def test_round_trip_identity(session, policy):
    # Single-occurrence identifiers, no dates -> obfuscate then restore == original.
    det = RuleEngineDetector(policy)
    eng = ObfuscationEngine(det, policy)
    text = "Patient: Alice Wonderland\nSSN: 111-22-3333\nEmail: alice@example.com"
    res = await eng.obfuscate(text, session, "d")
    restored = await Deobfuscator().restore(res.obfuscated_text, session)
    assert restored.restored_text == text
    assert restored.clean


async def test_affix_tolerant_restore(session):
    tok = await _tok(session, "Alice", "NAME")
    reply = f"{tok}'s chart and ({tok}) were reviewed. See {tok}."
    out = await Deobfuscator().restore(reply, session)
    assert out.restored_text == "Alice's chart and (Alice) were reviewed. See Alice."
    assert out.clean


async def test_case_mangled_token_restored(session):
    tok = await _tok(session, "Bob", "NAME")
    mangled = tok.lower()  # model lower-cased the whole token
    out = await Deobfuscator().restore(f"Patient {mangled} seen.", session)
    assert out.restored_text == "Patient Bob seen."
    assert out.clean


async def test_vault_miss_redacts_and_flags(session):
    from secure_context_pipeline.grammar import make_token
    unknown = make_token("SSN", "abcabcabcabc")  # never stored
    out = await Deobfuscator().restore(f"Value {unknown} here.", session)
    assert unknown not in out.restored_text
    assert "[redacted]" in out.restored_text
    assert out.vault_misses == ["SSN"]
    assert not out.clean


async def test_leftover_guard_never_ships_a_token(session):
    # A residue that survives resolution must be redacted; output is token-free.
    from secure_context_pipeline.grammar import has_token_residue, make_token
    unknown = make_token("MRN", "0f0f0f0f0f0f")
    out = await Deobfuscator().restore(f"see {unknown}", session)
    assert not has_token_residue(out.restored_text)


async def test_cross_session_token_is_a_vault_miss(manager):
    a = manager.create_session("a")
    b = manager.create_session("b")
    tok = await _tok(a, "Carol", "NAME")
    # B replays A's token -> different key space -> miss -> redacted, never A's value
    out = await Deobfuscator().restore(f"hello {tok}", b)
    assert "Carol" not in out.restored_text
    assert "[redacted]" in out.restored_text
