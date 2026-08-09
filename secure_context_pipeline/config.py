"""Policy and settings.

``ObfuscationPolicy`` is the single named, versioned knob-object (review: promoted from
scattered booleans). Its ``policy_version`` is logged per audit event so a compliance officer
can prove what was active for a given document. Defaults are permissive (preserve clinical
signal — Safe Harbor permits it) with strict one flag away.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from .entities import Action, EntityType


class AgeMode(str, Enum):
    EXACT = "exact"   # keep age < 90 (clinical signal; Safe Harbor permits)
    BAND = "band"     # 60-64 style banding (stricter)
    TOKEN = "token"   # tokenize age entirely (strictest)


#: Default entity_type -> Action. Tokenize identifiers (bias-neutral, fail-loud reversal);
#: generalize dates (one-way, vault-free); preserve clinical quasi-identifiers by default.
DEFAULT_ROUTING: dict[EntityType, Action] = {
    EntityType.NAME: Action.TOKENIZE,
    EntityType.SSN: Action.TOKENIZE,
    EntityType.MRN: Action.TOKENIZE,
    EntityType.ACCOUNT: Action.TOKENIZE,
    EntityType.ACCOUNT_FINANCIAL: Action.TOKENIZE,
    EntityType.INSURANCE_ID: Action.TOKENIZE,
    EntityType.CREDIT_CARD: Action.TOKENIZE,
    EntityType.EMAIL: Action.TOKENIZE,
    EntityType.PHONE: Action.TOKENIZE,
    EntityType.ADDRESS: Action.TOKENIZE,
    # ZIP -> generalize to the 3-digit prefix (Safe Harbor #17), zeroing restricted prefixes.
    # One-way and truthful (like dates), which is stronger than tokenizing an opaque ZIP.
    EntityType.ZIP: Action.GENERALIZE,
    EntityType.IP: Action.TOKENIZE,
    EntityType.URL: Action.TOKENIZE,
    EntityType.MEDICATION: Action.TOKENIZE,
    EntityType.LEGAL_CASE: Action.TOKENIZE,
    EntityType.GENERIC_ID: Action.TOKENIZE,
    EntityType.DIAGNOSIS: Action.TOKENIZE,
    EntityType.DOB: Action.GENERALIZE,
    EntityType.DATE: Action.GENERALIZE,
    # clinical quasi-identifiers — preserved unless policy flips them
    EntityType.ETHNICITY: Action.PRESERVE,
    EntityType.SEX: Action.PRESERVE,
    EntityType.AGE: Action.PRESERVE,
}


@dataclass
class ObfuscationPolicy:
    # --- quasi-identifier knobs (default permissive; strict one flag away) ---
    preserve_ethnicity: bool = True
    preserve_sex: bool = True
    preserve_age: bool = True
    age_mode: AgeMode = AgeMode.EXACT
    multi_record_strict: bool = False
    # --- fail-closed thresholds ---
    #: fraction 0.0-1.0 of unreadable/quarantined pages that trips whole-doc human review.
    #: 0.0 => any single quarantined page routes to human (strictest). Per-tenant sliding scale.
    image_quarantine_threshold: float = 0.05
    #: detection confidence below this -> REDACT rather than pass through (graceful degradation).
    confidence_threshold: float = 0.5
    #: age-driven date branches (review): <= floor -> human; >= ceiling -> delete all dates + age->90+
    pediatric_age_floor: int = 5
    elderly_age_ceiling: int = 90
    policy_version: str = "v1"
    #: optional per-deployment routing overrides
    routing_overrides: dict[EntityType, Action] = field(default_factory=dict)

    def action_for(self, et: EntityType) -> Action:
        if et in self.routing_overrides:
            return self.routing_overrides[et]
        # honor the preserve-knobs for clinical quasi-identifiers
        if et is EntityType.ETHNICITY and not self.preserve_ethnicity:
            return Action.TOKENIZE
        if et is EntityType.SEX and not self.preserve_sex:
            return Action.TOKENIZE
        if et is EntityType.AGE and not self.preserve_age:
            return Action.TOKENIZE
        return DEFAULT_ROUTING.get(et, Action.TOKENIZE)


@dataclass
class Settings:
    """Secrets and runtime config, from environment. Nothing hardcoded/committed."""

    master_key: bytes
    #: LLM provider selection: "auto" (default) picks Gemini > Anthropic > Mock by which key is
    #: present; force one with SCP_LLM_PROVIDER = mock | gemini | anthropic.
    llm_provider: str = "auto"
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-4-5"        # used by the Anthropic leg
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"      # used by the Gemini leg
    store_root: str = "store_data"
    session_ttl_seconds: int = 3600
    #: Optional path to a JSON file of deployment-authored detection rules (see
    #: detection.rules.load_custom_rules). Edit the file + redeploy to update detection — no code.
    custom_rules_path: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        mk = os.environ.get("SCP_MASTER_KEY")
        # 32-byte master key from hex env var; generated ephemeral if absent (demo convenience).
        master_key = bytes.fromhex(mk) if mk else os.urandom(32)
        return cls(
            master_key=master_key,
            llm_provider=os.environ.get("SCP_LLM_PROVIDER", "auto"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            llm_model=os.environ.get("SCP_LLM_MODEL", "claude-sonnet-4-5"),
            # accept GEMINI_API_KEY or the common GOOGLE_API_KEY alias
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
            gemini_model=os.environ.get("SCP_GEMINI_MODEL", "gemini-2.0-flash"),
            store_root=os.environ.get("SCP_STORE_ROOT", "store_data"),
            session_ttl_seconds=int(os.environ.get("SCP_SESSION_TTL", "3600")),
            custom_rules_path=os.environ.get("SCP_CUSTOM_RULES_PATH"),
        )
