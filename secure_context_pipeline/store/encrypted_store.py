"""Encrypted document store — envelope encryption at rest.

Master Key -> per-user KEK -> per-doc DEK.
AES-256-GCM authenticated encryption with AAD=(user_id, doc_id, key_version).
Extracted plaintext is NEVER persisted unencrypted.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_BYTES = 12


class EncryptedStore:
    """Envelope-encrypted document storage."""

    def __init__(self, master_key: bytes, store_root: str = "store_data") -> None:
        if len(master_key) < 32:
            raise ValueError("master key must be >= 32 bytes")
        self.master_key = master_key
        self.store_root = store_root
        os.makedirs(self.store_root, exist_ok=True)

    def _derive_kek(self, user_id: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"scp/store-kek/v1/" + user_id.encode("utf-8"),
        ).derive(self.master_key)

    def _derive_dek(self, user_id: str, doc_id: str) -> bytes:
        kek = self._derive_kek(user_id)
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"scp/store-dek/v1/" + doc_id.encode("utf-8"),
        ).derive(kek)

    def _make_aad(self, user_id: str, doc_id: str) -> bytes:
        return f"{user_id}:{doc_id}:v1".encode("utf-8")

    def _get_path(self, user_id: str, doc_id: str) -> str:
        user_dir = os.path.join(self.store_root, user_id)
        os.makedirs(user_dir, exist_ok=True)
        return os.path.join(user_dir, f"{doc_id}.enc")

    def save(self, user_id: str, doc_id: str, content: str) -> str:
        dek = self._derive_dek(user_id, doc_id)
        aad = self._make_aad(user_id, doc_id)
        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(dek).encrypt(nonce, content.encode("utf-8"), aad)
        blob = nonce + ct
        path = self._get_path(user_id, doc_id)
        with open(path, "wb") as f:
            f.write(blob)
        return path

    def load(self, user_id: str, doc_id: str) -> str:
        path = self._get_path(user_id, doc_id)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Document {doc_id} not found for user {user_id}")
        with open(path, "rb") as f:
            blob = f.read()
        dek = self._derive_dek(user_id, doc_id)
        aad = self._make_aad(user_id, doc_id)
        nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return AESGCM(dek).decrypt(nonce, ct, aad).decode("utf-8")
