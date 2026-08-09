"""Encrypted document store — envelope encryption, per-user isolation.

Key hierarchy: **master key (env/KMS) → per-user KEK → per-document DEK**, each derived by HKDF
with domain separation. Every ciphertext is AES-256-GCM with **AAD = (user_id, doc_id,
key_version)**, which is what makes the store resist more than a naive "encrypt the blob":

* **Stolen DB dump** → ciphertext only; without the master key nothing decrypts.
* **Cross-user read** → user B's KEK can't derive user A's DEK, and the AAD wouldn't match
  even if it could — decryption fails cryptographically, not by a permission check.
* **Blob-swap / replay** → moving a ciphertext to a different (user, doc) slot breaks the AAD
  bind, so GCM authentication fails.

This is a distinct hierarchy from the session vault's ephemeral ``K_s``: the store key is
*durable* (documents outlive sessions), the session key is *ephemeral* (crypto-shredded on
logout). Deliberately independent — see session-model.md. The demo master key comes from env;
the prod swap is a KMS/HSM-held key (designed-not-built). Extracted plaintext is never persisted
unencrypted — only ciphertext ever hits disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_BYTES = 12


class StoreError(RuntimeError):
    """Raised on a missing document or a failed authentication (tamper / wrong key / wrong slot)."""


class EncryptedDocumentStore:
    def __init__(self, master_key: bytes, store_root: str, key_version: str = "v1") -> None:
        if len(master_key) < 32:
            raise ValueError("master key must be >= 32 bytes")
        self._master = master_key
        self._root = store_root
        self._key_version = key_version
        os.makedirs(store_root, exist_ok=True)

    # -- key derivation --------------------------------------------------------------------
    def _user_kek(self, user_id: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None,
            info=b"scp/store/kek/" + user_id.encode("utf-8"),
        ).derive(self._master)

    def _doc_dek(self, user_id: str, doc_id: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None,
            info=b"scp/store/dek/" + doc_id.encode("utf-8"),
        ).derive(self._user_kek(user_id))

    def _aad(self, user_id: str, doc_id: str) -> bytes:
        return f"{user_id}\x00{doc_id}\x00{self._key_version}".encode("utf-8")

    def _path(self, user_id: str, doc_id: str) -> str:
        udir = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
        fname = hashlib.sha256(f"{user_id}\x00{doc_id}".encode("utf-8")).hexdigest() + ".enc"
        return os.path.join(self._root, udir, fname)

    # -- async I/O seam --------------------------------------------------------------------
    async def put(self, user_id: str, doc_id: str, plaintext: str) -> str:
        dek = self._doc_dek(user_id, doc_id)
        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), self._aad(user_id, doc_id))
        path = self._path(user_id, doc_id)
        await asyncio.to_thread(self._write, path, nonce + ct)
        return path

    async def get(self, user_id: str, doc_id: str) -> str:
        path = self._path(user_id, doc_id)
        try:
            blob = await asyncio.to_thread(self._read, path)
        except FileNotFoundError as exc:
            raise StoreError(f"no document for ({user_id!r}, {doc_id!r})") from exc
        dek = self._doc_dek(user_id, doc_id)
        nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        try:
            pt = AESGCM(dek).decrypt(nonce, ct, self._aad(user_id, doc_id))
        except Exception as exc:  # cryptography raises InvalidTag on wrong key/user/slot/tamper
            raise StoreError("document authentication failed (wrong key/user/slot or tampered)") from exc
        return pt.decode("utf-8")

    def exists(self, user_id: str, doc_id: str) -> bool:
        return os.path.exists(self._path(user_id, doc_id))

    @staticmethod
    def _write(path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    @staticmethod
    def _read(path: str) -> bytes:
        with open(path, "rb") as fh:
            return fh.read()
