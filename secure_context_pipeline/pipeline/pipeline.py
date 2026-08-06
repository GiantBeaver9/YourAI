"""End-to-end Secure Context Pipeline.

Orchestrates:
  (Encrypted Store) -> Detect & Obfuscate -> Verify-Before-Send -> LLM Provider -> De-obfuscate & Restore -> Audit
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..audit.logger import AuditLogger
from ..config import ObfuscationPolicy, Settings
from ..deobfuscation.deobfuscator import Deobfuscator
from ..entities import Action, DetectedEntity
from ..llm.provider import ContextInjector, LLMProvider, MockProvider
from ..obfuscation.engine import ObfuscationEngine, ObfuscationResult
from ..store.encrypted_store import EncryptedStore
from ..vault.session import Session, SessionClosed, SessionManager


@dataclass
class PipelineResult:
    doc_id: str
    obfuscated_text: str
    llm_raw_response: str
    restored_response: str
    detected_entities: list[DetectedEntity]
    audit_events: list[dict[str, Any]] = field(default_factory=list)


class SecureContextPipeline:
    """Async end-to-end pipeline orchestrator."""

    def __init__(
        self,
        settings: Settings | None = None,
        llm_provider: LLMProvider | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.store = EncryptedStore(self.settings.master_key, self.settings.store_root)
        self.session_manager = SessionManager(self.settings.session_ttl_seconds)
        self.audit_logger = audit_logger or AuditLogger()
        self.engine = ObfuscationEngine(audit_logger=self.audit_logger)
        self.deobfuscator = Deobfuscator()
        self.injector = ContextInjector()
        self.llm_provider = llm_provider or MockProvider()

    async def process_document(
        self,
        user_id: str,
        doc_id: str,
        raw_text: str,
        task: str = "Analyze the clinical report and provide summary recommendations.",
        session: Session | None = None,
        policy: ObfuscationPolicy | None = None,
    ) -> PipelineResult:
        if policy is None:
            policy = ObfuscationPolicy()

        # 1. Save document to encrypted store
        self.store.save(user_id, doc_id, raw_text)

        # 2. Get or create session
        if session is None:
            session = self.session_manager.create_session(user_id)

        # 3. Process request under session lease
        async with session.lease():
            # Obfuscation phase
            obf_result: ObfuscationResult = await self.engine.obfuscate(
                text=raw_text,
                session=session,
                doc_id=doc_id,
                policy=policy,
            )

            # Known originals for verify-before-send gate (exclude intentionally PRESERVED QIs)
            known_originals = [
                e.text for e in obf_result.entities
                if policy.action_for(e.entity_type) is not Action.PRESERVE
            ]

            # LLM prompt injection & gate verification
            llm_request = self.injector.build_request(
                obfuscated_text=obf_result.text,
                task=task,
                known_originals=known_originals,
            )

            # Call LLM
            llm_response = await self.injector.execute_bounded(
                self.llm_provider, llm_request
            )

            # De-obfuscate & restore response
            restored_response = await self.deobfuscator.restore(llm_response, session)

            audit_events = self.audit_logger.get_events()

            return PipelineResult(
                doc_id=doc_id,
                obfuscated_text=obf_result.text,
                llm_raw_response=llm_response,
                restored_response=restored_response,
                detected_entities=obf_result.entities,
                audit_events=audit_events,
            )
