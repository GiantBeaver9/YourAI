"""End-to-end demo: ingest -> detect -> obfuscate -> LLM -> restore, on the bundled fixture.

Runs keyless by default (a deterministic MockProvider). Set ANTHROPIC_API_KEY to route the
LLM leg to a real Claude model.
"""

from __future__ import annotations

import asyncio
import os
import pathlib

from secure_context_pipeline.audit import AuditLog
from secure_context_pipeline.detection import get_detector
from secure_context_pipeline.llm import AnthropicProvider, MockProvider
from secure_context_pipeline.pipeline import SecureContextPipeline
from secure_context_pipeline.vault.session import SessionManager

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sample_clinical_note.txt"
RULE = "-" * 78


def banner(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


async def main() -> None:
    document = FIXTURE.read_text(encoding="utf-8")
    provider = AnthropicProvider() if os.environ.get("ANTHROPIC_API_KEY") else MockProvider()

    manager = SessionManager()
    session = manager.create_session(user_id="dr_ramirez")
    audit = AuditLog()
    pipeline = SecureContextPipeline(detector=get_detector(), provider=provider, audit=audit)

    banner("1. ORIGINAL DOCUMENT (never leaves our infrastructure in this form)")
    print(document)

    result = await pipeline.process(
        session,
        document,
        task="Summarize this patient's anticoagulation plan and any ancestry-related dosing considerations.",
        doc_id="chart-001",
    )

    banner(f"2. OBFUSCATED CONTEXT SENT TO {type(provider).__name__} ({result.tokens} tokens)")
    print(result.obfuscated)
    print("\n>> Note: identifiers are opaque tokens; clinical signal (ancestry, age, dose,")
    print(">> viral load) is preserved so the model can still reason correctly.")

    banner("3. LLM RESPONSE (tokens preserved verbatim)")
    print(result.llm_reply)

    banner("4. RESTORED RESPONSE (what the user sees)")
    print(result.restored)

    banner("5. AUDIT LOG (token-only; no original values ever)")
    for event in audit.events[:8]:
        print(f"  {event.event:12} {event.entity_type:10} action={event.action:12} token={event.token}")
    if len(audit.events) > 8:
        print(f"  ... {len(audit.events) - 8} more")

    # destroy the session -> crypto-shred the vault; prior tokens become unrecoverable
    await manager.destroy(session.session_id)
    banner("6. LOGOUT -> VAULT CRYPTO-SHREDDED")
    print("  Session key zeroized. The token map is now unrecoverable — the tokens in step 2")
    print("  can never again be re-identified. Irreversibility is a cryptographic property.")


if __name__ == "__main__":
    asyncio.run(main())
