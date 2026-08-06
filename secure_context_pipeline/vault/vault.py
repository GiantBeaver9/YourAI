"""Session-scoped, encrypted, reversible token<->original map.

Entries are AES-256-GCM ciphertext (under the session ``k_enc``) even in the in-memory demo,
so ``destroy()`` crypto-shreds the map. The AAD binds each ciphertext to its own token slot,
so an entry can't be lifted and replayed under a different token.

Reversible within a session (``resolve``); irreversible once the key ring is destroyed.
Async signatures so the in-memory demo swaps to Redis/Postgres in prod with no caller change.
"""

from __future__ import annotations

from .keyring import KeyRing


class SessionVault:
    def __init__(self, keyring: KeyRing) -> None:
        self._kr = keyring
        self._map: dict[str, bytes] = {}   # token -> encrypted original value
        self._types: dict[str, str] = {}   # token -> entity_type tag (NOT PII)
        self._destroyed = False

    async def store(self, token: str, original: str, entity_type_tag: str) -> None:
        """Idempotent: same token (deterministic) -> same value, so a re-store is a no-op.
        Safe under concurrent writers because the token is derived, not assigned."""
        if self._destroyed:
            raise RuntimeError("vault destroyed")
        if token not in self._map:
            self._map[token] = self._kr.encrypt(original, aad=token.encode("utf-8"))
            self._types[token] = entity_type_tag

    async def resolve(self, token: str) -> str | None:
        """Return the original value for a token, or None on a vault miss (never guess)."""
        if self._destroyed:
            return None
        blob = self._map.get(token)
        if blob is None:
            return None
        return self._kr.decrypt(blob, aad=token.encode("utf-8"))

    def contains(self, token: str) -> bool:
        return token in self._map

    def entity_type_of(self, token: str) -> str | None:
        return self._types.get(token)

    def __len__(self) -> int:
        return len(self._map)

    def destroy(self) -> None:
        self._map.clear()
        self._types.clear()
        self._destroyed = True
