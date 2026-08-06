"""End-to-end orchestration: ingest -> detect -> obfuscate -> inject -> LLM -> restore.

Holds a session lease across the whole round-trip so a logout/expiry can't crypto-shred the
vault mid-request. Async at the I/O seam (the LLM call); the parse/obfuscate core is sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..audit import AuditLog
from ..config import ObfuscationPolicy
from ..deobfuscation import Deobfuscator
from ..detection import get_detector
from ..llm import ContextInjector, MockProvider
from ..obfuscation.engine import ObfuscationEngine


@dataclass
class PipelineResult:
    obfuscated: str
    llm_reply: str
    restored: str
    entities_detected: int
    tokens: int
    leftover_tokens: list[str] = field(default_factory=list)


class SecureContextPipeline:
    def __init__(self, detector=None, provider=None, policy=None, audit=None) -> None:
        self.policy = policy or ObfuscationPolicy()
        self.detector = detector or get_detector()
        self.provider = provider or MockProvider()
        self.audit = audit or AuditLog()
        self.engine = ObfuscationEngine(self.detector, self.policy, self.audit)
        self.injector = ContextInjector()

    async def build_payload(self, session, document_text: str, task: str, doc_id: str = "doc"):
        """Obfuscate and assemble the outbound request WITHOUT calling the LLM — the boundary
        the zero-leakage test inspects. Runs verify-before-send."""
        obf = await self.engine.obfuscate(document_text, session, doc_id)
        request = self.injector.build_request(obf.obfuscated_text, task, obf.known_originals)
        return request, obf

    async def process(
        self, session, document_text: str, task: str, doc_id: str = "doc"
    ) -> PipelineResult:
        async with session.lease():
            request, obf = await self.build_payload(session, document_text, task, doc_id)
            reply = await self.provider.complete(request)
            restored = await Deobfuscator(session.vault).restore(reply)
            return PipelineResult(
                obfuscated=obf.obfuscated_text,
                llm_reply=reply,
                restored=restored.text,
                entities_detected=len(obf.entities),
                tokens=obf.token_count,
                leftover_tokens=restored.leftover_tokens,
            )
