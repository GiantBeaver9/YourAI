"""Vault & session — the 30% security story: determinism, non-linkability, crypto-shred."""

from __future__ import annotations

import pytest

from secure_context_pipeline.grammar import TOKEN_REGEX
from secure_context_pipeline.vault.session import SessionClosed, SessionManager


def test_token_deterministic_within_session_and_document(session):
    a = session.token_for("Jonathan Reyes", "NAME", "doc-1")
    b = session.token_for("Jonathan Reyes", "NAME", "doc-1")
    assert a == b
    assert TOKEN_REGEX.fullmatch(a)


def test_token_differs_across_documents(session):
    a = session.token_for("Jonathan Reyes", "NAME", "doc-1")
    b = session.token_for("Jonathan Reyes", "NAME", "doc-2")
    assert a != b  # per-document k_doc -> cross-document non-linkability


def test_token_differs_across_sessions(manager):
    s1 = manager.create_session("user-a")
    s2 = manager.create_session("user-b")
    assert s1.token_for("Jonathan Reyes", "NAME", "d") != s2.token_for("Jonathan Reyes", "NAME", "d")


async def test_vault_roundtrip(session):
    tok = session.token_for("482-19-7734", "SSN", "d")
    await session.vault.store(tok, "482-19-7734", "SSN")
    assert await session.vault.resolve(tok) == "482-19-7734"


async def test_vault_miss_returns_none(session):
    assert await session.vault.resolve("[SSN_deadbeef0000]") is None


async def test_crypto_shred_makes_vault_unresolvable(session):
    tok = session.token_for("secret", "NAME", "d")
    await session.vault.store(tok, "secret", "NAME")
    assert await session.vault.resolve(tok) == "secret"
    await session.destroy()
    # After destroy the key ring is zeroized and the map crypto-shredded.
    assert await session.vault.resolve(tok) is None


async def test_keyring_destroyed_blocks_token_derivation(session):
    await session.destroy()
    with pytest.raises(RuntimeError):
        session.keyring.token_digest("x", "d")


async def test_lease_rejected_after_destroy(session):
    await session.destroy()
    with pytest.raises(SessionClosed):
        async with session.lease():
            pass


async def test_expired_session_lease_fails():
    mgr = SessionManager(ttl_seconds=0)  # already expired
    s = mgr.create_session("u")
    assert s.is_expired()
    with pytest.raises(SessionClosed):
        async with s.lease():
            pass


async def test_concurrent_sessions_isolated(manager):
    a = manager.create_session("user-a")
    b = manager.create_session("user-b")
    tok_a = a.token_for("Jonathan Reyes", "NAME", "d")
    await a.vault.store(tok_a, "Jonathan Reyes", "NAME")
    # B cannot resolve A's token (different key space, and B never stored it).
    assert await b.vault.resolve(tok_a) is None
