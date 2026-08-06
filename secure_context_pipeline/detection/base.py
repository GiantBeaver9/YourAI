"""Detector contract + the rule shape.

A `Detector` returns labeled spans over the ORIGINAL text. Confidence is carried through so
the engine can apply graceful degradation (below threshold -> redact, never pass through).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..config import ObfuscationPolicy
from ..entities import DetectedEntity, EntityType


@dataclass
class RuleSpec:
    """One detection rule as data. Compiles to a native regex here and to a Presidio
    recognizer in the Presidio backend — same rule, two executors."""

    entity_type: EntityType
    regex: str | None = None
    #: label/anchor words that, nearby, raise confidence (and can type an otherwise-bare value)
    context: tuple[str, ...] = ()
    #: optional checksum/format validator -> confidence booster (never a redaction gate)
    validator: Callable[[str], bool] | None = None
    confidence: float = 0.85
    name: str = ""

    _compiled: re.Pattern | None = field(default=None, repr=False, compare=False)

    def pattern(self) -> re.Pattern | None:
        if self.regex and self._compiled is None:
            self._compiled = re.compile(self.regex)
        return self._compiled


class Detector(Protocol):
    async def detect(
        self, text: str, policy: ObfuscationPolicy
    ) -> list[DetectedEntity]:
        ...
