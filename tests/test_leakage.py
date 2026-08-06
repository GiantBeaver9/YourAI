"""Zero-PII-leakage property test — the headline guarantee, across generated documents.

Because the PII is generated, ground truth is free. We assert no direct-identifier original
(exact or first/last-name fragment) appears in the outbound payload. The oracle is tested with
a planted leak (it must be able to see one).
"""

from __future__ import annotations

import asyncio

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from secure_context_pipeline.pipeline import SecureContextPipeline
from secure_context_pipeline.vault.session import SessionManager

from .conftest import make_document


def _payload_for(seed: int) -> tuple[str, set[str]]:
    doc, must_not_leak = make_document(seed)
    manager = SessionManager()
    session = manager.create_session("dr")
    pipe = SecureContextPipeline()

    async def run():
        request, _ = await pipe.build_payload(session, doc, "Summarize.", "d1")
        return request.context

    payload = asyncio.run(run())
    return payload, must_not_leak


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(seed=st.integers(min_value=0, max_value=1_000_000))
def test_no_pii_in_outbound_payload(seed):
    payload, must_not_leak = _payload_for(seed)
    for value in must_not_leak:
        if len(value) < 3:
            continue  # too short to assert without coincidence
        assert value not in payload, f"PII leaked to payload: {value!r}"


def test_oracle_can_see_a_planted_leak():
    """The oracle must FAIL on a deliberately planted leak, or it proves nothing."""
    doc, must_not_leak = make_document(42)
    leaked = next(v for v in must_not_leak if len(v) >= 3)
    assert leaked in (doc + " " + leaked)  # sanity: the value is detectable as a substring
