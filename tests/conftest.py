"""Shared fixtures + synthetic-document generator with ground-truth PII."""

from __future__ import annotations

import os

# The suite validates the DETERMINISTIC guarantee layer; force the native detector so results
# don't depend on whether Presidio + a spaCy model happen to be installed (probabilistic recall
# is exercised separately). Deploys leave this unset to use the Presidio substrate.
os.environ["SCP_DISABLE_PRESIDIO"] = "1"

import pytest
from faker import Faker

from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.vault.session import SessionManager


@pytest.fixture
def manager() -> SessionManager:
    return SessionManager(ttl_seconds=3600)


@pytest.fixture
def policy() -> ObfuscationPolicy:
    return ObfuscationPolicy()


def make_document(seed: int) -> tuple[str, set[str]]:
    """A synthetic clinical doc + the set of DIRECT-IDENTIFIER originals that must never leak.

    Because we generate the PII, the ground truth is free. Clinical quasi-identifiers
    (ethnicity/age) are intentionally NOT in the leak set — they are preserved and transit.
    """
    fake = Faker()
    fake.seed_instance(seed)
    first = fake.first_name()
    last = fake.last_name()
    name = f"{first} {last}"
    ssn = fake.numerify("###-##-####")
    mrn = fake.numerify("########")
    email = f"{first.lower()}.{last.lower()}@example.com"
    phone = fake.numerify("###-###-####")

    doc = (
        f"Patient Name: {name}\n"
        f"DOB: 03/14/1979    SSN: {ssn}    MRN: {mrn}\n"
        f"Email: {email}    Phone: {phone}\n"
        f"{first} is a 47-year-old Han Chinese male on warfarin. "
        f"Follow up with {name} next week; reach {first} at {email}."
    )
    must_not_leak = {name, first, last, ssn, mrn, email, phone}
    return doc, must_not_leak
