"""The ``ObfuscationStrategy`` ABC and its three concrete primitives."""

from ...entities import Action
from .base import Obfuscated, ObfuscationStrategy, VaultEntry
from .generalize import Generalize
from .pseudonymize import Pseudonymize
from .tokenize import Tokenize

#: Default registry of the reversible/one-way surface strategies, keyed by the action they
#: implement. The engine looks a strategy up here from ``policy.action_for(entity_type)``.
DEFAULT_STRATEGIES: dict[Action, ObfuscationStrategy] = {
    Action.TOKENIZE: Tokenize(),
    Action.PSEUDONYMIZE: Pseudonymize(),
    Action.GENERALIZE: Generalize(),
}

__all__ = [
    "ObfuscationStrategy",
    "Obfuscated",
    "VaultEntry",
    "Tokenize",
    "Pseudonymize",
    "Generalize",
    "DEFAULT_STRATEGIES",
]
