"""Shared fixtures. A fixed master key keeps the encrypted store deterministic across tests."""

from __future__ import annotations

import os

os.environ.setdefault("SCP_MASTER_KEY", "11" * 32)

import pytest

from secure_context_pipeline.audit.audit import AuditLog
from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.detection.factory import build_detector
from secure_context_pipeline.detection.native import RuleEngineDetector
from secure_context_pipeline.obfuscation.engine.engine import ObfuscationEngine
from secure_context_pipeline.vault.session import SessionManager


@pytest.fixture
def policy() -> ObfuscationPolicy:
    return ObfuscationPolicy()


@pytest.fixture
def manager() -> SessionManager:
    return SessionManager(ttl_seconds=3600)


@pytest.fixture
def session(manager):
    return manager.create_session("user-test")


@pytest.fixture(params=["native", "presidio"])
def detector(request, policy):
    """Run detection-dependent tests on BOTH substrates. Presidio is skipped when unavailable
    so keyless CI still runs the native path."""
    if request.param == "native":
        return RuleEngineDetector(policy)
    det = build_detector(policy, prefer_presidio=True)
    if getattr(det, "name", "") != "presidio":
        pytest.skip("presidio substrate not installed")
    return det


@pytest.fixture
def audit() -> AuditLog:
    return AuditLog()


@pytest.fixture
def engine(detector, policy, audit) -> ObfuscationEngine:
    return ObfuscationEngine(detector, policy, audit=audit)
