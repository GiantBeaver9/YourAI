"""Zero-leakage property test (the headline guarantee) + its oracle.

Hypothesis drives a seed; Faker mints ground-truth synthetic identifiers; we assert **no
original value (exact or name fragment) appears in the outbound payload**, and that the
verify-before-send gate stays silent. The oracle test proves the check has teeth: a
pass-through (null) obfuscation must trip the very same assertion.
"""

from __future__ import annotations

import pytest
from faker import Faker
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.detection.native import RuleEngineDetector
from secure_context_pipeline.llm.injector import ContextInjector
from secure_context_pipeline.obfuscation.engine.engine import ObfuscationEngine
from secure_context_pipeline.vault.session import SessionManager


def _make_doc(seed: int) -> tuple[str, dict[str, str]]:
    fk = Faker("en_US")
    fk.seed_instance(seed)
    first, last = fk.first_name(), fk.last_name()
    values = {
        "first": first,
        "last": last,
        "ssn": fk.ssn(),
        "email": f"user{fk.random_int(1000, 9999)}@example.com",
        "phone": f"({fk.random_int(200, 999)}) {fk.random_int(200, 999)}-{fk.random_int(1000, 9999)}",
        "mrn": str(fk.random_int(1000000, 9999999)),
        "member": fk.bothify("??######").upper(),
        "account": "ACCT-" + str(fk.random_int(10000, 99999)),
    }
    doc = (
        f"Patient: {first} {last}\n"
        f"SSN: {values['ssn']}\n"
        f"Email: {values['email']}\n"
        f"Phone: {values['phone']}\n"
        f"MRN: {values['mrn']}\n"
        f"Member ID: {values['member']}\n"
        f"Account No: {values['account']}\n"
    )
    return doc, values


def assert_no_leak(payload: str, values: dict[str, str]) -> None:
    for key, val in values.items():
        assert val not in payload, f"LEAK: {key}={val!r} present in outbound payload"


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
async def test_no_identifier_leaks(seed):
    doc, values = _make_doc(seed)
    pol = ObfuscationPolicy()
    eng = ObfuscationEngine(RuleEngineDetector(pol), pol)
    session = SessionManager().create_session("prop-user")

    res = await eng.obfuscate(doc, session, "doc")
    assert_no_leak(res.obfuscated_text, values)

    # The live twin: verify-before-send must not fire on a correctly-obfuscated payload.
    ContextInjector().build_request(res.obfuscated_text, "Summarize.", res.known_originals)


def test_oracle_detects_a_planted_leak():
    """If obfuscation were bypassed, the SAME assertion must fail — proving it isn't vacuous."""
    doc, values = _make_doc(12345)
    with pytest.raises(AssertionError):
        assert_no_leak(doc, values)  # raw doc still contains every value
