"""Compliance audit log — token-only, append-only.

HARD REQUIREMENT: an event NEVER contains an original value. It records what was obfuscated
(type + token + action) and under which policy, so a compliance officer can prove obfuscation
ran — and prove *which* policy was active for a document 30 days ago (`policy_version`).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass


@dataclass
class AuditEvent:
    session_id: str
    event: str            # "obfuscate" | "deobfuscate"
    entity_type: str
    token: str            # the token, NEVER the original value
    action: str
    policy_version: str
    doc_id: str = ""
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()


class AuditLog:
    """Async, JSON-lines. In-memory by default; writes to a file if a path is given."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path
        self._events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self._events.append(event)
        if self._path:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(event)) + "\n")

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)
