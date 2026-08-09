"""Obfuscation: strategies (the reversible/one-way primitives) + the routing engine."""

from .engine.engine import ObfuscationEngine, ObfuscationResult
from .strategies import Generalize, Obfuscated, ObfuscationStrategy, Pseudonymize, Tokenize

__all__ = [
    "ObfuscationEngine",
    "ObfuscationResult",
    "ObfuscationStrategy",
    "Obfuscated",
    "Tokenize",
    "Pseudonymize",
    "Generalize",
]
