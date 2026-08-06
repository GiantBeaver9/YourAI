"""Obfuscation strategies.

Implements the three core primitives behind a shared ABC:
  1. Tokenize: Opaque, bias-neutral tokens `[TYPE_hex]`. Reversible -> vault entry.
  2. Pseudonymize: Realistic fakes generated deterministically via Faker seeded by the
     token digest. Reversible -> vault entry.
  3. Generalize: One-way precision reduction (e.g., dates to year). Vault-free.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from faker import Faker

from ..config import ObfuscationPolicy
from ..entities import DetectedEntity, EntityType
from ..vault.session import Session


class ObfuscationStrategy(ABC):
    @abstractmethod
    async def apply(
        self,
        entity: DetectedEntity,
        session: Session,
        doc_id: str,
        policy: ObfuscationPolicy,
    ) -> str:
        """Apply obfuscation strategy and return replacement string."""
        ...


class TokenizeStrategy(ObfuscationStrategy):
    async def apply(
        self,
        entity: DetectedEntity,
        session: Session,
        doc_id: str,
        policy: ObfuscationPolicy,
    ) -> str:
        canonical = entity.cluster_id or entity.text
        token = session.token_for(canonical, entity.entity_type.tag, doc_id)
        await session.vault.store(token, entity.text, entity.entity_type.tag)
        return token


class PseudonymizeStrategy(ObfuscationStrategy):
    """Deterministic pseudonymization using Faker seeded by the key ring token digest."""

    async def apply(
        self,
        entity: DetectedEntity,
        session: Session,
        doc_id: str,
        policy: ObfuscationPolicy,
    ) -> str:
        canonical = entity.cluster_id or entity.text
        digest_str = session.keyring.token_digest(canonical, doc_id)
        seed = int(digest_str[:16], 16)
        fake = Faker()
        fake.seed_instance(seed)

        et = entity.entity_type
        if et is EntityType.NAME:
            pseudonym = fake.name()
        elif et is EntityType.ADDRESS:
            pseudonym = fake.address().replace("\n", ", ")
        elif et is EntityType.EMAIL:
            pseudonym = fake.email()
        elif et is EntityType.PHONE:
            pseudonym = fake.phone_number()
        elif et is EntityType.MRN:
            pseudonym = f"MRN-{fake.numerify('#######')}"
        elif et is EntityType.ACCOUNT or et is EntityType.ACCOUNT_FINANCIAL:
            pseudonym = f"ACC-{fake.numerify('########')}"
        elif et is EntityType.SSN:
            pseudonym = fake.ssn()
        elif et is EntityType.ZIP:
            pseudonym = fake.zipcode()
        else:
            pseudonym = f"PSEUDO-{et.tag}-{digest_str[:6]}"

        token = session.token_for(canonical, entity.entity_type.tag, doc_id)
        await session.vault.store(token, entity.text, entity.entity_type.tag)
        return pseudonym


class GeneralizeStrategy(ObfuscationStrategy):
    """One-way generalization (e.g. dates to year). Vault-free."""

    async def apply(
        self,
        entity: DetectedEntity,
        session: Session,
        doc_id: str,
        policy: ObfuscationPolicy,
    ) -> str:
        et = entity.entity_type
        text = entity.text.strip()

        if et in (EntityType.DATE, EntityType.DOB):
            match = re.search(r"\b(19\d\d|20\d\d)\b", text)
            if match:
                year = match.group(1)
                return f"[YEAR_{year}]"
            return "[YEAR_UNKNOWN]"

        if et is EntityType.AGE:
            match = re.search(r"\b(\d+)\b", text)
            if match:
                age_val = int(match.group(1))
                if age_val >= policy.elderly_age_ceiling:
                    return "[AGE_90+]"
                if age_val <= policy.pediatric_age_floor:
                    return f"[AGE_{age_val}_PEDIATRIC_SIGNAL]"
                return f"[AGE_{age_val}]"
            return text

        if et is EntityType.ZIP:
            if len(text) >= 3:
                return f"{text[:3]}XX"
            return "[ZIP_GENERALIZED]"

        return f"[{et.tag}_GENERALIZED]"
