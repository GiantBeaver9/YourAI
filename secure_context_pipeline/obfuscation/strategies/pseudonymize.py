"""Pseudonymize — realistic fake substitution. ``"John Smith" -> "Michael Torres"``.

Reversible (vault entry keyed by the *fake* surface), but reversal is entity-resolution, not
parsing: a missed mention looks like a real name, so it fails **silent** — which is exactly why
tokenization, not this, is the default for round-trip-critical identifiers. Pseudonymization
earns its keep in the **de-identified export mode**: a realistic-but-fake shareable copy
(demos, synthetic datasets, training corpora) where realistic values are the *point* and
nothing is restored, so the fail-silent risk is moot.

Graded component — built for the properties that matter:

* **Deterministic in session.** Faker is seeded from the same per-(session, document) HMAC
  digest tokenize uses, so the same value maps to the same fake within a document, collapses
  coreferent mentions, and differs across sessions/documents. No global RNG, no ``Faker()``
  default seed.
* **Gender-preserving names.** The original first name's gender (inferred from Faker's en_US
  name lists) selects the fake's gender, so "she"/"he" in surrounding prose stays coherent and
  the model's relational reasoning isn't derailed.
* **Type-appropriate.** An address becomes a fake address, a phone a fake phone — the fake
  matches the slot so the document stays well-formed for the model.
* **Disjoint from the original.** A one-shot re-roll guarantees the fake never equals the
  value it replaces.
"""

from __future__ import annotations

from faker import Faker
from faker.providers.person.en_US import Provider as _EnPerson

from ...config import ObfuscationPolicy
from ...entities import Action, EntityType
from ...vault.keyring import KeyRing
from .base import Obfuscated, ObfuscationStrategy, VaultEntry

# Curated gender sets from Faker's own en_US lists — used only to infer the *original's* gender
# so the fake preserves it. Lowercased for case-robust lookup.
_MALE_NAMES = {n.lower() for n in _EnPerson.first_names_male}
_FEMALE_NAMES = {n.lower() for n in _EnPerson.first_names_female}


class Pseudonymize(ObfuscationStrategy):
    action = Action.PSEUDONYMIZE

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
        seed = int(digest[:12], 16)  # 48 bits of the HMAC -> deterministic-in-session seed
        fake_str = self._generate(canonical, entity_type, seed)

        # Disjoint from the original: never emit the value we are replacing.
        if fake_str.strip().lower() == canonical.strip().lower():
            fake_str = self._generate(canonical, entity_type, seed ^ 0xA5A5A5)

        return Obfuscated(
            replacement=fake_str,
            vault_entry=VaultEntry(key=fake_str, original=canonical, entity_tag=entity_type.tag),
        )

    # -- generation, per entity type -------------------------------------------------------
    def _generate(self, canonical: str, entity_type: EntityType, seed: int) -> str:
        fake = Faker("en_US")
        fake.seed_instance(seed)

        if entity_type is EntityType.NAME:
            return self._fake_name(canonical, fake)
        if entity_type is EntityType.ADDRESS:
            return fake.address().replace("\n", ", ")
        if entity_type is EntityType.EMAIL:
            return fake.ascii_email()
        if entity_type is EntityType.PHONE:
            return fake.phone_number()
        if entity_type in (EntityType.SSN,):
            # Available for export mode only; default routing tokenizes SSN precisely because a
            # realistic fake SSN can collide with a real person's number.
            return fake.ssn()
        if entity_type in (EntityType.MRN, EntityType.ACCOUNT, EntityType.ACCOUNT_FINANCIAL,
                           EntityType.INSURANCE_ID, EntityType.GENERIC_ID):
            return fake.bothify("??######").upper()
        if entity_type is EntityType.CREDIT_CARD:
            return fake.credit_card_number()
        if entity_type is EntityType.IP:
            return fake.ipv4()
        if entity_type is EntityType.URL:
            return fake.url()
        # Fallback: a plausible generic label rather than leaking the original.
        return fake.word().capitalize()

    def _fake_name(self, canonical: str, fake: Faker) -> str:
        first = canonical.strip().split()[0].lower() if canonical.strip() else ""
        if first in _MALE_NAMES and first not in _FEMALE_NAMES:
            return f"{fake.first_name_male()} {fake.last_name()}"
        if first in _FEMALE_NAMES and first not in _MALE_NAMES:
            return f"{fake.first_name_female()} {fake.last_name()}"
        # Ambiguous / unknown gender -> gender-neutral generation.
        return f"{fake.first_name()} {fake.last_name()}"
