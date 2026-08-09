"""Regression tests for issues found in code review of the initial implementation."""

from __future__ import annotations

from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.deobfuscation.deobfuscator import Deobfuscator
from secure_context_pipeline.detection.coreference import propagate_names, resolve_overlaps
from secure_context_pipeline.detection.native import RuleEngineDetector
from secure_context_pipeline.entities import Action, EntityType
from secure_context_pipeline.llm.injector import ContextInjector
from secure_context_pipeline.obfuscation.engine.engine import ObfuscationEngine
from secure_context_pipeline.vault.session import SessionManager, SessionState


def test_verify_does_not_false_block_on_substring():
    # A known name part ("John") must not be found inside an unrelated word ("Johnson").
    inj = ContextInjector()
    req = inj.build_request("Dr. Johnson reviewed [NAME_0123456789ab].", "t", {"John", "Smith"})
    assert "Johnson" in req.context  # not blocked


def test_verify_still_blocks_identifier_fragment():
    import pytest
    from secure_context_pipeline.llm.injector import VerifyBeforeSendError

    inj = ContextInjector()
    with pytest.raises(VerifyBeforeSendError):
        inj.build_request("id=482-19-7734x", "t", {"482-19-7734"})  # digit value -> substring


async def test_expired_session_is_crypto_shredded_by_reaper():
    mgr = SessionManager(ttl_seconds=0)
    s = mgr.create_session("u")
    tok = s.token_for("secret", "NAME", "d")
    await s.vault.store(tok, "secret", "NAME")
    reaped = await mgr.reap_expired()
    assert reaped == 1
    assert s.state is SessionState.DESTROYED
    assert await s.vault.resolve(tok) is None  # map crypto-shredded


async def test_common_word_name_not_propagated_into_prose(session, policy):
    det = RuleEngineDetector(policy)
    text = "Patient: Will Green\nWill the patient return next week?"
    ents = resolve_overlaps(propagate_names(text, resolve_overlaps(det.detect(text))))
    # The modal verb "Will the" must NOT become a NAME span (only the labeled "Will Green" does).
    verb_span = next((e for e in ents if e.start == text.index("Will the")), None)
    assert verb_span is None or verb_span.entity_type is not EntityType.NAME


async def test_pseudonym_restore_handles_backslash_value(session):
    pol = ObfuscationPolicy(routing_overrides={EntityType.ADDRESS: Action.PSEUDONYMIZE})
    # store a pseudonym whose ORIGINAL contains a regex-replacement metacharacter
    fake = "Fakeville"
    await session.vault.store(fake, r"1420 O\Brien St", EntityType.ADDRESS.tag)
    out = await Deobfuscator().restore(f"lives in {fake} now", session,
                                       used_pseudonymization=True)
    assert out.restored_text == r"lives in 1420 O\Brien St now"  # literal, no re.error
