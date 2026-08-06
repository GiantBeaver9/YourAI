"""The five required scenarios + the full round-trip."""

from __future__ import annotations

import asyncio

import pytest

from secure_context_pipeline.deobfuscation import Deobfuscator
from secure_context_pipeline.grammar import TOKEN_REGEX
from secure_context_pipeline.pipeline import SecureContextPipeline
from secure_context_pipeline.vault.session import SessionClosed

from .conftest import make_document


async def test_happy_path(manager):
    session = manager.create_session("dr_a")
    doc, must_not_leak = make_document(1)
    result = await SecureContextPipeline().process(session, doc, "Summarize.", "d1")

    for value in must_not_leak:
        assert value not in result.obfuscated, f"leaked to LLM: {value!r}"
    assert not TOKEN_REGEX.search(result.restored), "a token reached the user"
    assert result.leftover_tokens == []
    assert "Han Chinese" in result.obfuscated  # clinical signal transits


async def test_entity_type_not_found(manager):
    session = manager.create_session("dr_a")
    result = await SecureContextPipeline().process(
        session, "The quarterly report is due next week.", "Summarize.", "d1"
    )
    assert result.tokens == 0
    assert result.leftover_tokens == []


async def test_vault_miss_on_deobfuscation(manager):
    session = manager.create_session("dr_a")
    deobf = Deobfuscator(session.vault)
    res = await deobf.restore("The result for [NAME_deadbeef0000] is ready.")
    assert res.leftover_tokens == ["[NAME_deadbeef0000]"]  # reported, not guessed
    assert not TOKEN_REGEX.search(res.text)               # no raw token survives
    assert "[UNRESTORED]" in res.text


async def test_expired_session(manager):
    session = manager.create_session("dr_a")
    session.expires_at = 0  # force expiry
    doc, _ = make_document(2)
    with pytest.raises(SessionClosed):
        await SecureContextPipeline().process(session, doc, "Summarize.", "d1")


async def test_concurrent_session_isolation(manager):
    doc, _ = make_document(3)
    a = manager.create_session("dr_a")
    b = manager.create_session("dr_a")  # same user, different session
    pipe = SecureContextPipeline()

    ra, rb = await asyncio.gather(
        pipe.process(a, doc, "Summarize.", "d1"),
        pipe.process(b, doc, "Summarize.", "d1"),
    )
    # same input value -> different tokens across sessions (fresh K_s each)
    assert ra.obfuscated != rb.obfuscated
    # session B cannot resolve session A's token
    a_token = TOKEN_REGEX.search(ra.obfuscated).group(0)
    assert await b.vault.resolve(a_token) is None
    assert await a.vault.resolve(a_token) is not None


async def test_destroy_on_logout_crypto_shreds(manager):
    session = manager.create_session("dr_a")
    doc, _ = make_document(4)
    result = await SecureContextPipeline().process(session, doc, "Summarize.", "d1")
    token = TOKEN_REGEX.search(result.obfuscated).group(0)
    assert await session.vault.resolve(token) is not None
    await manager.destroy(session.session_id)
    assert await session.vault.resolve(token) is None  # shredded
