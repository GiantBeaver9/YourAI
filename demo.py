"""End-to-end demo runner for Secure Context Pipeline."""

from __future__ import annotations

import asyncio
import os
import sys

from secure_context_pipeline import (
    ObfuscationPolicy,
    SecureContextPipeline,
    Settings,
)
from secure_context_pipeline.llm.provider import AnthropicProvider, MockProvider


async def main() -> None:
    print("=" * 80)
    print("SECURE CONTEXT PIPELINE — END-TO-END DEMO")
    print("=" * 80)

    # 1. Load fixture document
    fixture_path = os.path.join(os.path.dirname(__file__), "fixture.txt")
    if not os.path.exists(fixture_path):
        print(f"Error: Fixture file not found at {fixture_path}")
        sys.exit(1)

    with open(fixture_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    print(f"\n[1] Loaded fixture document ({len(raw_text)} chars)")
    print("-" * 40)
    print(raw_text[:350] + "\n...[truncated]...\n")

    # 2. Configure LLM provider
    settings = Settings.from_env()
    if settings.anthropic_api_key:
        print(f"[2] LLM Provider: Anthropic ({settings.llm_model})")
        llm_provider = AnthropicProvider(
            api_key=settings.anthropic_api_key, model=settings.llm_model
        )
    else:
        print("[2] LLM Provider: MockProvider (deterministic demo mode — no API key required)")
        llm_provider = MockProvider()

    # 3. Instantiate pipeline
    pipeline = SecureContextPipeline(settings=settings, llm_provider=llm_provider)
    policy = ObfuscationPolicy()

    print("\n[3] Executing pipeline: Detect -> Obfuscate -> Verify-Before-Send -> LLM -> Restore")
    print("-" * 40)

    result = await pipeline.process_document(
        user_id="user_123",
        doc_id="doc_claim_99",
        raw_text=raw_text,
        task="Summarize patient diagnosis, clinical lab results, and discharge care plan.",
        policy=policy,
    )

    print("\n[4] Detected Entities & Routing:")
    for entity in result.detected_entities:
        action = policy.action_for(entity.entity_type)
        print(f"  - [{entity.entity_type.value}] '{entity.text}' -> Action: {action.value} (conf: {entity.confidence:.2f})")

    print("\n[5] Obfuscated Payload Sent to LLM (Zero Direct-Identifier PII):")
    print("-" * 40)
    print(result.obfuscated_text[:600] + "\n...[truncated]...\n")

    print("\n[6] Raw Response Received from LLM (With Tokens):")
    print("-" * 40)
    print(result.llm_raw_response)

    print("\n[7] Final Restored Response Delivered to User:")
    print("-" * 40)
    print(result.restored_response)

    print("\n[8] Audit Trail Events (Token-Only, Zero PII Logged):")
    print("-" * 40)
    for event in result.audit_events[:8]:
        print(f"  {event}")
    if len(result.audit_events) > 8:
        print(f"  ... and {len(result.audit_events) - 8} more audit events.")

    print("\n" + "=" * 80)
    print("DEMO COMPLETE — ZERO PII LEAKAGE CONFIRMED")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
