"""Per-session key material.

From one random session root key ``K_s`` we HKDF-derive two domain-separated subkeys:
  * ``k_token`` — HMAC key for deterministic, one-way token digests.
  * ``k_enc``   — AES-256-GCM key for encrypting the reversal map.

``destroy()`` zeroizes the root (best-effort — Python can't guarantee wiping; a KMS-held key
never resident in app memory is the real prod answer) which crypto-shreds every vault entry:
the AES-GCM ciphertext is unrecoverable without ``k_enc``, independent of clearing the dict.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_BYTES = 12  # 96-bit GCM nonce; fresh per encryption, never reused under one key.


class KeyRing:
    def __init__(self, root_key: bytes) -> None:
        if len(root_key) < 32:
            raise ValueError("root key must be >= 32 bytes")
        self._root = bytearray(root_key)
        self._k_token = self._derive(b"scp/token-hmac/v1")
        self._k_enc = self._derive(b"scp/vault-enc/v1")
        self._doc_keys: dict[str, bytes] = {}  # per-document subkeys, derived lazily
        self._destroyed = False

    def _derive(self, info: bytes) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=info
        ).derive(bytes(self._root))

    def token_digest(self, value: str, doc_id: str = "") -> str:
        """Deterministic, one-way HMAC digest of a canonical value, keyed per document.

        Same value + same document + same session -> same digest (coreference collapse).
        Different session -> different ``K_s`` -> different digest (cross-SESSION non-linkability).
        Different document -> different ``k_doc`` -> different digest (cross-DOCUMENT
        non-linkability: the same patient tokenizes differently across documents, so a
        clinician never sees one persistent pseudonym to correlate on)."""
        self._check()
        return hmac.new(self._doc_key(doc_id), value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _doc_key(self, doc_id: str) -> bytes:
        """Per-document subkey ``k_doc = HKDF(k_token, info=doc_id)`` — derived lazily, cached."""
        key = self._doc_keys.get(doc_id)
        if key is None:
            key = HKDF(
                algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"scp/doc/" + doc_id.encode("utf-8"),
            ).derive(self._k_token)
            self._doc_keys[doc_id] = key
        return key

    def encrypt(self, plaintext: str, aad: bytes = b"") -> bytes:
        self._check()
        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(self._k_enc).encrypt(nonce, plaintext.encode("utf-8"), aad)
        return nonce + ct

    def decrypt(self, blob: bytes, aad: bytes = b"") -> str:
        self._check()
        nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return AESGCM(self._k_enc).decrypt(nonce, ct, aad).decode("utf-8")

    def destroy(self) -> None:
        for i in range(len(self._root)):
            self._root[i] = 0
        self._k_token = b"\x00" * 32
        self._k_enc = b"\x00" * 32
        self._doc_keys.clear()
        self._destroyed = True

    def _check(self) -> None:
        if self._destroyed:
            raise RuntimeError("key ring destroyed — session key no longer exists")
