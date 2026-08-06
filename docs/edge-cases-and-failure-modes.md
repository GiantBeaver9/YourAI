# Edge Cases & Failure-Mode Register

The point of this document: find the bugs on paper, before the interfaces calcify
around a naive happy path. Every entry is also a **test we owe the suite.**

Failure classes (severity order):
- 🔴 **LEAK** — raw PII/PHI crosses the boundary. System-defining failure.
- 🟠 **CORRUPTION** — wrong value restored / silent data loss. Clinical-safety risk.
- 🟡 **CRASH** — pipeline dies. Availability, and a crash mid-flight can strand a vault.
- 🔵 **UTILITY** — over-redaction; model reasons on degraded input.

---

# PART A — Load-bearing decisions (resolve BEFORE writing interfaces)

These are the ones that, if discovered mid-build, force a rewrite. Each has my
recommended resolution and, where it's a genuine judgment call, the open question.

## A1. Coreference / surface-form collapse 🟠🔵 — THE hardest correctness problem

**Scenario.** "John Smith saw Dr. Smith. John was prescribed... He returned..." — one
patient appears as `John Smith`, `John`, `Mr. Smith`, `J. Smith`, `Smith, John`;
a *different* person is `Dr. Smith`. Naive HMAC-on-surface-string gives every form a
**different token**, so:
- the LLM loses the thread (5 tokens for 1 person → incoherent reasoning), 🔵
- de-obf restores inconsistently, and
- `John` (patient) and `Smith` (could match Dr. Smith) may **cross-link** → 🟠.

**Why it's load-bearing.** The HMAC input *is* the canonical form. If we don't decide
the canonicalization + within-document entity-linking strategy now, the token scheme,
the vault key, and the strategy interface are all built on sand.

**Recommended resolution.**
1. **Normalize before hashing:** casefold, strip titles (Mr/Dr/Ms), collapse
   whitespace/punctuation. `normalize("Mr. John Smith") -> "john smith"`.
2. **Within-document entity resolution** *before* tokenization: cluster mentions that
   refer to the same entity (person-name subset matching — "John" ⊂ "John Smith";
   longest mention is the canonical). One cluster → one token.
3. Tokenize the **cluster id**, not the surface string. Vault maps token → canonical
   original; each surface occurrence is replaced with the same token.

**Open judgment call for us:** how far do we take entity resolution in the *demo*?
Full coreference (pronouns "he/she") is a research problem. My proposal: **string-level
clustering only** (surface-form variants of the same name), explicitly document that
*pronominal* coreference is out of scope and handled by the model seeing one consistent
token per entity. Over-reaching here burns the whole budget. But we must be honest that
"John" appearing as both a name and a common word is a real ambiguity we accept.

⚠️ The danger of *over*-collapsing: "Dr. Smith" and "Ms. Smith" normalize toward "smith"
if we strip titles too aggressively → two people become one token → 🟠 clinical
cross-contamination. **Titles that distinguish people must be part of the cluster key.**
This tension (collapse variants vs. preserve distinct people) has no clean answer — it's
a precision/recall dial we set consciously and document.

## A2. Offset-safe replacement engine 🟠 — guaranteed bug if we wing it

**Scenario.** Replace a 16-char name with an 11-char token; every span offset *after*
it shifts by −5. Apply replacements left-to-right naively and every subsequent span
corrupts.

**Resolution (decide now, it shapes the engine interface):**
- Detection emits **immutable `(start, end, type, confidence)` spans** over the
  *original* text.
- A conflict-resolution pass produces a **non-overlapping, sorted** span set (see A3).
- Replacement builds the output in **one pass with an offset accumulator** (or applies
  spans right-to-left). Never mutate offsets in place across iterations.
- Replaced regions are **frozen** — never re-detected, never re-replaced (A6).

## A3. Overlap / nesting / conflict resolution 🔴🟠

**Scenario.** Presidio returns overlapping spans: `[John Smith]`(NAME) overlapping
`[Smith]`(NAME); a DATE inside an ADDRESS; a 9-digit run tagged both SSN and
US_BANK_NUMBER. Obfuscating overlapping spans corrupts offsets and can leave a
**partial entity** unmasked → 🔴.

**Resolution:** before obfuscation, resolve to a disjoint span set by priority:
1. highest confidence wins;
2. tie → longest span (mask the most);
3. tie → type priority (identifier > quasi-identifier > free-text);
4. **security default: on unresolved overlap, mask the UNION**, never the gap.

## A4. Token scheme + collision handling 🟠 — silent corruption at scale

**Scenario.** A 50-page brief has 200 entities. Token = `HMAC` truncated to 4 hex
(16 bits). Birthday bound → collision likely well under 200 entities. Two distinct
originals → same token → de-obf restores one as the other → 🟠, invisibly.

**Resolution:**
- Truncate to enough bits that collision is negligible at expected scale (**≥ 32 bits /
  8 hex**; compute the birthday bound in the README).
- **Collision is still checked, not assumed away:** vault stores the full HMAC. On
  insert, if the truncated token already maps to a *different* full-HMAC, **extend the
  truncation** for that entity (or append a disambiguating counter) and record it.
  Determinism-within-session is preserved because the extension is itself derived
  deterministically.
- Token grammar is a **strict, unambiguous regex** so de-obf can't half-match.

## A5. Under-detection & boundary leaks 🔴 — the security-first policy call

**Scenario.** Presidio tags `Smith` but not `John`; or a phone number split by a PDF
line-break is detected as two fragments, neither valid, both passed through; or a rare
name below the confidence threshold. **Any un-masked residue is a real leak.**

**Resolution — bias the whole system toward over-redaction:**
- Confidence-gated: below threshold → **redact** (block token), never pass through
  (the "graceful degradation" NFR, framed as a security default).
- Structured PII (SSN/account/MRN) via **validated** regex with *generous* boundaries;
  a validation failure on a PII-shaped string → redact anyway, don't emit it raw.
- Pre-detection text normalization to heal extraction artifacts (de-hyphenate
  line-broken tokens, join split digit runs) — see A8.
- Optional **paranoid pass:** near a detected entity, redact residual capitalized
  tokens / long digit runs. Costs 🔵 utility; document as a config knob.

**Open judgment call:** default threshold and whether paranoid mode is on in the demo.
I lean threshold ~0.5 with redaction below, paranoid off-by-default but demonstrated.

## A6. Detection idempotency 🟠

**Scenario.** Pseudonymization emits "Michael Torres"; a second detection pass tags it
as a NAME and re-obfuscates it → double-masking, or the pseudonym leaks into the vault
as if it were an original.

**Resolution:** replaced spans are **frozen/immutable**; the engine never re-runs
detection over output. Single detect → resolve → replace flow, no loops.

## A7. Session-key lifecycle & in-flight pinning 🟡🔴

**Scenarios.**
- Session TTL expires while the LLM call is in flight; response returns, de-obf needs
  the vault, it's gone → 🟡 crash or, worse, a fallback that emits raw-looking tokens.
- Process crash mid-session strands vault ciphertext under a lost key (acceptable by
  design — but must be clean, not a corrupt-state crash).
- Logout races an in-flight request.

**Resolution (shapes the Vault + Session interfaces):**
- `K_s` lives in a **session context** with a **lease/refcount**. An in-flight
  request holds a lease.
- Expiry/logout sets a `destroying` flag; actual key destruction waits for leases to
  drain, **or** in-flight de-obf fails with a safe typed error — **never** emits raw
  tokens, never emits originals.
- **HKDF-derive two subkeys** from `K_s`: `k_token` (HMAC) and `k_enc` (AES-GCM).
  Never use `K_s` directly for two purposes.
- **Fresh random 96-bit nonce per GCM encryption.** Determinism applies to *tokens*,
  never to encryption nonces — nonce reuse under one key is catastrophic.

## A8. Chunking must not split entities 🔴

**Scenario.** A name/number straddles a chunk boundary → each half detected separately
or not at all → 🔴 partial leak; or the same boundary entity is double-obfuscated.

**Resolution:** **detect + obfuscate on the full document text first**, *then* chunk the
**already-obfuscated** text, choosing boundaries that never fall inside a `[...]` token.
Ordering is fixed: `extract → normalize → detect → resolve → obfuscate → chunk →
assemble → LLM`.

## A9. Concurrency (async is required) 🟠

**Scenario.** Two async tasks process content containing the same "John Smith"; both
compute the (identical, deterministic) token; both write the vault entry → race.

**Resolution:** vault writes are **idempotent**, keyed by token; atomic upsert or a
per-session async lock. Determinism guarantees concurrent writers agree on the value,
so idempotency is sufficient — but it must be *designed in*, not assumed.

---

# PART B — Full failure-mode register (each → a test)

## Detection
- **B1** Overlapping/nested spans → A3. *test: overlap resolves to disjoint union.*
- **B2** Partial-name detection (`Smith` not `John`) → A5. 🔴 *test: no residual name.*
- **B3** Numbers that are/aren't PII — "2 tablets" vs account #; dosing interval vs DOB.
  Context-dependent; rules over/under-fire. *test: dosing numbers survive, IDs masked.*
- **B4** Invalid/test structured IDs (078-05-1120, Luhn-fail card) → validate-then-redact
  (A5). *test: malformed-but-PII-shaped is redacted, not emitted.*
- **B5** Unicode / accents / non-Latin / homoglyph & zero-width evasion → normalize
  (NFKC) pre-detection. *test: "Jоhn" with Cyrillic о is caught or redacted.*
- **B6** PII split by PDF line-break / hyphenation → pre-normalize (A8/A5). 🔴
- **B7** Repeated page headers/footers with patient name → dedup via coreference (A1).
- **B8** Same string, different entities in context ("May" name vs month) → confidence
  + context; accept residual ambiguity, document.

## Obfuscation
- **B9** Token collision at scale → A4. 🟠 *test: 500 entities, zero collisions restored.*
- **B10** Pseudonym that is itself valid PII (fake SSN = real SSN) → structured IDs are
  **tokenized/normalized to `000-00-0000`**, never "realistically" faked (per your call).
- **B11** Pseudonym collides with a real name elsewhere in the doc → draw from a
  disjoint fake namespace; check against detected set.
- **B12** Gender/number agreement — replacing a "she" antecedent with a male pseudonym
  breaks grammar *and* (B-clinical) drops sex, which is clinical signal. Pseudonyms
  preserve gender; sex preserved as explicit signal per ADR-3.
- **B13** Offset shift on unequal-length replacement → A2. 🟠
- **B14** Document literally contains `[PHI_NAME_a3f2]`-shaped text pre-obfuscation →
  escape/namespace our token grammar so real content can't be mistaken for a token.

## Vault
- **B15** Concurrent write race → A9. *test: concurrent obfuscation, one consistent map.*
- **B16** Session expiry mid-flight → A7. 🟡 *test: expired session → safe typed error,
  no raw output.*
- **B17** Cross-session isolation — replay session A's tokens into session B → B's vault
  misses (different `k_token` space). *test: B cannot resolve A's tokens.* 🔴
- **B18** Vault miss (unknown token) → leftover guard (B22), never crash, never guess.
- **B19** Crash mid-session → orphaned ciphertext is unrecoverable *by design*; state
  stays consistent. *test: no partial/corrupt vault entry survives.*
- **B20** Unbounded vault growth — eviction must not drop a token referenced by an
  in-flight response. Lease-aware eviction only.

## De-obfuscation
- **B21** Affixed/inflected tokens: `[..]'s`, `[..],`, `([..])`, `[..].` → affix-tolerant
  grammar. *test: possessive & punctuation-wrapped restore.*
- **B22** 🔴 **Leftover-token guard:** after restore, assert no token-grammar residue in
  user output. Residue → redact + audit + surface as failure; **never** pass a
  token-shaped string (or a guessed value) to the user. *test: hallucinated token blocked.*
- **B23** LLM mangles token (case, injected space, dropped bracket) → **conservative**
  match: case-insensitive type+hex, known affixes only. Do **not** fuzzy-match arbitrary
  edits — wrong restoration (🟠) is worse than a caught miss (B22). Mitigate at source by
  prompting the model to preserve tokens verbatim.
- **B24** Two tokens concatenated by the model → grammar requires delimiters; unmatched
  → B22.
- **B25** Coreference / indirect reference ("the patient's condition") → **unsolvable by
  replacement.** Named limit (your Q2). Optional prod mitigation: second-pass NER on the
  *response* to catch any real value the model regurgitated.
- **B26** Restore ordering when tokens could be substrings → grammar makes tokens
  non-overlapping; restore is single-pass.

## Pipeline / LLM
- **B27** Chunk boundary splits entity or token → A8. 🔴
- **B28** LLM invents new PII-shaped content in its response (a made-up SSN) → detect on
  the response before returning; decide redact vs allow. *test: response-side scan.*
- **B29** LLM call fails after obfuscation → retry reuses the same deterministic vault;
  no re-obfuscation needed. *test: idempotent retry.*
- **B30** Inference attack — model reconstructs identity from un-masked quasi-identifiers
  in combination (rare diagnosis + age + zip) → k-anonymity limit; tokenize
  high-linkage quasi-identifiers (ADR-3, your Q3). Named, not "solved."

## Audit / crypto
- **B31** Audit must contain token + type only, never originals. *test: grep audit for
  fixture PII → zero hits.* 🔴
- **B32** Frequency analysis over audit (token counts/types over time) → low-risk
  inference; name it, don't over-engineer.
- **B33** Async audit interleaving → session_id + monotonic sequence number per event.
- **B34** GCM nonce reuse → A7 (fresh nonce per write). *test: no two ciphertexts share
  a nonce.* 🔴
- **B35** Key-purpose separation (token vs enc) → A7 (HKDF subkeys). *test: subkeys differ.*

---

# What this changes about the build

The interfaces must carry, from line one:
- `DetectedEntity` with `(start, end, type, confidence, cluster_id)` — **cluster_id
  exists because of A1**; a naive interface without it can't do coreference.
- A **conflict-resolution stage** between detect and obfuscate (A3) — not an
  afterthought, a named pipeline step.
- A **frozen-span** notion so replacement is offset-safe and idempotent (A2, A6).
- `Vault` with **lease/refcount + typed errors** (A7, B16) — not a bare dict.
- HKDF subkey derivation + per-write nonce in the crypto layer (A7, B34/35).
- A **leftover-token guard** as a hard post-condition on the pipeline output (B22).

None of these appear in a happy-path scaffold. This is why we don't scaffold yet.
