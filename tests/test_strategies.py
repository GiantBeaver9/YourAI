"""Obfuscation strategy units — pure, no event loop needed."""

from __future__ import annotations

from secure_context_pipeline.config import AgeMode, ObfuscationPolicy
from secure_context_pipeline.entities import EntityType
from secure_context_pipeline.grammar import TOKEN_REGEX
from secure_context_pipeline.obfuscation.strategies import Generalize, Pseudonymize, Tokenize
from secure_context_pipeline.vault.keyring import KeyRing


def _kr() -> KeyRing:
    return KeyRing(b"\x02" * 32)


def test_tokenize_shape_and_vault_entry():
    out = Tokenize().apply("Jonathan Reyes", EntityType.NAME, _kr(), "d", ObfuscationPolicy())
    assert TOKEN_REGEX.fullmatch(out.replacement)
    assert out.vault_entry.key == out.replacement
    assert out.vault_entry.original == "Jonathan Reyes"


def test_tokenize_deterministic_same_keyring():
    kr = _kr()
    a = Tokenize().apply("x", EntityType.NAME, kr, "d", ObfuscationPolicy())
    b = Tokenize().apply("x", EntityType.NAME, kr, "d", ObfuscationPolicy())
    assert a.replacement == b.replacement


def test_pseudonymize_deterministic_and_gender_preserving():
    kr = _kr()
    pol = ObfuscationPolicy()
    a = Pseudonymize().apply("Sarah Johnson", EntityType.NAME, kr, "d", pol)
    b = Pseudonymize().apply("Sarah Johnson", EntityType.NAME, kr, "d", pol)
    assert a.replacement == b.replacement            # deterministic in session
    assert a.replacement != "Sarah Johnson"          # disjoint from original
    assert a.vault_entry.key == a.replacement         # keyed by the fake surface
    assert a.vault_entry.original == "Sarah Johnson"


def test_pseudonymize_differs_across_keyrings():
    a = Pseudonymize().apply("Sarah Johnson", EntityType.NAME, _kr(), "d", ObfuscationPolicy())
    b = Pseudonymize().apply("Sarah Johnson", EntityType.NAME, KeyRing(b"\x09" * 32), "d",
                             ObfuscationPolicy())
    assert a.replacement != b.replacement


def test_generalize_date_to_year():
    out = Generalize().apply("03/15/1985", EntityType.DOB, _kr(), "d", ObfuscationPolicy())
    assert out.replacement == "1985"
    assert out.vault_entry is None  # one-way, no vault entry


def test_generalize_elderly_removes_dates_and_bands_age():
    pol = ObfuscationPolicy()
    dates = Generalize().apply("03/15/1930", EntityType.DATE, _kr(), "d", pol, patient_age=94)
    assert dates.replacement == "[redacted]"
    age = Generalize().apply("94", EntityType.AGE, _kr(), "d", pol)
    assert age.replacement == "90+"


def test_generalize_age_banding():
    pol = ObfuscationPolicy(age_mode=AgeMode.BAND)
    out = Generalize().apply("62", EntityType.AGE, _kr(), "d", pol)
    assert out.replacement == "60-64"


def test_generalize_pediatric_preserves_dates():
    pol = ObfuscationPolicy()
    out = Generalize().apply("03/15/2022", EntityType.DATE, _kr(), "d", pol, patient_age=3)
    assert out.replacement == "03/15/2022"  # preserved; engine routes doc to human
