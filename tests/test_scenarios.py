"""The five required scenarios, end-to-end through the pipeline.

1. happy path            2. entity-not-found      3. vault miss
4. expired session       5. concurrent session isolation
"""

from __future__ import annotations

import pytest

from secure_context_pipeline import ObfuscationPolicy, SecureContextPipeline, Settings
from secure_context_pipeline.deobfuscation.deobfuscator import Deobfuscator
from secure_context_pipeline.detection.native import RuleEngineDetector
from secure_context_pipeline.fixtures import FIXTURE_DOC_ID, FIXTURE_PII
from secure_context_pipeline.fixtures.clinical_note import FIXTURE_TEXT
from secure_context_pipeline.grammar import TOKEN_REGEX
from secure_context_pipeline.llm.provider import MockProvider
from secure_context_pipeline.vault.session import SessionClosed, SessionManager


def _pipeline(tmp_path) -> SecureContextPipeline:
    settings = Settings(master_key=b"\x05" * 32, store_root=str(tmp_path))
    return SecureContextPipeline(
        settings=settings, policy=ObfuscationPolicy(),
        detector=RuleEngineDetector(ObfuscationPolicy()), provider=MockProvider(),
    )


# 1 -------------------------------------------------------------------------------------------
async def test_happy_path(tmp_path):
    pipe = _pipeline(tmp_path)
    sess = pipe.sessions.create_session("u")
    res = await pipe.process(sess, FIXTURE_DOC_ID, "Summarize the patient.", text=FIXTURE_TEXT)
    for value in FIXTURE_PII.values():
        assert value not in res.obfuscation.obfuscated_text
    assert res.restored_text
    assert res.deobfuscation.clean


# 2 -------------------------------------------------------------------------------------------
async def test_entity_not_found(tmp_path):
    pipe = _pipeline(tmp_path)
    sess = pipe.sessions.create_session("u")
    clean = "the patient is doing well today and reports no new complaints at all."
    res = await pipe.process(sess, "doc-clean", "Summarize.", text=clean)
    assert not TOKEN_REGEX.search(res.obfuscation.obfuscated_text)  # nothing to obfuscate
    assert res.obfuscation.obfuscated_text == clean


# 3 -------------------------------------------------------------------------------------------
async def test_vault_miss(tmp_path):
    pipe = _pipeline(tmp_path)
    sess = pipe.sessions.create_session("u")
    from secure_context_pipeline.grammar import make_token

    unknown = make_token("NAME", "aaaabbbbcccc")
    out = await Deobfuscator().restore(f"Reviewed {unknown}.", sess)
    assert unknown not in out.restored_text
    assert out.vault_misses == ["NAME"]
    assert not out.clean


# 4 -------------------------------------------------------------------------------------------
async def test_expired_session(tmp_path):
    settings = Settings(master_key=b"\x05" * 32, store_root=str(tmp_path))
    pipe = SecureContextPipeline(settings=settings, provider=MockProvider(),
                                 detector=RuleEngineDetector(ObfuscationPolicy()))
    pipe.sessions = SessionManager(ttl_seconds=0)
    sess = pipe.sessions.create_session("u")
    with pytest.raises(SessionClosed):
        await pipe.process(sess, "doc", "Summarize.", text="Name: John Smith")


# 5 -------------------------------------------------------------------------------------------
async def test_concurrent_session_isolation(tmp_path):
    pipe = _pipeline(tmp_path)
    a = pipe.sessions.create_session("user-a")
    b = pipe.sessions.create_session("user-b")
    text = "Patient: John Smith\nSSN: 123-45-6789"

    ra = await pipe.process(a, "doc", "Summarize.", text=text)
    rb = await pipe.process(b, "doc", "Summarize.", text=text)

    # Same value -> different tokens across sessions (fresh K_s each).
    ta = TOKEN_REGEX.findall(ra.obfuscation.obfuscated_text)
    tb = TOKEN_REGEX.findall(rb.obfuscation.obfuscated_text)
    assert set(ta).isdisjoint(set(tb))

    # B cannot resolve A's token.
    a_token = TOKEN_REGEX.search(ra.obfuscation.obfuscated_text).group(0)
    out = await Deobfuscator().restore(f"See {a_token}", b)
    assert "[redacted]" in out.restored_text
    assert "John" not in out.restored_text
