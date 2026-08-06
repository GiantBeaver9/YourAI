"""Obfuscation engine."""

from .engine import (
    ObfuscationEngine,
    ObfuscationResult,
    RouteToHumanError,
    apply_replacements,
    resolve_overlaps,
)

__all__ = [
    "ObfuscationEngine",
    "ObfuscationResult",
    "RouteToHumanError",
    "resolve_overlaps",
    "apply_replacements",
]
