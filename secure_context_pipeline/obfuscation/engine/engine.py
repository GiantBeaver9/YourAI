"""The obfuscation engine — orchestration of the sync core.

Pipeline stage order (BUILD-SPEC §5): **detect → resolve overlaps → cluster → route → replace
→ freeze → audit**. Everything here except the vault write is synchronous and pure; the one
async touch (persisting reversible entries) is confined to :meth:`obfuscate`, which awaits the
plan the sync core produced. That keeps the security-critical transform deterministic and
trivially testable.

Two invariants the engine enforces:

* **Offset-safe replacement.** Spans are disjoint (overlap-resolved) and applied right-to-left,
  so an earlier replacement never shifts a later span's indices. Never a ``str.replace`` loop —
  that would corrupt on repeated or substring values.
* **Confidence-gated redaction.** A detection below ``policy.confidence_threshold`` is
  **redacted**, never passed through. Under-detection is the only true leak; the system biases
  to over-cut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...audit.audit import AuditLog
from ...config import ObfuscationPolicy
from ...detection.coreference import cluster_names, propagate_names, resolve_overlaps
from ...detection.detector import Detector
from ...detection.rules import NAME_LABEL_STOP
from ...entities import Action, DetectedEntity, EntityType
from ...vault.session import Session
from ..strategies import DEFAULT_STRATEGIES, ObfuscationStrategy

_REDACTION = "[redacted]"
_AGE_INT = re.compile(r"\d{1,3}")


def _is_label_name(e: DetectedEntity) -> bool:
    """A single-token NAME whose word is a known field label (NER mislabel) — drop it.
    Multi-token names and real surnames are never affected."""
    if e.entity_type is not EntityType.NAME:
        return False
    toks = e.text.split()
    return len(toks) == 1 and toks[0].strip(".,'’").lower() in NAME_LABEL_STOP


@dataclass
class ObfuscationResult:
    obfuscated_text: str
    entities: list[DetectedEntity]
    #: Original surfaces that were removed/obfuscated — the verify-before-send scan set.
    #: Deliberately EXCLUDES preserved clinical quasi-identifiers (they stay in the payload).
    known_originals: set[str] = field(default_factory=set)
    route_to_human: bool = False
    used_pseudonymization: bool = False
    detector_name: str = ""


@dataclass
class _PlannedReplacement:
    start: int
    end: int
    replacement: str
    entity_type: EntityType
    action: Action
    original: str
    vault_key: str | None = None
    vault_original: str | None = None
    token: str | None = None
    ref: str | None = None
    source: str = ""


class ObfuscationEngine:
    def __init__(
        self,
        detector: Detector,
        policy: ObfuscationPolicy,
        *,
        strategies: dict[Action, ObfuscationStrategy] | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._detector = detector
        self._policy = policy
        self._strategies = strategies or DEFAULT_STRATEGIES
        self._audit = audit

    # -- public async entrypoint (the vault-write seam) ------------------------------------
    async def obfuscate(self, text: str, session: Session, doc_id: str) -> ObfuscationResult:
        planned, result = self._plan(text, session, doc_id)
        for p in planned:
            if p.vault_key is not None and p.vault_original is not None:
                await session.vault.store(p.vault_key, p.vault_original, p.entity_type.tag)
            if self._audit is not None:
                self._audit.emit(
                    session_id=session.session_id, entity_type=p.entity_type.value,
                    action=p.action.value, token=p.token,
                    policy_version=self._policy.policy_version, ref=p.ref, rule=p.source,
                )
        return result

    # -- the pure synchronous core --------------------------------------------------------
    def _plan(self, text: str, session: Session, doc_id: str) -> tuple[list[_PlannedReplacement], ObfuscationResult]:
        entities = resolve_overlaps(self._detector.detect(text))
        entities = [e for e in entities if not _is_label_name(e)]  # drop mislabeled field words
        # Propagate known names to catch NER misses (bare surnames in prose / signatures),
        # then re-resolve so full-name spans absorb their overlapping parts.
        entities = resolve_overlaps(propagate_names(text, entities))
        canonicals = cluster_names(entities)
        patient_age = self._resolve_age(entities)
        route_to_human = (
            patient_age is not None and patient_age <= self._policy.pediatric_age_floor
        )

        planned: list[_PlannedReplacement] = []
        known: set[str] = set()
        used_pseudo = False

        for e in entities:
            canonical = canonicals.get(e.cluster_id, e.text) if e.cluster_id else e.text
            action = self._route(e)
            if action is Action.PRESERVE:
                continue  # stays inline — NOT added to the verify set

            p = self._apply(action, canonical, e, session, doc_id, patient_age)
            if p is None:
                continue
            planned.append(p)
            known.add(e.text)
            known.add(canonical)
            if action is Action.PSEUDONYMIZE:
                used_pseudo = True

        obfuscated = self._offset_safe_replace(text, planned)
        result = ObfuscationResult(
            obfuscated_text=obfuscated, entities=entities, known_originals=known,
            route_to_human=route_to_human, used_pseudonymization=used_pseudo,
            detector_name=getattr(self._detector, "name", "unknown"),
        )
        return planned, result

    def _route(self, e: DetectedEntity) -> Action:
        if e.confidence < self._policy.confidence_threshold:
            return Action.REDACT  # graceful degradation — over-cut, never pass through
        return self._policy.action_for(e.entity_type)

    def _apply(
        self, action: Action, canonical: str, e: DetectedEntity, session: Session,
        doc_id: str, patient_age: int | None,
    ) -> _PlannedReplacement | None:
        base = dict(start=e.start, end=e.end, entity_type=e.entity_type, action=action,
                    original=e.text, source=e.source)

        if action in (Action.REDACT, Action.ROUTE_TO_HUMAN):
            return _PlannedReplacement(replacement=_REDACTION, **base)

        strategy = self._strategies.get(action)
        if strategy is None:  # unknown action -> fail closed to redaction
            return _PlannedReplacement(replacement=_REDACTION, **base)

        obf = strategy.apply(
            canonical, e.entity_type, session.keyring, doc_id, self._policy,
            patient_age=patient_age,
        )
        ref = session.keyring.token_digest(canonical, doc_id)[:8]
        p = _PlannedReplacement(replacement=obf.replacement, ref=ref, **base)
        if obf.vault_entry is not None:
            p.vault_key = obf.vault_entry.key
            p.vault_original = obf.vault_entry.original
            if action is Action.TOKENIZE:
                p.token = obf.vault_entry.key  # the [TYPE_hex] token, safe to log
        return p

    @staticmethod
    def _offset_safe_replace(text: str, planned: list[_PlannedReplacement]) -> str:
        # Disjoint spans applied right-to-left: an earlier splice never moves a later span.
        for p in sorted(planned, key=lambda r: r.start, reverse=True):
            text = text[:p.start] + p.replacement + text[p.end:]
        return text

    @staticmethod
    def _resolve_age(entities: list[DetectedEntity]) -> int | None:
        for e in entities:
            if e.entity_type is EntityType.AGE:
                m = _AGE_INT.search(e.text)
                if m:
                    return int(m.group(0))
        return None
