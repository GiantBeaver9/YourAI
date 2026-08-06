"""Engine: offset-safe replacement, overlap resolution, preserved QIs, age branches."""

from __future__ import annotations

import datetime

import pytest

from secure_context_pipeline.config import ObfuscationPolicy
from secure_context_pipeline.detection import get_detector
from secure_context_pipeline.entities import DetectedEntity, EntityType
from secure_context_pipeline.obfuscation.engine import (
    ObfuscationEngine,
    RouteToHumanError,
    apply_replacements,
    resolve_overlaps,
)


def test_apply_replacements_is_offset_safe():
    text = "AAAA BBBB CCCC"
    repls = [(0, 4, "[X]"), (5, 9, "[YY]"), (10, 14, "[ZZZ]")]
    assert apply_replacements(text, repls) == "[X] [YY] [ZZZ]"


def test_resolve_overlaps_highest_confidence_wins():
    dob = DetectedEntity(0, 10, EntityType.DOB, "01/04/1973", 0.95)
    frag = DetectedEntity(0, 2, EntityType.GENERIC_ID, "01", 0.7)
    assert resolve_overlaps([dob, frag]) == [dob]


async def test_preserved_quasi_identifier_transits_and_is_not_a_known_original(manager):
    s = manager.create_session("u")
    r = await ObfuscationEngine(get_detector(), ObfuscationPolicy()).obfuscate(
        "The patient is Han Chinese male.", s, "d1"
    )
    assert "Han Chinese" in r.obfuscated_text
    assert "Han Chinese" not in r.known_originals


async def test_pediatric_dob_routes_to_human(manager):
    s = manager.create_session("u")
    year = datetime.date.today().year - 3
    with pytest.raises(RouteToHumanError):
        await ObfuscationEngine(get_detector(), ObfuscationPolicy()).obfuscate(
            f"DOB: 03/14/{year}", s, "d1"
        )


async def test_policy_can_flip_ethnicity_to_tokenize(manager):
    s = manager.create_session("u")
    strict = ObfuscationPolicy(preserve_ethnicity=False)
    r = await ObfuscationEngine(get_detector(), strict).obfuscate(
        "The patient is Han Chinese.", s, "d1"
    )
    assert "Han Chinese" not in r.obfuscated_text  # now tokenized per policy
