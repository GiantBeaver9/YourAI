"""Tokenization — the bias-neutral, fail-loud default for identifiers and names.

Token = `[TYPE_hexdigest]` where the digest is a per-document keyed HMAC of the canonical
value. Deterministic within a session+document; one-way; carries no bias signal. Reversible
via the vault.
"""

from __future__ import annotations

from ...entities import EntityType
from ...normalize import normalize_value
from .base import ObfuscationStrategy, StrategyContext, TransformResult


class Tokenization(ObfuscationStrategy):
    name = "tokenize"

    def transform(
        self, value: str, entity_type: EntityType, ctx: StrategyContext
    ) -> TransformResult:
        canonical = normalize_value(value)
        token = ctx.session.token_for(canonical, entity_type.tag, ctx.doc_id)
        return TransformResult(replacement=token, reversible=True, canonical=canonical)
