"""The one real obfuscation interface: ``ObfuscationStrategy`` (ABC) with three concretes.

Each strategy is a *pure, synchronous* transform: given a canonical value (the coreference
cluster's representative) and its entity type, it returns an ``Obfuscated`` — the replacement
string plus, if the transform is reversible, the vault entry to persist.

Purity is deliberate (BUILD-SPEC: "parse/obfuscate core is sync and honest, not
async-painted"). The single async touch — writing the vault — is confined to the engine, which
collects each strategy's ``vault_entry`` and awaits the store. So strategies stay trivially
unit-testable with no event loop and no I/O.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...config import ObfuscationPolicy
from ...entities import Action, EntityType
from ...vault.keyring import KeyRing


@dataclass(frozen=True)
class VaultEntry:
    """A reversible mapping to persist: ``key`` (the emitted surface — token OR fake string)
    resolves back to ``original``. ``entity_tag`` is metadata, never PII."""

    key: str
    original: str
    entity_tag: str


@dataclass(frozen=True)
class Obfuscated:
    """A strategy's output for one entity/cluster.

    ``replacement`` is what appears in the outbound text. ``vault_entry`` is present iff the
    transform is reversible (tokenize, pseudonymize) and absent for one-way transforms
    (generalize, redact)."""

    replacement: str
    vault_entry: VaultEntry | None = None


class ObfuscationStrategy(ABC):
    """Turn a canonical value into its obfuscated surface form.

    Subclasses set :attr:`action` and implement :meth:`apply`. The engine owns *which*
    strategy runs for a given entity (via ``policy.action_for``); a strategy owns only *how*.
    """

    #: The routing :class:`Action` this strategy implements.
    action: Action

    @abstractmethod
    def apply(
        self,
        canonical: str,
        entity_type: EntityType,
        keyring: KeyRing,
        doc_id: str,
        policy: ObfuscationPolicy,
        *,
        patient_age: int | None = None,
    ) -> Obfuscated:
        """Return the obfuscated surface (and any vault entry) for ``canonical``.

        ``keyring`` supplies the per-(session, document) key material — the strategy never
        holds session state itself. ``patient_age`` is the document-level age context the
        engine resolves once and threads through, used only by :class:`Generalize`'s
        elderly/pediatric date branches.
        """
        raise NotImplementedError
