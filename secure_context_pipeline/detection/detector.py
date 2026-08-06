"""The ``Detector`` interface — one of the four real abstractions.

A detector maps original text to candidate :class:`DetectedEntity` spans. It does **not**
resolve overlaps or cluster — the engine owns that pipeline stage (detect → resolve → cluster →
route). Two implementations satisfy this Protocol: :class:`PresidioDetector` (the intended
substrate) and :class:`RuleEngineDetector` (the native fallback).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..entities import DetectedEntity


@runtime_checkable
class Detector(Protocol):
    #: Human-readable substrate name, surfaced in the demo / audit ("presidio" | "native-regex").
    name: str

    def detect(self, text: str) -> list[DetectedEntity]:
        """Return candidate spans over ``text`` (possibly overlapping, unclustered)."""
        ...
