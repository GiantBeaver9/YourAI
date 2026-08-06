"""Obfuscation strategies behind a shared ABC — swappable via config, zero harness change."""

from ...entities import Action
from .base import ObfuscationStrategy, StrategyContext, TransformResult
from .generalization import Generalization
from .pseudonymization import Pseudonymization
from .tokenization import Tokenization

#: Action -> strategy instance. The engine looks routing decisions up here.
STRATEGY_BY_ACTION: dict[Action, ObfuscationStrategy] = {
    Action.TOKENIZE: Tokenization(),
    Action.PSEUDONYMIZE: Pseudonymization(),
    Action.GENERALIZE: Generalization(),
}

__all__ = [
    "ObfuscationStrategy",
    "StrategyContext",
    "TransformResult",
    "Tokenization",
    "Pseudonymization",
    "Generalization",
    "STRATEGY_BY_ACTION",
]
