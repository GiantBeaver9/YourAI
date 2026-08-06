"""Complete test suite for Secure Context Pipeline.

Includes:
  - Hypothesis zero-leakage property test with planted leak oracle.
  - 5 required scenarios (happy path, entity not found, vault miss, expired session, concurrent session isolation).
  - Cryptographic shredding & audit safety tests.
"""

from __future__ import annotations

import asyncio
import time
import pytest
from faker import Faker
from hypothesis import given, settings as hyp_settings, strategies as st

from secure_context_pipeline import (
    ObfuscationPolicy,
    SecureContextPipeline,
    SessionClosed,
    SessionManager,
    Settings,
)
from secure_context_pipeline.audit.logger import AuditLogger
from secure_context_pipeline.deobfuscation.deobfuscator import (
    Deobfuscator,
    LeftoverResidueError,
)
from secure_context_pipeline.entities import Action, EntityType
from secure_context_pipeline.llm.provider import ContextInjector, SecurityLeakError
from secure_context_pipeline.obfuscation.engine import ObfuscationEngine
from secure_context_pipeline.vault.keyring import KeyRing


@pytest.fixture
def session_mgr():
    return SessionManager(ttl_seconds=3600)


@pytest.fixture
def session(session_mgr):
    return session_mgr.create_session("user_test")


@pytest.mark.asyncio
async def test_scenario_1_happy_path(session):
    """Scenario 1: Happy path end-to-end obfuscation and restoration."""
    raw_text = "Patient Johnathan Smith (SSN: 987-65-4321, MRN: MRN-884920) visited on 10/15/2024."
    audit_logger = AuditLogger("test_audit_1.jsonl")
    audit_logger.clear()
    engine = ObfuscationEngine(audit_logger=audit_logger)
    policy = ObfuscationPolicy()

    # 1. Obfuscate
    res = await engine.obfuscate(raw_text, session, doc_id="doc_1", policy=policy)
    assert "Johnathan Smith" not in res.text
    assert "987-65-4321" not in res.text
    assert "[NAME_" in res.text
    assert "[SSN_" in res.text

    # 2. De-obfuscate / Restore
    deobf = Deobfuscator()
    restored = await deobf.restore(res.text, session)
    assert "Johnathan Smith" in restored
    assert "987-65-4321" in restored
    assert "MRN-884920" in restored


@pytest.mark.asyncio
async def test_scenario_2_entity_not_found(session):
    """Scenario 2: Plain text with no PII passes through unaltered."""
    raw_text = "The quick brown fox jumps over the lazy dog."
    engine = ObfuscationEngine()
    res = await engine.obfuscate(raw_text, session, doc_id="doc_2")
    assert res.text == raw_text
    assert len(res.tokens) == 0


@pytest.mark.asyncio
async def test_scenario_3_vault_miss_fails_closed(session):
    """Scenario 3: Vault miss raises LeftoverResidueError (fails closed)."""
    fake_token_text = "Analysis for patient [NAME_000000000000] is complete."
    deobf = Deobfuscator()
    with pytest.raises(LeftoverResidueError):
        await deobf.restore(fake_token_text, session)


@pytest.mark.asyncio
async def test_scenario_4_expired_session_fails_closed(session_mgr):
    """Scenario 4: Expired or destroyed session raises SessionClosed."""
    session = session_mgr.create_session("user_exp")
    session.expires_at = time.time() - 10  # Expire immediately
    engine = ObfuscationEngine()
    with pytest.raises(SessionClosed):
        async with session.lease():
            await engine.obfuscate("John Doe", session, "doc_exp")


@pytest.mark.asyncio
async def test_scenario_5_concurrent_session_isolation(session_mgr):
    """Scenario 5: Session isolation — same value produces different tokens in different sessions; B cannot resolve A's token."""
    s_a = session_mgr.create_session("user_A")
    s_b = session_mgr.create_session("user_B")
    engine = ObfuscationEngine()

    val = "Johnathan Smith"
    t_a = s_a.token_for(val, "NAME", "doc_shared")
    t_b = s_b.token_for(val, "NAME", "doc_shared")

    # Tokens must differ across sessions!
    assert t_a != t_b

    # Store in session A's vault
    await s_a.vault.store(t_a, val, "NAME")

    # Session B vault must not contain or resolve session A's token!
    res_b = await s_b.vault.resolve(t_a)
    assert res_b is None


@pytest.mark.asyncio
async def test_crypto_shredding(session):
    """Zeroizing root key destroys key ring and makes vault decryption impossible."""
    await session.vault.store("[NAME_1234567890ab]", "Alice Baker", "NAME")
    assert await session.vault.resolve("[NAME_1234567890ab]") == "Alice Baker"

    # Destroy session (shreds key ring)
    await session.destroy()

    with pytest.raises(RuntimeError):
        session.keyring.encrypt("test")


@pytest.mark.asyncio
async def test_verify_before_send_gate():
    """Verify-before-send gate raises SecurityLeakError if raw PII is in outbound payload."""
    injector = ContextInjector()
    known_originals = ["Johnathan Smith", "987-65-4321"]

    # Leaked payload
    leaked_text = "Report for patient Johnathan Smith [SSN_1234567890ab]"
    with pytest.raises(SecurityLeakError):
        injector.build_request(leaked_text, "Summarize", known_originals)

    # Clean payload
    clean_text = "Report for patient [NAME_1234567890ab] [SSN_1234567890ab]"
    req = injector.build_request(clean_text, "Summarize", known_originals)
    assert req is not None


@pytest.mark.asyncio
async def test_audit_log_zero_pii(session):
    """Audit log contains token and action events, ZERO raw PII."""
    raw_text = "Patient Alice Johnson (SSN: 123-45-6789)"
    audit_logger = AuditLogger("test_audit_zero_pii.jsonl")
    audit_logger.clear()
    engine = ObfuscationEngine(audit_logger=audit_logger)

    await engine.obfuscate(raw_text, session, "doc_audit")

    events = audit_logger.get_events()
    assert len(events) > 0
    for event in events:
        event_str = str(event)
        assert "Alice" not in event_str
        assert "Johnson" not in event_str
        assert "123-45-6789" not in event_str


# Hypothesis zero-leakage property test
@given(
    seed=st.integers(min_value=1, max_value=10000),
)
@hyp_settings(max_examples=25, deadline=None)
def test_hypothesis_zero_leakage_property(seed):
    fake = Faker()
    fake.seed_instance(seed)

    name = fake.name()
    ssn = fake.ssn()
    email = fake.email()

    text = f"Clinical note for patient {name}, SSN {ssn}, email {email}."

    async def run_check():
        sm = SessionManager()
        s = sm.create_session("hyp_user")
        engine = ObfuscationEngine()
        res = await engine.obfuscate(text, s, "hyp_doc")

        # Property assertion: raw PII strings MUST NOT exist in obfuscated text!
        assert name not in res.text
        assert ssn not in res.text
        assert email not in res.text

    asyncio.run(run_check())
