# BUILD SPEC — authoritative build order for the implementing agent

This is the contract the build agent follows. The design docs are the *why*; this is the
*what and in what order*. **Honor the parked foundation** (`secure_context_pipeline/` on the
branch) — extend its patterns, don't rewrite to the median. When a HOW-decision isn't
specified here, choose the non-median option (below), not the tutorial default.

Stack: Python 3.10+, asyncio, pytest + pytest-asyncio, `cryptography`, `faker`, `hypothesis`.
**Presidio is the detection substrate** (pinned dependency) — it does the detection; our
rules-as-data compile into its recognizers. We are a harness around Presidio, not a
replacement for it. A native-regex path exists only as a degraded fallback if Presidio is
absent (so keyless CI can run), never as the default. No secrets hardcoded — all via env/.env.

---

## The four real interfaces (everything else concrete — no manager-of-managers)
1. `ObfuscationStrategy` (ABC) — `Tokenize | Pseudonymize | Generalize`.
2. `Detector` (Protocol) — `PresidioDetector` is the substrate (rules-as-data compile into
   its recognizers); a native-regex `RuleEngineDetector` is the degraded fallback if Presidio
   isn't installed.
3. `LLMProvider` (Protocol) — **LLM-agnostic**: `MockProvider` (default) | any real provider
   behind the protocol (Gemini, Anthropic, a local model). Provider choice is config, not code.
4. `SessionVault` — concrete; Protocol only at the prod-swap seam (Redis).

A fifth abstraction must be *earned*, not speculative.

---

## Deliberate non-median calls (the build MUST honor these)
- **async only at true I/O seams** (LLM, store, prod-vault) — parse/obfuscate core is sync and honest, not async-painted.
- **offset-safe replacement** — apply over sorted disjoint spans right-to-left (or rebuild with an offset accumulator). NEVER a naive `str.replace` loop.
- **fail-closed as types** — `SessionClosed`; vault-miss → `None` → leftover-guard fires; never swallow.
- **single-source token grammar** — tokenizer AND de-obf guard both import from `grammar.py`. Never a hand-copied regex literal.
- **policy injected explicitly** — `ObfuscationPolicy` passed down; no globals/singletons.
- **verify-before-send** — required gate; scan payload for any known original, fail closed.
- **crypto-shred** — vault entries AES-256-GCM under `k_enc`; destroy zeroizes the key.

---

## Build order (dependency order — each has passing tests before the next)

1. **`grammar.py`, `entities.py`, `config.py`** — DONE in foundation. Token format `[TYPE_hex]`
   (12 hex / 48-bit, no extend path). `ObfuscationPolicy` object. Keep as-is.
2. **`vault/`** — DONE in foundation. `KeyRing` (HKDF→k_token/k_enc, **per-document `k_doc =
   HKDF(k_token, doc_id)`**, AES-GCM, one-way digest), `SessionVault` (encrypted token→original),
   `Session`/`SessionManager` (lifecycle + leases). Tokens are per (session, document, entity):
   `session.token_for(canonical, tag, doc_id)`. Cross-session AND cross-document non-linkability.
3. **`obfuscation/strategies/`** — `ObfuscationStrategy` ABC + three:
   - **Tokenize**: `make_token(type_tag, keyring.token_digest(canonical))`. Reversible → vault entry. Bias-neutral.
   - **Pseudonymize**: Faker seeded by `keyring.token_digest(canonical)` → deterministic-in-session fake; gender-preserving for names; disjoint fake namespace. Reversible → vault entry. **BUILD THIS WELL — it's 25% graded even though we default to tokenize.**
   - **Generalize**: dates → year; age branches (≤ floor → route-human signal; ≥ ceiling → delete all dates + age→"90+"; else year). One-way, NO vault entry.
4. **`detection/`** — `Detector` protocol; **`PresidioDetector` is the substrate**:
   - Rules-as-data **compile into Presidio recognizers** (`PatternRecognizer` + context words for anchors, custom `EntityRecognizer` where needed) and register into Presidio's `RecognizerRegistry`. Presidio runs detection. Safe Harbor set: SSN/phone/email/MRN(label)/account/ZIP/etc. A native-regex fallback covers the same rules only when Presidio is absent.
   - **Magnitude rule**: 5+ consecutive digits → scrub; leading zeros → scrub; separator formats (dashed SSN, grouped phone) via regex; short separator-free → keep. **Unit-adjacency carve-out**: keep a 5+ run if a clinical unit token is adjacent (viral load / cell counts).
   - **Coreference**: string-level clustering BEFORE tokenization — cluster same-entity surface forms (subset match for names), assign `cluster_id`, tokenize the cluster canonical (longest). NOT pronominal.
   - **Confidence-gated redaction**: below `policy.confidence_threshold` → REDACT, never pass through.
   - Guard the Presidio import so a missing install falls back to native regex rather than crashing CI — but Presidio is the intended substrate and is pinned in requirements/docker.
5. **`obfuscation/engine/`** — orchestrate: detect → resolve overlaps (disjoint union) → cluster → route by `policy.action_for(type)` → offset-safe replace → freeze spans → emit token-only audit events.
6. **`deobfuscation/`** — token-grammar regex (from `grammar.py`) + affix tolerance (brackets delimit, so affixes are free) → vault.resolve → replace. **Leftover guard**: `grammar.has_token_residue` post-restore → fail closed. Vault-miss → never guess. Response-side net for pseudonyms (search known fakes) only when pseudonymization was used.
7. **`audit/`** — JSON-lines sink; event = `{ts, session_id, entity_type, token, action, policy_version}`. **NEVER original values.** A test greps output for fixture PII → zero hits.
8. **`store/`** — envelope encryption: master (env) → per-user KEK → per-doc DEK; AES-256-GCM with AAD=(user_id,doc_id,key_version); per-user-key isolation; file-based under `store_root`. Extracted plaintext never persisted unencrypted.
9. **`llm/`** — `LLMProvider` protocol; `MockProvider` (deterministic, echoes tokens back in a plausible reply — used by tests/demo, keyless); one or more real providers behind the protocol (Gemini, Anthropic, …), selected by config/env — the leg is LLM-agnostic. `ContextInjector.build_request(obfuscated_text, task, known_originals) -> LLMRequest` — inline (no sidecar), token-preservation system prompt, **verify-before-send**. Chunking: single-call when it fits; else `asyncio.gather` bounded by semaphore, results in input order (chunk i → slot i), obfuscate-whole-first so vault is read-only during fan-out (no locks); per-chunk verify.
10. **`pipeline/`** — async orchestration: (store read) → detect → obfuscate → inject → LLM → de-obf → restore. Returns user-facing restored text. Holds a session lease across the round-trip.
11. **`tests/`** — pytest + pytest-asyncio:
    - **Leakage property test** (Hypothesis): generate docs with ground-truth synthetic PII (Faker, seeded) → assert no original value (exact + fragment + normalized) in the outbound payload. Oracle tested with a planted leak (must fail).
    - **5 required scenarios**: happy path; entity-not-found; vault miss; expired session; concurrent session isolation (same value → different tokens; B can't resolve A's token).
    - **Core properties**: deterministic-in-session, non-deterministic-across, round-trip identity, affix restore, leftover-guard soundness, audit PHI-free.
12. **`demo.py`** — end-to-end on the bundled fixture; prints detected entities, obfuscated payload (show zero PII), the (mock) LLM reply with tokens, and the restored output.
13. **`docker-compose.yml` + `Dockerfile`** — `docker-compose up` runs the demo/tests reproducibly.
14. **`README.md`** — see README (assemble from the design corpus).

---

## MVP vs designed-not-built (do NOT build these — README-prose only)
- PDF x-coordinate column clustering, table parsing, multi-column reading-order reconstruction.
- RE2 rule-intake validator + `exrex` example/preview UI (whole rule-authoring surface).
- OCR of scanned pages (image pages → **quarantine** + `image_quarantine_threshold`).
- map-reduce chunking for global reasoning over over-context docs.
- KMS/HSM (demo master key from env; note prod swap).

## Fixture (build it)
One synthetic clinical/legal doc exercising: inline labeled fields, a multi-field line, a
stacked-label block, a small table, a signature block, running headers, and a prose paragraph
with embedded PII + protected clinical numbers (a dose, a viral load). No real PII.

## Definition of done
`docker-compose up` (or `pip install -e . && python demo.py`) runs the fixture end-to-end;
`pytest` green; outbound payload provably PII-free; tokens fully restored; audit PHI-free.
