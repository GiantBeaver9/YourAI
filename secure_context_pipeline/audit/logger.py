"""Audit logging subsystem — JSON-lines token-only audit trail.

CRITICAL SECURITY REQUIREMENT:
The audit logger records timestamp, session ID, entity type, data class, token,
action, and policy version — NEVER the original PII value. A grep over audit output
for any fixture PII MUST yield zero hits.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from ..entities import Action, DataClass, EntityType, DATA_CLASS


class AuditLogger:
    """Token-only audit sink for compliance and verification."""

    def __init__(self, log_path: str = "audit.jsonl") -> None:
        self.log_path = log_path
        self._events: list[dict[str, Any]] = []

    def log_event(
        self,
        session_id: str,
        entity_type: EntityType,
        token: str,
        action: Action,
        policy_version: str,
    ) -> dict[str, Any]:
        """Record an audit event. MUST NEVER include original PII."""
        data_class = DATA_CLASS.get(entity_type, DataClass.PII).value
        event = {
            "ts": time.time(),
            "session_id": session_id,
            "entity_type": entity_type.value,
            "data_class": data_class,
            "token": token,
            "action": action.value,
            "policy_version": policy_version,
        }
        self._events.append(event)
        self._flush_event(event)
        return event

    def _flush_event(self, event: dict[str, Any]) -> None:
        try:
            folder = os.path.dirname(self.log_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception:
            pass  # Fallback to in-memory events

    def get_events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()
        if os.path.exists(self.log_path):
            try:
                os.remove(self.log_path)
            except Exception:
                pass
