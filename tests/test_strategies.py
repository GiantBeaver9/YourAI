"""Strategies: tokenize determinism, structured-secret safety, generalization."""

from __future__ import annotations

from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.entities import Action, EntityType
from secure_context_pipeline.obfuscation.strategies import STRATEGY_BY_ACTION, StrategyContext


def _ctx(manager):
    s = manager.create_session("u")
    return StrategyContext(session=s, doc_id="d1", policy=ObfuscationPolicy())


def test_tokenize_collapses_normalized_variants(manager):
    ctx = _ctx(manager)
    tok = STRATEGY_BY_ACTION[Action.TOKENIZE]
    assert (
        tok.transform("Mr. John Smith", EntityType.NAME, ctx).replacement
        == tok.transform("john smith", EntityType.NAME, ctx).replacement
    )


def test_pseudonymize_never_fakes_a_real_looking_ssn(manager):
    ctx = _ctx(manager)
    p = STRATEGY_BY_ACTION[Action.PSEUDONYMIZE]
    assert p.transform("123-45-6789", EntityType.SSN, ctx).replacement == "000-00-0000"


def test_pseudonymize_is_deterministic(manager):
    ctx = _ctx(manager)
    p = STRATEGY_BY_ACTION[Action.PSEUDONYMIZE]
    assert (
        p.transform("John Smith", EntityType.NAME, ctx).replacement
        == p.transform("John Smith", EntityType.NAME, ctx).replacement
    )


def test_generalize_date_and_age_are_one_way(manager):
    ctx = _ctx(manager)
    g = STRATEGY_BY_ACTION[Action.GENERALIZE]
    dob = g.transform("01/04/1973", EntityType.DOB, ctx)
    assert dob.replacement == "1973" and dob.reversible is False
    assert g.transform("94", EntityType.AGE, ctx).replacement == "90+"
