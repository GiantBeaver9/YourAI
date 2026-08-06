# Reconciliation Punch-List (from adversarial review)

Surviving findings from a hostile completeness review. WIP-whining and already-owned items
(async-at-the-edges, heuristics-as-tradeoffs) filtered out. Status tracked here.

## Decisions locked this session
- **R4 token grammar → bracketed `[TYPE_hex]`** (unambiguous guard > fluency; type field aids restore).
- **R2 guard** → derive regex from the *same* grammar constant as the tokenizer; auto-resolved by R4.
- **R10 collision** → fixed width ≥32 bits, **delete the extend path** (no variable length → determinism holds).
- **R1 coreference** → wire string-clustering before HMAC, hash the cluster canonical; delete "free collapse"/"overscrub".
- **R6 detector (proposed, pending push-back)** → deterministic rules core IS the demo detector (labels + magnitude + structured-ID regex + rules engine over line-based text); Presidio = prose-name mop-up only; CUT expensive layout parse (PDF coords, table columns, multi-column) as designed-not-built.
- **R3 injector** → NOT assumed; drawn from scratch *after* R6 settles (it assembles detector output).

## Must-fix (real holes, load-bearing)

| # | Finding | Files | Fix | Status |
|---|---|---|---|---|
| R1 | **Coreference asserted free, never wired.** Formula hashes `normalize(value)`, not a cluster id; "John Smith"/"John"/"Mr. Smith" → 3 tokens. "Overscrub similar forms" contradicts frozen-spans. | ADR-2/ADR-3, session-model §1, edge-cases A1/A2/A6 | Wire string-clustering *before* HMAC OR scope to exact-normalized + document the gap. **DECISION NEEDED.** | open |
| R2 | **Leftover-guard regex doesn't match emitted token.** Guard = `[A-Z]+_..._[0-9a-f]+`; token = `patient_[0-9a-f]{n}`. Leaked token sails past. | de-obf L22/L37, edge-cases B22, ADR-5 | Pin ONE grammar; derive guard from the *same constant* the tokenizer uses; test guard vs an emitted token. **DEPENDS ON R4.** | open |
| R3 | **LLM injector undrawn + preserve-vs-de-link contradiction + "zero PII in transit" oversold.** | ADR-3, ADR-4, regulatory | Draw the payload envelope (obfuscated block + facts sidecar + preservation prompt); pick preserve-inline OR extract-sidecar; restate NFR: "zero *direct-identifier* PII in transit; QIs under audited policy." **DECISION NEEDED.** | open |
| R4 | **Token grammar never pinned** (ADR-2b flagged it open; 3 docs assume resolved, 2 directions). `patient` is a common English word → de-obf ambiguity. | ADR-2b, session-model, de-obf | Pin grammar (see fork below). Cascades to R2. **DECISION NEEDED.** | open |
| R5 | **Structural-vs-NER conflict resolution: two schemes.** "Structural runs first & wins/frozen" vs A3 "highest confidence wins." Low-conf mis-parse beats high-conf NER. | structural §2, edge-cases A3/A6 | Give structural spans real confidence; let A3 arbitrate uniformly; specify mis-anchor (P4) / over-grab (P7) recovery. | open |
| R6 | **Demo detector ambiguous: Presidio (ADR-1) vs bespoke rule engine (structural + rules-engine docs).** Can't build both in budget. | ADR-1, structural, rules-engine | Pick one for demo. Buildability says Presidio + label-regex + 1 custom recognizer. **DECISION NEEDED.** | open |

## Cleanups (mechanical, no decision)

| # | Finding | Status |
|---|---|---|
| R7 | "Settled decisions" said DOB → keyed date-shift (stale; now generalize-to-year) | ✅ fixed |
| R8 | ADR-6 listed Fernet as acceptable (AES-128, fails AES-256 req) | ✅ fixed |
| R9 | Token width 16/24/32 bits inconsistent; A4's ≥32 never reached keystone | open — pick fixed width w/ R4 |
| R10 | Collision-extend breaks fixed grammar AND determinism (insertion-order under async) | open — **delete the extend path; use a fixed width big enough that collision is negligible** (anti-bloat fix) |
| R11 | "Confidence-gated degradation everywhere" — only on Presidio residue path, not structural | open — reword |

## Thin (defensible, tighten for the room)

- **Pseudonymization is graded (25%) but docs argue against building it** → build a real (simple) one behind the ABC anyway; don't only argue against it.
- **Q1 (scale) partly reduces to "we reject the multi-column brief"** → need a stronger scale answer than refusal for the PRD's marquee scenario.
- **Q4 (vault SPOF) holds only in prod**; demo has K_s + vault in one process, live-process compromise unaddressed.
- **Chunking mechanics** (size, overlap, response stitching, sidecar-per-chunk) unspecified.

## MVP line (the review's most useful output — adopt at top of README)

Build ONLY: in-memory vault (K_s→HKDF→k_enc/k_token, AES-256-GCM entries, destroy-on-logout);
Presidio + label-regex + 1 custom recognizer + confidence redaction; tokenize + a simple
real pseudonymize behind the ABC + generalize-to-year dates; token-grammar regex + affix +
matching guard; per-user-key AES-256-GCM file store; JSON-lines token-only audit; Hypothesis
leakage test + 5 scenario tests. **Cut from demo:** RE2 rule-intake + exrex preview panel,
PDF x-coordinate columns + reading-order reconstruction, multi-column reject classifier,
viral-load unit-adjacency, versioned ObfuscationPolicy object (→ plain dict), coreference
beyond exact-normalized (string clustering only if time). Everything cut = README prose.
