#!/usr/bin/env python3
"""End-to-end demo on the bundled synthetic fixture — no API key required.

Run: ``python demo.py``  (uses the deterministic MockProvider by default; set
``ANTHROPIC_API_KEY`` to route the LLM leg to a real provider.)

Prints, in order: the detected entities (types only), the obfuscated payload that would leave
our infrastructure (proving zero PII at the wire), the mock LLM reply carrying tokens back, and
the restored, user-facing output.
"""

from __future__ import annotations

import asyncio
import os

from secure_context_pipeline import ObfuscationPolicy, SecureContextPipeline, Settings
from secure_context_pipeline.detection.coreference import (
    cluster_names,
    propagate_names,
    resolve_overlaps,
)
from secure_context_pipeline.fixtures import FIXTURE_DOC_ID, FIXTURE_PII, FIXTURE_PRESERVED
from secure_context_pipeline.fixtures.clinical_note import FIXTURE_TEXT

RULE = "─" * 78


def _header(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


async def main() -> None:
    # Deterministic master key for a reproducible demo run (prod: env/KMS).
    settings = Settings(
        master_key=bytes.fromhex(os.environ.get("SCP_MASTER_KEY", "00" * 32)),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        store_root=os.environ.get("SCP_STORE_ROOT", "store_data"),
    )
    pipe = SecureContextPipeline(settings=settings, policy=ObfuscationPolicy())

    print(f"Detection substrate : {pipe.detector.name}")
    print(f"LLM provider        : {pipe.provider.name}")

    _header("1. DETECTED ENTITIES (types only — values never printed)")
    ents = resolve_overlaps(propagate_names(FIXTURE_TEXT, resolve_overlaps(pipe.detector.detect(FIXTURE_TEXT))))
    cluster_names(ents)
    by_type: dict[str, int] = {}
    for e in ents:
        by_type[e.entity_type.value] = by_type.get(e.entity_type.value, 0) + 1
    for etype, count in sorted(by_type.items()):
        print(f"  {etype:14} x{count}")

    # --- ingest to encrypted store, then run the round-trip under a session lease ---
    await pipe.ingest("demo-user", FIXTURE_DOC_ID, FIXTURE_TEXT)
    session = pipe.sessions.create_session("demo-user")
    result = await pipe.process(
        session, FIXTURE_DOC_ID,
        "Summarize this patient's status and current therapy in two sentences.",
        user_id="demo-user",
    )
    obf = result.obfuscation

    _header("2. OBFUSCATED PAYLOAD (this is what leaves our infrastructure)")
    print(obf.obfuscated_text)

    _header("3. WIRE CHECKS")
    leaks = [v for v in FIXTURE_PII.values() if v in obf.obfuscated_text]
    kept = [v for v in FIXTURE_PRESERVED.values() if v in obf.obfuscated_text]
    print(f"  PII values leaked to payload      : {len(leaks)}  {leaks}")
    print(f"  clinical values preserved (of {len(FIXTURE_PRESERVED)}) : {len(kept)}  {kept}")
    print(f"  verify-before-send                : PASSED (payload shipped)")

    _header("4. MOCK LLM REPLY (opaque tokens carried back verbatim)")
    print(result.raw_response)

    _header("5. RESTORED, USER-FACING OUTPUT (tokens resolved via the session vault)")
    print(result.restored_text)
    print(
        f"\n  tokens restored={result.deobfuscation.tokens_restored} "
        f"vault_misses={result.deobfuscation.vault_misses} "
        f"leftover_guard_fired={result.deobfuscation.leftover_guard_fired}"
    )

    _header("6. AUDIT TRAIL (token-only; sample of 6 events)")
    for line in pipe.audit.as_jsonl().splitlines()[:6]:
        print(f"  {line}")

    # Logout: crypto-shred the session vault.
    await pipe.sessions.destroy_all()
    print("\nSession destroyed — vault crypto-shredded (K_s zeroized).")


if __name__ == "__main__":
    asyncio.run(main())
