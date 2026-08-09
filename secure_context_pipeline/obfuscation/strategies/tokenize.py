"""Tokenize — the default reversible primitive. ``value -> [TYPE_hexdigest]``.

Two properties make this the fail-closed default for anything that must round-trip:

* **Bias-neutral.** The token carries none of the inferable signal a realistic fake would
  (ethnicity/gender/class priors that could steer clinical reasoning). Tokenization is a
  de-biasing layer, not merely a privacy one.
* **Fails LOUD.** An un-restored token still *looks like a token*, so the de-obfuscation
  leftover-guard catches it. A missed value never ships silently as if it were real — contrast
  pseudonymization, whose misses look like genuine names (see de-obfuscation-deep-dive.md).

The digest is a per-(session, document) HMAC, so the same value collapses to one token within
a document (coreference) yet tokenizes differently across sessions and across documents.
"""

from __future__ import annotations

from ...config import ObfuscationPolicy
from ...entities import Action, EntityType
from ...grammar import make_token
from ...vault.keyring import KeyRing
from .base import Obfuscated, ObfuscationStrategy, VaultEntry


class Tokenize(ObfuscationStrategy):
    action = Action.TOKENIZE

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
        digest = keyring.token_digest(canonical, doc_id)
        token = make_token(entity_type.tag, digest)
        return Obfuscated(
            replacement=token,
            vault_entry=VaultEntry(key=token, original=canonical, entity_tag=entity_type.tag),
        )
