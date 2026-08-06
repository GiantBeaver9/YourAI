"""Audit trail — JSON-lines, **token-only, never original values**.

Every obfuscation event records *what type* was acted on, *which action* ran, the *opaque
token* (or a content-free reference for non-token strategies), the *policy version* active, and
*which rule* caught it — enough for a compliance officer to prove what happened to a document,
with zero PHI in the log. The invariant is tested directly: a test greps the emitted lines for
every fixture PII value and asserts zero hits.

What audit proves and doesn't: it proves obfuscation *ran* on each detected entity — not
detection *recall*. A missed entity is invisible to this log by construction (you can't audit
what you never detected); recall is closed in prod by egress DLP + detection metrics, named as
a known gap.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AuditEvent:
    ts: float
    session_id: str
    entity_type: str        # the TYPE only (e.g. "SSN") — never the value
    action: str             # TOKENIZE | PSEUDONYMIZE | GENERALIZE | REDACT | PRESERVE | ...
    token: str | None       # the [TYPE_hex] token for tokenize; None for one-way strategies
    policy_version: str
    ref: str | None = None  # content-free correlation id (HMAC digest prefix), never PII
    rule: str | None = None # which detector/rule fired ("presidio", "rule:ssn", ...)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


class AuditLog:
    """In-memory sink that optionally mirrors to a JSON-lines file.

    Kept in memory so tests can assert over events without file I/O; the file mirror is the
    durable trail. Only ever handed :class:`AuditEvent`\\s, which are PHI-free by construction —
    there is no code path that writes an original value here."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.events: list[AuditEvent] = []
        self._fh = open(path, "a", encoding="utf-8") if path else None

    def emit(
        self,
        *,
        session_id: str,
        entity_type: str,
        action: str,
        token: str | None = None,
        policy_version: str = "v1",
        ref: str | None = None,
        rule: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            ts=time.time(), session_id=session_id, entity_type=entity_type, action=action,
            token=token, policy_version=policy_version, ref=ref, rule=rule,
        )
        self.events.append(event)
        if self._fh is not None:
            self._fh.write(event.to_json() + "\n")
            self._fh.flush()
        return event

    def as_jsonl(self) -> str:
        return "\n".join(e.to_json() for e in self.events)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
