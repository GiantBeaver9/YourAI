"""The shared strategy contract. Adding a strategy = subclass this + register — zero harness
change (strategy-swappability NFR)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...config import ObfuscationPolicy
from ...entities import EntityType


@dataclass
class StrategyContext:
    """Everything a strategy needs, injected explicitly (no globals)."""

    session: "object"   # vault.Session — duck-typed to avoid an import cycle
    doc_id: str
    policy: ObfuscationPolicy


@dataclass
class TransformResult:
    replacement: str   # the string that goes into the obfuscated text
    reversible: bool   # True -> engine stores replacement->original in the vault
    canonical: str     # normalized value, for determinism + audit correlation


class ObfuscationStrategy(ABC):
    #: stable identifier used in config/routing
    name: str = "base"

    @abstractmethod
    def transform(
        self, value: str, entity_type: EntityType, ctx: StrategyContext
    ) -> TransformResult:
        """Map an original value to its replacement. MUST be deterministic given the same
        (value, entity_type, session, doc_id) so tokens are stable within a session+document."""
        raise NotImplementedError
