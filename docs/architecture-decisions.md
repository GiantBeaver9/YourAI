# Architecture Decisions — Secure Context Pipeline

Working notes for the YourAI take-home. Each decision is framed **demo choice vs.
production choice**, because the point of this exercise is not "which library did
you import" — it's "do you understand the threat model well enough to know when the
demo answer and the prod answer differ, and why."

Legend: 🎯 = what the rubric rewards · ⚠️ = the trap · 🔒 = security-critical

---

## ADR-0: What is actually load-bearing

The rubric weights tell us where the grade lives:

| Dimension | Wt | Where it's earned |
|---|---|---|
| Security Architecture | 30% | Vault crypto + zero-PII-in-transit |
| Obfuscation Quality | 25% | Deterministic-in-session / non-det-across |
| De-obfuscation | 20% | Restore tokens incl. inflected forms |
| Code Quality | 15% | Typed interfaces, pluggable strategies, async |
| Critical Thinking | 10% | This document, essentially |

**75% is vault + obfuscation + de-obfuscation.** Detection, store, Docker, PDF
parsing are *supporting cast*. We build the core to production-grade reasoning and
keep the edges thin-but-real. ⚠️ The failure mode of a mid candidate is a beautiful
PDF ingester and a toy vault.

---

## ADR-1: Detection engine (the one with a real tradeoff)

Detection is the **recall-critical weakest link**: a false negative means raw PII
leaks to the provider. Precision failures are cheap (over-redaction, mild utility
loss); recall failures are the whole-system failure. This asymmetry drives every
call below.

### Options considered

**A. Microsoft Presidio**
- ➕ Batteries-included recognizers for most required types (NAME, EMAIL, PHONE, SSN,
  CREDIT_CARD, US bank/DL/passport, DATE_TIME, medical license…).
- ➕ Every result carries a **confidence score** → graceful-degradation
  (redact-below-threshold) is free and principled.
- ➕ First-class **custom `EntityRecognizer` registry** → "add PASSPORT_NUMBER = config
  only" is *genuinely true*, not aspirational. Directly satisfies the extensibility NFR.
- ➕ Deterministic + offline + auditable — no PII leaves the box to detect PII.
- ➖ Heavier install (pulls spaCy model), NER recall on messy clinical text is
  moderate, PHI-specific concepts (diagnoses, procedures) are weak out of the box.

**B. spaCy + hand-rolled rules**
- ➕ Light, total control, fast.
- ➖ You rebuild confidence scoring, the recognizer-registry pattern, and every
  structured-ID regex yourself. Same coverage, more code, more of *your* bugs in the
  security-critical path. Presidio *is* spaCy underneath + the scaffolding you'd write.

**C. LLM-based detection**
- ➕ Best semantic recall — catches privileged *strategy*, implied diagnoses, the
  fuzzy Legal/PHI concepts regexes can't.
- ➖ 🔒 **Self-defeating with a remote LLM**: to find the PII you ship raw PII to a
  provider — the exact boundary this system exists to protect. Only defensible with a
  **local** model. Non-deterministic, higher latency/cost, and hard to prove "no PII in
  transit" when detection itself is a model call.

**D. Cloud NER (AWS Comprehend / Comprehend Medical)**
- ➕ Comprehend *Medical* is genuinely strong on PHI (ICD-10, RxNorm linkage).
- ➖ Same boundary violation as (C) unless it's in your own VPC/BAA, plus vendor
  lock-in and per-call cost. A prod option under the right contract, not a demo one.

### Decision

- **Demo → Presidio.** Best coverage-per-hour, confidence scores wire straight into
  graceful degradation, custom-recognizer story proves the extensibility NFR live,
  and it keeps detection *inside the boundary* — which is thematically the whole point.
- **Prod → layered defense-in-depth, because recall is existential:**
  1. **Rules/regex** for structured, high-confidence IDs (SSN, MRN, account #, tax ID) —
     deterministic, near-perfect precision, cheap.
  2. **Presidio/spaCy NER** for names, locations, orgs.
  3. **Local LLM** (e.g. an on-prem Llama/clinical model) as a **recall backstop** for
     the semantic long tail — privileged strategy, implied PHI — that never leaves the
     VPC. This is why prod ≠ demo: the demo can't justify a whole local-model deployment,
     but prod's threat model demands the extra recall layer *inside* the boundary.
  4. **Confidence-gated degradation everywhere:** below threshold → **redact**, never
     pass through. (Satisfies the "graceful entity degradation" NFR as a security default,
     not a nicety.)

🎯 The narrative "demo = Presidio for reproducibility; prod = spaCy/rules + **local**
LLM for recall inside the trust boundary" is exactly the senior signal — it shows you
know that a remote LLM detector reintroduces the very leak you're preventing.

**Open question for you:** do we ship even *one* custom recognizer in the demo (e.g. a
synthetic MRN format) to prove the extensibility path end-to-end? I'd argue yes — it's
~20 lines and it's the difference between claiming extensibility and demonstrating it.

---

## ADR-2: Vault + crypto — the crown jewel (30% lives here)

The constraint "reversible within a session, irreversible across sessions" has one
clean design that *also* hands you deterministic-in-session / non-deterministic-across
obfuscation for free:

**Per-session key, never persisted next to the data.**

- On session start: mint a random **session key** `K_s`, held in a session-scoped
  store (memory / short-TTL cache). It is the root of trust for the whole session.
- **Token ID** = `HMAC(K_s, normalize(entity_value))`, truncated →
  `[PHI_NAME_a3f2]`. Properties, all from one primitive:
  - *Deterministic within session*: same value → same token (natural coreference
    collapse — "John" appears 20×, one token).
  - *Non-deterministic across sessions*: different `K_s` → different token. 🔒
  - *One-way*: HMAC leaks nothing about the input; the token is not a hash the
    adversary can dictionary-attack without `K_s`.
- **Reverse map** `token → original` is stored encrypted under a key **derived from
  `K_s`** (AES-256-GCM via the `cryptography` lib; HKDF to split token-derivation and
  encryption subkeys). Destroy `K_s` on logout → the vault ciphertext is
  mathematically unrecoverable. **"Irreversible across sessions" becomes a
  cryptographic property, not a policy promise.** That's the sentence that wins Q6.

### Demo vs prod

| | Demo | Prod |
|---|---|---|
| `K_s` storage | in-memory dict / SQLite row, key in process memory | **KMS/HSM-wrapped**; envelope encryption, `K_s` never in app memory in plaintext longer than a call |
| Vault backend | SQLite (+ optional SQLCipher) | Postgres/Redis w/ per-session encryption, short TTL, auto-purge on logout |
| Key lifecycle | destroy on logout | KMS `GenerateDataKey` per session; scheduled crypto-shredding; access-logged |
| Isolation | separate key per session obj | per-session KMS grant; Session B literally lacks the grant to decrypt Session A |

🔒 Q4 (vault as single point of failure) answer: the vault stores *ciphertext +
HMAC tokens*, never plaintext-recoverable-without-`K_s`. Compromising the vault store
without the session-key custodian (KMS/HSM) yields tokens and ciphertext, not PII.
Defense in depth: split custody of the token map (vault DB) from the key (KMS).

---

## ADR-3: Obfuscation strategies — tokenization vs pseudonymization

Shared `ObfuscationStrategy` ABC; both selectable by config; zero harness changes to
add a third. The README needs a per-entity-type comparison — here's the reasoning:

| Entity type | Tokenization `[PHI_...]` | Pseudonymization (realistic fake) | Recommendation |
|---|---|---|---|
| **SSN / MRN / account #** | ✅ opaque, obviously non-real, zero re-identification surface | ⚠️ a fake SSN still *looks* like an SSN → risk the model treats it as real / a real person's number by collision | **Tokenize.** Structured secrets want opacity. |
| **NAME** | ⚠️ `[PHI_NAME_a3f2]` degrades LLM fluency/coreference; models handle bracket-tokens worse than names | ✅ "Michael Torres" keeps grammar, pronoun agreement, narrative flow → better model reasoning | **Pseudonymize.** Utility win, and the fake carries no real-person link within session. |
| **DIAGNOSIS / condition** | ✅ safe but the model can't *reason* about `[PHI_DIAGNOSIS_x7a]` clinically | ⚠️ a plausible fake diagnosis could mislead clinical reasoning / hallucinate | Context-dependent — lean tokenize; discuss. |
| **DATE / DOB** | ⚠️ breaks temporal reasoning ("how long since…") | ✅ **date-shifting** (consistent offset per session) preserves intervals while hiding absolutes | **Pseudonymize via shift.** Classic HIPAA-safe-harbor move. |

🎯 The insight to surface: **the choice is a utility-vs-linkage tradeoff, and it's
per-entity, not global.** Tokenization maximizes safety and kills semantic utility;
pseudonymization preserves utility but the realistic value is itself a linkage risk if
the mapping ever leaks (Q3). Best systems route per entity type — which our config-driven
strategy selection makes trivial.

🔒 Q3 (adversary has obfuscated doc + response): with pseudonymization they see a
*coherent fake* — they can infer structure (there's one patient, one physician, a
diagnosis) and possibly re-identify via **quasi-identifier combination** (rare
diagnosis + age + zip). Mitigations to name: per-session non-determinism (can't
correlate across sessions), tokenize the high-linkage quasi-identifiers even when
pseudonymizing names, and note that this is *k-anonymity territory* — obfuscation
reduces but doesn't eliminate inference risk. Saying that out loud is the 10%.

---

## ADR-4: LLM injector / provider leg

- **Interface first:** `LLMProvider` protocol (`async complete(payload) -> str`).
  Anthropic and a `MockProvider` both implement it.
- **Demo → real Anthropic call behind the interface, MockProvider as the default** so
  `demo.py` and the test suite run with **zero key** and zero flakiness, but a real
  round-trip is one env var away. This is the "reviewers can run it AND you can show a
  genuine provider round-trip" position — don't hard-wire a live call into tests.
- 🔒 The injector is where "zero PII in transit" is *verified*, not assumed: the
  payload assembler runs **only obfuscated chunks**, and a test asserts the outbound
  body contains none of the fixture's original values (the "100 runs, zero leakage"
  benchmark). Treat that assertion as a security control, not a unit test.

### Demo vs prod

| | Demo | Prod |
|---|---|---|
| Providers | Mock default + Anthropic | Multi-provider w/ failover, per-tenant routing |
| Transport | single async call | streaming, retries w/ backoff, timeout budgets, circuit breaker |
| Leak defense | pre-send assertion in tests | **egress proxy / DLP scan on the wire** as belt-and-suspenders + zero-retention contract |
| Prompt | obfuscated context + task | + instructions to preserve tokens verbatim (helps de-obf recall) |

⚠️ Note the ordering constraint for chunked docs: obfuscate **before** chunking so a
token is never split across a chunk boundary.

---

## ADR-5: De-obfuscation (20%) — where "good" separates from "correct"

- Base case: regex over the **token grammar** `[TYPE_SUBTYPE_hex]` with **affix
  tolerance** — possessives `[..]'s`, adjacent punctuation, casing. Vault lookup per
  match, `< 5ms` target (in-memory map → trivially met).
- 🔒 **Leftover-token guard:** after restoration, assert no `[A-Z]+_..._[0-9a-f]+`
  pattern survives in user-facing output. A token leaking to the user = correctness
  failure *and* a signal the vault missed. Fail loud.
- **The honest limit (Q2 — coreference):** the model writes "the patient's condition"
  instead of `[PHI_DIAGNOSIS_x7a]`. That's an *indirect* reference with no token to
  restore — genuinely unsolvable by token replacement. Don't fake a fix. Name the
  mitigations: (a) prompt the model to reference entities by token; (b) tokens survive
  better than you'd think because models copy opaque strings verbatim; (c) prod could
  run a second-pass NER on the *response* to catch any real values the model
  regurgitated. Stating this boundary clearly beats pretending regex solves coreference.

---

## ADR-6: Store & audit (thin but real)

- **Store:** encrypted file store, `cryptography` Fernet (AES-128-CBC+HMAC) or AES-256-GCM,
  **per-user key** (KDF from user secret / KMS in prod). TXT fully; PDF/DOCX behind a
  `DocumentExtractor` interface (pypdf / python-docx) — the interface is the point, edge-case
  parsing is not. Prod: S3/GCS + KMS SSE, per-user prefixes.
- **Audit:** append-only structured log (JSON lines). Every obfuscate/de-obfuscate
  event: `timestamp, session_id, entity_type, token, action` — 🔒 **never the
  original value.** A test greps the audit output for fixture PII and asserts zero
  hits. Prod: tamper-evident (hash chain / WORM), separate retention.
- 🔒 Q5 (HIPAA auditor, "prove no PHI hit the provider in 30 days"): the audit log
  shows *what was tokenized and when* (counts, types, sessions) — evidence the pipeline
  ran. **Gap to admit:** it proves obfuscation *executed*, not that detection had 100%
  recall; a missed entity is invisible to the log. Closing that gap = the egress DLP
  scan from ADR-4 + detection eval metrics. Knowing the gap is the point.

---

## Proposed repo layout (matches the PRD spec)

```
/store          encrypted upload/retrieval + DocumentExtractor interface
/detection      Presidio wrapper + custom recognizers + config-driven registry
/obfuscation
  /strategies   tokenization.py, pseudonymization.py (share ABC)
  /engine       detect → select strategy per entity type → emit
/vault          session key mgmt + AES-GCM token↔original map + destroy-on-logout
/pipeline       async orchestration: ingest → obfuscate → LLM → restore
/deobfuscation  token grammar + affix-tolerant restore + leftover guard
/audit          token-only structured logging
/tests          happy path, entity-not-found, vault miss, expired session, concurrent isolation
docker-compose.yml
demo.py         end-to-end on bundled fixture
README.md       threat model, ADR summaries, entity-type comparison, gaps, next-day
```

## Time-box (~6.5h focus)

1. Scaffold + typed interfaces + config — 0:30
2. 🔒 Vault + both strategies + unit tests — 1:30
3. Detection (Presidio + 1 custom recognizer + degradation) — 1:00
4. Pipeline + injector + de-obfuscation + leftover guard — 1:00
5. Store + audit — 0:45
6. Required tests + demo.py — 0:45
7. README (fold in these ADRs) — 0:45

## Open questions to settle before build

1. Ship one custom recognizer in the demo to *prove* extensibility? (I say yes.)
2. Per-entity strategy routing in the demo, or global-with-config + discuss routing in
   README? (Routing is ~1 config map — I'd do it; it's a visible differentiator.)
3. SQLCipher vs. app-layer AES-GCM for the vault store — the crypto reasoning matters
   more than the choice; app-layer AES-GCM is easier to *show* being applied correctly.
