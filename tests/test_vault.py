"""Vault: determinism, non-linkability, one-wayness, crypto-shred."""

from __future__ import annotations

import os

from secure_context_pipeline.vault.keyring import KeyRing


async def test_deterministic_in_session_and_document(manager):
    s = manager.create_session("u")
    assert s.token_for("john smith", "NAME", "d1") == s.token_for("john smith", "NAME", "d1")


async def test_cross_document_non_linkability(manager):
    s = manager.create_session("u")
    assert s.token_for("john smith", "NAME", "d1") != s.token_for("john smith", "NAME", "d2")


async def test_cross_session_non_linkability(manager):
    a, b = manager.create_session("u"), manager.create_session("u")
    assert a.token_for("john smith", "NAME", "d1") != b.token_for("john smith", "NAME", "d1")


async def test_vault_roundtrip(manager):
    s = manager.create_session("u")
    t = s.token_for("john smith", "NAME", "d1")
    await s.vault.store(t, "John Smith", "NAME")
    assert await s.vault.resolve(t) == "John Smith"


async def test_crypto_shred_on_destroy(manager):
    s = manager.create_session("u")
    t = s.token_for("x", "NAME", "d1")
    await s.vault.store(t, "X", "NAME")
    await s.destroy()
    assert await s.vault.resolve(t) is None


def test_keyring_digest_is_one_way():
    kr = KeyRing(os.urandom(32))
    digest = kr.token_digest("super secret value")
    assert "secret" not in digest and len(digest) == 64
