"""Encrypted store isolation + audit-is-PHI-free."""

from __future__ import annotations

import json
import os

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from secure_context_pipeline.audit import AuditLog
from secure_context_pipeline.pipeline import SecureContextPipeline
from secure_context_pipeline.store import EncryptedStore

from .conftest import make_document


async def test_store_roundtrip(tmp_path):
    store = EncryptedStore(os.urandom(32), root=str(tmp_path))
    await store.put("alice", "d1", b"SSN 123-45-6789")
    assert await store.get("alice", "d1") == b"SSN 123-45-6789"


async def test_ciphertext_on_disk(tmp_path):
    store = EncryptedStore(os.urandom(32), root=str(tmp_path))
    await store.put("alice", "d1", b"123-45-6789 secret")
    raw = open(store._path("alice", "d1"), "rb").read()
    assert b"123-45-6789" not in raw


async def test_cross_user_key_isolation(tmp_path):
    store = EncryptedStore(os.urandom(32), root=str(tmp_path))
    await store.put("alice", "d1", b"phi")
    blob = json.load(open(store._path("alice", "d1")))
    # bob's KEK cannot unwrap alice's DEK
    with pytest.raises(InvalidTag):
        AESGCM(store._user_kek("bob")).decrypt(
            bytes.fromhex(blob["wrap_nonce"]),
            bytes.fromhex(blob["wrapped_dek"]),
            store._aad("alice", "d1"),
        )


async def test_audit_never_contains_original_values(manager):
    session = manager.create_session("u")
    audit = AuditLog()
    doc, must_not_leak = make_document(7)
    await SecureContextPipeline(audit=audit).process(session, doc, "Summarize.", "d1")
    blob = json.dumps([e.__dict__ for e in audit.events])
    for value in must_not_leak:
        if len(value) >= 3:
            assert value not in blob, f"audit leaked {value!r}"
    assert audit.events, "no audit events recorded"
