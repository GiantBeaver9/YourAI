"""Pseudonymization — realistic fakes, deterministic within a session+document.

Seeded from the same per-document keyed digest as tokenization, so a given original maps to a
stable fake. Home use: a de-identified export mode where realistic values are the point and no
reversal is needed. NOTE the two guardrails from the design:
  * structured secrets (SSN, credit card) are NOT faked realistically — a fake-but-valid SSN
    can collide with a real person's number. They get an obviously-invalid placeholder.
  * de-obfuscation of pseudonyms fails silently, so we default to tokenize for anything that
    must round-trip; this strategy is for restore-tolerant contexts.
"""

from __future__ import annotations

from faker import Faker

from ...entities import EntityType
from ...normalize import normalize_value
from .base import ObfuscationStrategy, StrategyContext, TransformResult

# structured identifiers -> obviously-invalid placeholders, never a realistic fake
_STRUCTURED_PLACEHOLDER = {
    EntityType.SSN: "000-00-0000",
    EntityType.CREDIT_CARD: "0000-0000-0000-0000",
    EntityType.MRN: "MRN-000000",
    EntityType.ACCOUNT: "ACCT-000000",
}


class Pseudonymization(ObfuscationStrategy):
    name = "pseudonymize"

    def transform(
        self, value: str, entity_type: EntityType, ctx: StrategyContext
    ) -> TransformResult:
        canonical = normalize_value(value)
        # deterministic seed from the keyed digest -> same original => same fake in this session+doc
        seed = int(ctx.session.keyring.token_digest(canonical, ctx.doc_id)[:16], 16)
        faker = Faker()
        faker.seed_instance(seed)
        fake = self._fake_for(entity_type, faker)
        return TransformResult(replacement=fake, reversible=True, canonical=canonical)

    @staticmethod
    def _fake_for(entity_type: EntityType, faker: Faker) -> str:
        if entity_type in _STRUCTURED_PLACEHOLDER:
            return _STRUCTURED_PLACEHOLDER[entity_type]
        if entity_type is EntityType.NAME:
            return faker.name()
        if entity_type is EntityType.EMAIL:
            return faker.email()
        if entity_type is EntityType.PHONE:
            return faker.numerify("###-###-####")
        if entity_type in (EntityType.ADDRESS,):
            return faker.address().replace("\n", ", ")
        if entity_type is EntityType.ZIP:
            return faker.postcode()
        # default: an opaque bracketed placeholder rather than a wrong-typed fake
        return f"[{entity_type.tag}]"
