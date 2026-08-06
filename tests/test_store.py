"""Encrypted store: envelope round-trip, per-user isolation, tamper/slot-bind resistance."""

from __future__ import annotations

import pytest

from secure_context_pipeline.store.store import EncryptedDocumentStore, StoreError


def _store(tmp_path) -> EncryptedDocumentStore:
    return EncryptedDocumentStore(master_key=b"\x03" * 32, store_root=str(tmp_path))


async def test_roundtrip(tmp_path):
    s = _store(tmp_path)
    await s.put("user-a", "doc-1", "secret note")
    assert await s.get("user-a", "doc-1") == "secret note"


async def test_only_ciphertext_on_disk(tmp_path):
    s = _store(tmp_path)
    path = await s.put("user-a", "doc-1", "PLAINTEXT-MARKER")
    with open(path, "rb") as fh:
        blob = fh.read()
    assert b"PLAINTEXT-MARKER" not in blob


async def test_cross_user_read_fails(tmp_path):
    s = _store(tmp_path)
    await s.put("user-a", "doc-1", "a's data")
    with pytest.raises(StoreError):
        await s.get("user-b", "doc-1")  # different KEK + AAD -> auth failure


async def test_wrong_doc_id_fails(tmp_path):
    s = _store(tmp_path)
    await s.put("user-a", "doc-1", "data")
    with pytest.raises(StoreError):
        await s.get("user-a", "doc-2")


async def test_tamper_detected(tmp_path):
    s = _store(tmp_path)
    path = await s.put("user-a", "doc-1", "data")
    with open(path, "rb") as fh:
        blob = bytearray(fh.read())
    blob[-1] ^= 0x01  # flip a ciphertext bit
    with open(path, "wb") as fh:
        fh.write(blob)
    with pytest.raises(StoreError):
        await s.get("user-a", "doc-1")


async def test_blob_swap_fails(tmp_path):
    """Move user-a/doc-1 ciphertext into user-a/doc-2's slot -> AAD bind breaks."""
    s = _store(tmp_path)
    p1 = await s.put("user-a", "doc-1", "one")
    await s.put("user-a", "doc-2", "two")
    p2 = s._path("user-a", "doc-2")  # noqa: SLF001 (test reaches for the slot path)
    with open(p1, "rb") as fh:
        stolen = fh.read()
    with open(p2, "wb") as fh:
        fh.write(stolen)
    with pytest.raises(StoreError):
        await s.get("user-a", "doc-2")
