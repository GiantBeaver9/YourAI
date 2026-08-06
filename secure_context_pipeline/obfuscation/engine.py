"""Obfuscation engine — orchestrates detection, overlap resolution, routing, replacement, and audit.

NON-MEDIAN RULE (BUILD SPEC):
  - Offset-safe replacement: apply over sorted disjoint spans right-to-left. NEVER a naive str.replace.
  - Policy injected explicitly: ObfuscationPolicy passed down; no globals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..audit.logger import AuditLogger
from ..config import ObfuscationPolicy
from ..detection.detector import Detector, create_detector
from ..entities import Action, DetectedEntity, EntityType
from ..vault.session import Session
from .strategies import (
    GeneralizeStrategy,
    ObfuscationStrategy,
    PseudonymizeStrategy,
    TokenizeStrategy,
)


@dataclass
class ObfuscationResult:
    text: str
    entities: list[DetectedEntity]
    tokens: list[str] = field(default_factory=list)


class ObfuscationEngine:
    """Orchestrates detection -> overlap resolution -> strategy routing -> right-to-left replace -> audit."""

    def __init__(self, detector: Detector | None = None, audit_logger: AuditLogger | None = None) -> None:
        self.detector = detector or create_detector()
        self.audit_logger = audit_logger or AuditLogger()
        self.strategies: dict[Action, ObfuscationStrategy] = {
            Action.TOKENIZE: TokenizeStrategy(),
            Action.PSEUDONYMIZE: PseudonymizeStrategy(),
            Action.GENERALIZE: GeneralizeStrategy(),
        }

    def resolve_overlaps(self, entities: list[DetectedEntity]) -> list[DetectedEntity]:
        """Disjoint union of entity spans: sort by start asc, length desc, confidence desc."""
        if not entities:
            return []

        sorted_entities = sorted(
            entities,
            key=lambda e: (e.start, -(e.end - e.start), -e.confidence),
        )

        disjoint: list[DetectedEntity] = []
        last_end = -1

        for e in sorted_entities:
            if e.start >= last_end:
                disjoint.append(e)
                last_end = e.end
        return disjoint

    async def obfuscate(
        self,
        text: str,
        session: Session,
        doc_id: str = "",
        policy: ObfuscationPolicy | None = None,
    ) -> ObfuscationResult:
        if policy is None:
            policy = ObfuscationPolicy()

        # 1. Detection
        raw_entities = self.detector.detect(text, policy)

        # 2. Overlap resolution
        entities = self.resolve_overlaps(raw_entities)

        # 3. Process entities & build right-to-left replacement list
        replacements: list[tuple[int, int, str]] = []
        emitted_tokens: list[str] = []

        for entity in entities:
            # Check confidence-gated degradation
            if entity.confidence < policy.confidence_threshold:
                action = Action.REDACT
            else:
                action = policy.action_for(entity.entity_type)

            if action is Action.PRESERVE:
                continue

            if action is Action.REDACT:
                replacement = f"[{entity.entity_type.tag}_REDACTED]"
            elif action in self.strategies:
                strategy = self.strategies[action]
                replacement = await strategy.apply(entity, session, doc_id, policy)
            else:
                replacement = f"[{entity.entity_type.tag}_REDACTED]"

            replacements.append((entity.start, entity.end, replacement))
            emitted_tokens.append(replacement)

            # Audit emission (token-only, NO PII)
            self.audit_logger.log_event(
                session_id=session.session_id,
                entity_type=entity.entity_type,
                token=replacement,
                action=action,
                policy_version=policy.policy_version,
            )

        # 4. Offset-safe right-to-left replacement
        replacements.sort(key=lambda r: r[0], reverse=True)
        obfuscated_text = text
        for start, end, replacement in replacements:
            obfuscated_text = obfuscated_text[:start] + replacement + obfuscated_text[end:]

        return ObfuscationResult(
            text=obfuscated_text,
            entities=entities,
            tokens=emitted_tokens,
        )
