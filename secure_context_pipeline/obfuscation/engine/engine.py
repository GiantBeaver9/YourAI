"""Obfuscation engine — detect -> resolve overlaps -> route -> offset-safe replace -> audit.

The algorithmically fiddly module, built deliberately:
  * **Overlap resolution** (A3): greedy by (confidence, length) -> a disjoint span set; the
    higher-confidence span wins (DOB beats a bare-number magnitude hit inside it).
  * **Offset-safe replacement** (A2): apply right-to-left so earlier offsets never shift.
    NEVER a naive left-to-right `str.replace` loop.
  * **Cluster-canonical restoration** (R1): all mentions of one entity share a token; the
    vault stores the *longest* original surface for that token, so every mention restores to
    the full form.
  * **Graceful degradation**: confidence below threshold -> REDACT, never pass through.
  * Preserved quasi-identifiers are left in place AND excluded from `known_originals`, so the
    verify-before-send / leakage check doesn't false-positive on legitimately-transmitted
    clinical signal.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...audit import AuditEvent, AuditLog
from ...config import ObfuscationPolicy
from ...entities import Action, DetectedEntity
from ..strategies import STRATEGY_BY_ACTION, StrategyContext


class RouteToHumanError(RuntimeError):
    """Fail closed: this document/entity needs human review, not automated obfuscation."""


@dataclass
class ObfuscationResult:
    obfuscated_text: str
    known_originals: set[str]   # direct-identifier originals removed (for the leak check)
    entities: list[DetectedEntity]
    token_count: int


def resolve_overlaps(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Greedy by (confidence, span length) -> disjoint set. Higher-confidence span wins an
    overlap; ties break to the longer span."""
    chosen: list[DetectedEntity] = []
    for e in sorted(entities, key=lambda x: (x.confidence, x.end - x.start), reverse=True):
        if not any(e.overlaps(c) for c in chosen):
            chosen.append(e)
    return sorted(chosen, key=lambda x: x.start)


def apply_replacements(text: str, repls: list[tuple[int, int, str]]) -> str:
    """Offset-safe: apply right-to-left so replacing a span never shifts an earlier one."""
    for start, end, replacement in sorted(repls, key=lambda r: r[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


class ObfuscationEngine:
    def __init__(
        self,
        detector,
        policy: ObfuscationPolicy | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.detector = detector
        self.policy = policy or ObfuscationPolicy()
        self.audit = audit

    async def obfuscate(self, text: str, session, doc_id: str = "") -> ObfuscationResult:
        entities = resolve_overlaps(await self.detector.detect(text, self.policy))
        ctx = StrategyContext(session=session, doc_id=doc_id, policy=self.policy)

        planned: list[tuple[DetectedEntity, Action, str, str | None]] = []
        canonical_original: dict[str, str] = {}  # token -> longest original surface

        for e in entities:
            action = self._action_for(e)
            if action is Action.ROUTE_TO_HUMAN:
                raise RouteToHumanError(f"{e.entity_type.value} requires human review")
            if action is Action.PRESERVE:
                continue  # left in place; NOT a leak (clinical signal), so not a known-original
            if action is Action.REDACT:
                planned.append((e, action, f"[REDACTED_{e.entity_type.tag}]", None))
                continue
            res = STRATEGY_BY_ACTION[action].transform(e.cluster_id or e.text, e.entity_type, ctx)
            token = res.replacement if res.reversible else None
            if token is not None:
                prev = canonical_original.get(token)
                if prev is None or len(e.text) > len(prev):
                    canonical_original[token] = e.text
            planned.append((e, action, res.replacement, token))

        # store reversible tokens -> their cluster's longest original (idempotent, order-safe)
        for token, original in canonical_original.items():
            await session.vault.store(token, original, "")

        repls: list[tuple[int, int, str]] = []
        known: set[str] = set()
        for e, action, replacement, token in planned:
            repls.append((e.start, e.end, replacement))
            known.add(e.text)
            if self.audit:
                await self.audit.record(
                    AuditEvent(
                        session_id=session.session_id,
                        event="obfuscate",
                        entity_type=e.entity_type.value,
                        token=token or replacement,
                        action=action.value,
                        policy_version=self.policy.policy_version,
                        doc_id=doc_id,
                    )
                )

        return ObfuscationResult(
            obfuscated_text=apply_replacements(text, repls),
            known_originals=known,
            entities=entities,
            token_count=len(canonical_original),
        )

    def _action_for(self, e: DetectedEntity) -> Action:
        if e.confidence < self.policy.confidence_threshold:
            return Action.REDACT  # graceful degradation — under-confident -> redact, never leak
        return self.policy.action_for(e.entity_type)
