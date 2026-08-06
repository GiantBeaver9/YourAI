"""Encrypted-at-rest document store — envelope encryption, per-user key isolation.

master key -> per-USER KEK (HKDF) -> per-DOCUMENT DEK (random) -> AES-256-GCM(document).
The DEK is wrapped by the user's KEK and stored beside the ciphertext. AAD binds every
ciphertext to (user_id, doc_id, key_version), so a stolen blob can't be swapped between users
or documents. User A's KEK cannot unwrap User B's DEK -> isolation is cryptographic, not an
access-control `if`. Rotation = re-wrap DEKs (cheap); per-doc shred = drop one DEK.
"""

from __future__ import annotations

import hashlib
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_KEY_VERSION = "v1"


class EncryptedStore:
    def __init__(self, master_key: bytes, root: str = "store_data") -> None:
        if len(master_key) < 32:
            raise ValueError("master key must be >= 32 bytes")
        self._master = master_key
        self._root = root
        self._user_keks: dict[str, bytes] = {}
        os.makedirs(root, exist_ok=True)

    def _user_kek(self, user_id: str) -> bytes:
        kek = self._user_keks.get(user_id)
        if kek is None:
            kek = HKDF(
                algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"scp/user-kek/" + user_id.encode("utf-8"),
            ).derive(self._master)
            self._user_keks[user_id] = kek
        return kek

    @staticmethod
    def _aad(user_id: str, doc_id: str) -> bytes:
        return f"{user_id}|{doc_id}|{_KEY_VERSION}".encode("utf-8")

    async def put(self, user_id: str, doc_id: str, data: bytes) -> None:
        aad = self._aad(user_id, doc_id)
        dek = os.urandom(32)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, data, aad)
        wrap_nonce = os.urandom(12)
        wrapped_dek = AESGCM(self._user_kek(user_id)).encrypt(wrap_nonce, dek, aad)
        blob = {
            "key_version": _KEY_VERSION,
            "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex(),
            "wrap_nonce": wrap_nonce.hex(),
            "wrapped_dek": wrapped_dek.hex(),
        }
        path = self._path(user_id, doc_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(blob, fh)

    async def get(self, user_id: str, doc_id: str) -> bytes:
        with open(self._path(user_id, doc_id), encoding="utf-8") as fh:
            blob = json.load(fh)
        aad = self._aad(user_id, doc_id)
        dek = AESGCM(self._user_kek(user_id)).decrypt(
            bytes.fromhex(blob["wrap_nonce"]), bytes.fromhex(blob["wrapped_dek"]), aad
        )
        return AESGCM(dek).decrypt(
            bytes.fromhex(blob["nonce"]), bytes.fromhex(blob["ciphertext"]), aad
        )

    def _path(self, user_id: str, doc_id: str) -> str:
        u = hashlib.sha256(user_id.encode()).hexdigest()[:16]
        d = hashlib.sha256(doc_id.encode()).hexdigest()[:16]
        return os.path.join(self._root, u, d + ".enc")
