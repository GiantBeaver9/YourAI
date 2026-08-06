"""Detector selection: Presidio when present, native regex otherwise.

Presidio is the intended substrate; the native fallback exists so keyless CI and a
dependency-light demo still run. The choice is logged (``detector.name``) so a run is never
ambiguous about which engine produced its spans.
"""

from __future__ import annotations

from ..config import ObfuscationPolicy
from .detector import Detector
from .native import RuleEngineDetector
from .rules import Rule


def build_detector(
    policy: ObfuscationPolicy,
    custom_rules: list[Rule] | None = None,
    *,
    prefer_presidio: bool = True,
) -> Detector:
    """Return the best available detector. Falls back to native regex if Presidio (or its
    spaCy model) can't be loaded — never crashes on a missing heavy dependency."""
    if prefer_presidio:
        try:
            from .presidio_detector import PresidioDetector

            return PresidioDetector(policy, custom_rules)
        except Exception:  # noqa: BLE001 — import OR model-load failure both mean "fall back"
            pass
    return RuleEngineDetector(policy, custom_rules)
