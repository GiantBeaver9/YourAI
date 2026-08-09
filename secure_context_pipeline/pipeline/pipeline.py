"""``SecureContextPipeline`` — the end-to-end round-trip.

    (encrypted store read) → detect → obfuscate → inject+verify → LLM → de-obfuscate → restore

Async only at the true I/O seams (store, LLM); the detect/obfuscate core underneath is
synchronous. A **session lease** is held across the entire round-trip, so teardown (logout /
expiry) cannot crypto-shred the vault mid-request — in-flight de-obfuscation always has its
key. Wiring is constructor-injected (detector, provider, policy, store, audit) — no globals —
so any leg swaps for a test double or a prod implementation without touching the flow.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..audit.audit import AuditLog
from ..config import ObfuscationPolicy, Settings
from ..deobfuscation.deobfuscator import Deobfuscator, DeobfuscationResult
from ..detection.factory import build_detector
from ..detection.rules import Rule
from ..llm.injector import ContextInjector, complete_with_chunking
from ..llm.provider import AnthropicProvider, GeminiProvider, LLMProvider, MockProvider
from ..obfuscation.engine.engine import ObfuscationEngine, ObfuscationResult
from ..store.store import EncryptedDocumentStore
from ..vault.session import Session, SessionManager


def select_provider_name(settings: Settings) -> str:
    """Decide which LLM provider to use — pure, SDK-free, so it's unit-testable.

    Explicit ``SCP_LLM_PROVIDER`` wins; otherwise auto-select by which key is present, preferring
    **Gemini** over Anthropic (this deployment runs on Gemini credit), then falling back to the
    keyless MockProvider."""
    choice = (settings.llm_provider or "auto").lower()
    if choice in ("mock", "gemini", "anthropic"):
        return choice
    if settings.gemini_api_key:
        return "gemini"
    if settings.anthropic_api_key:
        return "anthropic"
    return "mock"


@dataclass
class ProcessResult:
    #: User-facing restored text (``None`` when the document was routed to human review).
    restored_text: str | None
    obfuscation: ObfuscationResult
    deobfuscation: DeobfuscationResult | None
    raw_response: str | None
    routed_to_human: bool = False


class SecureContextPipeline:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        policy: ObfuscationPolicy | None = None,
        provider: LLMProvider | None = None,
        detector=None,
        audit: AuditLog | None = None,
        store: EncryptedDocumentStore | None = None,
        custom_rules: list[Rule] | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.policy = policy or ObfuscationPolicy()
        self.audit = audit or AuditLog()
        # Load deployment-authored rules from the configured file when none were passed in —
        # the operator "update the rules" path (edit JSON + redeploy, no code change).
        if custom_rules is None and self.settings.custom_rules_path:
            from ..detection.rules import load_custom_rules

            custom_rules = load_custom_rules(self.settings.custom_rules_path)
        self.custom_rules = custom_rules or []
        self.detector = detector or build_detector(self.policy, self.custom_rules)
        self.provider = provider or self._default_provider()
        self.store = store or EncryptedDocumentStore(self.settings.master_key, self.settings.store_root)
        self.sessions = SessionManager(self.settings.session_ttl_seconds)

        self._engine = ObfuscationEngine(self.detector, self.policy, audit=self.audit)
        self._injector = ContextInjector()
        self._deobf = Deobfuscator(audit=self.audit)

    def _default_provider(self) -> LLMProvider:
        name = select_provider_name(self.settings)
        if name == "gemini":
            return GeminiProvider(self.settings.gemini_api_key, self.settings.gemini_model)
        if name == "anthropic":
            return AnthropicProvider(self.settings.anthropic_api_key, self.settings.llm_model)
        return MockProvider()

    # -- ingest: encrypt-at-rest -----------------------------------------------------------
    async def ingest(self, user_id: str, doc_id: str, text: str) -> str:
        """Persist a document as ciphertext. Returns the on-disk path (ciphertext only)."""
        return await self.store.put(user_id, doc_id, text)

    # -- the round-trip --------------------------------------------------------------------
    async def process(
        self,
        session: Session,
        doc_id: str,
        task: str,
        *,
        text: str | None = None,
        user_id: str | None = None,
    ) -> ProcessResult:
        """Run one document through the full pipeline under a held session lease.

        Provide ``text`` directly, or ``user_id`` to read the encrypted store."""
        await self.sessions.reap_expired()  # crypto-shred any lapsed sessions before we work
        async with session.lease():
            if text is None:
                if user_id is None:
                    raise ValueError("provide either text= or user_id= to read the store")
                text = await self.store.get(user_id, doc_id)

            obf = await self._engine.obfuscate(text, session, doc_id)

            # Fail closed on human-review routing (e.g. pediatric: dates preserved) — the
            # payload is NOT sent to the provider.
            if obf.route_to_human:
                return ProcessResult(
                    restored_text=None, obfuscation=obf, deobfuscation=None,
                    raw_response=None, routed_to_human=True,
                )

            response = await complete_with_chunking(
                self._injector, self.provider, obf.obfuscated_text, task, obf.known_originals,
            )
            deob = await self._deobf.restore(
                response, session, used_pseudonymization=obf.used_pseudonymization,
            )
            return ProcessResult(
                restored_text=deob.restored_text, obfuscation=obf, deobfuscation=deob,
                raw_response=response,
            )
