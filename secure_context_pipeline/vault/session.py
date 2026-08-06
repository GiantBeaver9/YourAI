"""Session lifecycle — the keystone.

A session is an authenticated multi-document working period owning one ephemeral random root
key ``K_s``. Two independent key hierarchies: the durable per-user store key (elsewhere) and
this ephemeral ``K_s`` — deliberately NOT derived from each other, which is what makes
"irreversible across sessions" true rather than aspirational.

3-state lifecycle ACTIVE -> DRAINING -> DESTROYED. In-flight work holds a lease; teardown
waits for leases to drain (bounded) before zeroizing the key and crypto-shredding the vault.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from enum import Enum

from .keyring import KeyRing
from .vault import SessionVault


class SessionState(str, Enum):
    ACTIVE = "ACTIVE"
    DRAINING = "DRAINING"
    DESTROYED = "DESTROYED"


class SessionClosed(RuntimeError):
    """Raised when work is attempted against a draining/destroyed/expired session.

    Fail closed: callers get this typed error, never raw tokens or raw PII.
    """


class Session:
    def __init__(self, session_id: str, user_id: str, ttl_seconds: int = 3600) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self._root = bytearray(os.urandom(32))  # K_s — random, ephemeral (mutable for zeroize)
        self.keyring = KeyRing(self._root)
        self.vault = SessionVault(self.keyring)
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl_seconds
        self.state = SessionState.ACTIVE
        self._leases = 0
        self._drained = asyncio.Event()
        self._drained.set()

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def token_for(self, canonical_value: str, type_tag: str, doc_id: str = "") -> str:
        """Deterministic token for a canonical (cluster) value, keyed per document.

        Same value+doc within the session -> same token; a different document -> a different
        token for the same value (cross-document non-linkability); a new session -> a fresh
        ``K_s`` -> different tokens entirely (cross-session non-linkability)."""
        from ..grammar import make_token

        return make_token(type_tag, self.keyring.token_digest(canonical_value, doc_id))

    @asynccontextmanager
    async def lease(self):
        """Hold across a full obfuscate->LLM->de-obf round-trip so teardown can't destroy the
        vault mid-request. Rejects new work once the session is no longer ACTIVE."""
        if self.state is not SessionState.ACTIVE or self.is_expired():
            raise SessionClosed(f"session {self.session_id} is not active")
        self._leases += 1
        self._drained.clear()
        try:
            yield
        finally:
            self._leases -= 1
            if self._leases == 0:
                self._drained.set()

    async def destroy(self, drain_timeout: float = 10.0) -> None:
        """Logout/expiry: stop admitting work, let in-flight leases finish (bounded), then
        zeroize K_s and crypto-shred the vault."""
        if self.state is SessionState.DESTROYED:
            return
        self.state = SessionState.DRAINING
        try:
            await asyncio.wait_for(self._drained.wait(), timeout=drain_timeout)
        except asyncio.TimeoutError:
            pass  # forced teardown; in-flight de-obf will fail closed on the destroyed vault
        self.vault.destroy()
        self.keyring.destroy()
        for i in range(len(self._root)):
            self._root[i] = 0
        self.state = SessionState.DESTROYED


class SessionManager:
    """Owns sessions; enforces isolation — each session has its own K_s, nothing shared."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl_seconds

    def create_session(self, user_id: str) -> Session:
        sid = "sess_" + os.urandom(8).hex()
        session = Session(sid, user_id, self._ttl)
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session and session.state is SessionState.ACTIVE and session.is_expired():
            session.state = SessionState.DRAINING  # lazy expiry; reap_expired() does the shred
        return session

    async def reap_expired(self) -> int:
        """Destroy every expired session — zeroizing K_s and crypto-shredding its vault.

        The demo has no background timer, so the pipeline calls this opportunistically at the
        start of each round-trip. That bounds how long an expired session's key material and
        reversible map linger in memory (prod would run this on a periodic reaper)."""
        reaped = 0
        for sid in list(self._sessions):
            session = self._sessions[sid]
            if session.is_expired() and session.state is not SessionState.DESTROYED:
                await self.destroy(sid)
                reaped += 1
        return reaped

    async def destroy(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            await session.destroy()

    async def destroy_all(self) -> None:
        for sid in list(self._sessions):
            await self.destroy(sid)
