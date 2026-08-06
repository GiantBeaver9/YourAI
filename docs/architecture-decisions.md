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

## THESIS: determinism is the security control

YourAI's own framing is the key: *"contractual guarantees are not sufficient — we
need a **technical** guarantee."* Extend that one step and it indicts the popular
answer to this whole challenge:

> **A probabilistic detector is not a sufficient technical guarantee either.**

The crowd reaches for "use an LLM to find and redact the PII." That move quietly makes
a model's *recall* the security boundary — and if the model is remote, you've **leaked
raw PII to detect the leak.** Worse, you can't *prove* anything about a stochastic
component: you can't run it 100× and assert byte-identical safe output, you can't
reproduce a leak to fix it, you can't hand an auditor a deterministic guarantee.

Our thesis: **the security-critical path is deterministic and rule-anchored.**
- The load-bearing transform is a **keyed HMAC** — same input + same session key →
  same token, every single run. Reversible *only* with the session key. Provable.
- Detection is **deterministic-first**: rules/checksums for structured PII (SSN,
  MRN, account #, dates), then Presidio's spaCy NER (inference is deterministic — no
  sampling) for names. Same document → same spans, every run.
- This is what makes the "zero leakage across 100 runs" benchmark *meaningful*
  rather than a dice roll. An LLM in the security path makes that test unfalsifiable.
- LLMs, if present at all, are a **recall backstop inside the trust boundary** (ADR-1
  prod), never the boundary itself.

🎯 **Scoping assumption this buys:** deterministic detection requires a **text layer** —
readable/extractable PDF/DOCX/TXT, *not* scanned images. OCR reintroduces probability
and belongs behind the boundary as a separate probabilistic pre-stage. We assume
extractable text and document scanned-image OCR as a known gap. That is a defensible
engineering boundary, not a dodge — and stating it is itself the senior signal.

⚠️ Honest distinction: *deterministic* ≠ *complete*. Determinism buys provability and
auditability (the same doc always yields the same safe output). Detection *recall* is a
separate axis, handled by defense-in-depth + confidence-gated redaction — never
conflate "reproducible" with "catches everything."

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

## ADR-2b: Token format — per-document keyed, human-readable, leak-tracing

The token is not just an opaque placeholder — designed well it does three jobs at once.
Derivation unifies session isolation, per-document non-linkability, and forensic tracing
under one primitive:

```
k_doc  = HKDF(K_s, doc_id)                       # per-document subkey from session key
slice  = HMAC(k_doc, normalize(entity_value))    # one-way, deterministic-in-doc
token  = f"patient_{slice[:6]}"                   # human-readable presentation
```

Properties, all from one construction:
- **Session isolation** (PRD requirement): `K_s` scopes everything to a session; Session
  B can't derive Session A's tokens.
- **Per-document namespace / no cross-document correlation:** `doc_id` in the derivation
  means the *same real patient* gets a *different* slice in a different document. A
  clinician never sees one patient's snippet repeated across their whole caseload —
  they see it once, in-document. Defeats cross-document linkage. 🔒
- **Deterministic within a document:** same value → same slice on every mention (natural
  coreference collapse *within the doc*, where it actually matters — ADR/earlier note
  that cross-doc identity resolution is explicitly NOT our job).
- **Irreversibility:** HMAC one-way; slice reveals nothing about the value.
- **Leak-tracing canary — with NO PHI persisted.** Durable storage holds only
  `token ↔ doc_id` — a *reference* to a document, never its contents. A token found in
  the wild traces back to its source document via that reference table; the token
  doubles as a **watermark**. There is no standing re-identification liability, because
  the value's only durable home is the encrypted document store (under the user key).
  The token→value reversal map is **session-ephemeral**, destroyed on logout.

**Correcting an earlier over-engineering:** irreversibility is an *external* property.
YourAI already holds the PHI locally; the guarantee is that the *provider* can't
reconstruct it. So we do NOT need `k_doc` to remain re-derivable after logout, and we
never persist key material next to references. Local access to PHI is by design and by
the user's key — not a leak.

### Two real constraints this creates

1. **Human-readable prefix vs de-obf reliability.** `patient_02938d` reads more naturally
   to the LLM than `[PHI_NAME_02938d]` → better fluency and coreference. But `patient` is
   a common English word, and de-obfuscation must detect tokens **unambiguously** (a
   false match restores a wrong value → 🟠). Resolution options: a reserved sentinel
   character in the slice delimiter, a per-type readable prefix drawn from a reserved
   namespace, or a strict `prefix_[0-9a-f]{6}` grammar the model is instructed to
   preserve verbatim. **We buy fluency only if we can keep the grammar unambiguous.**
2. **Slice length vs collision** (register A4): 6 hex = 24 bits is ample *within a
   document* (few dozen entities); collision is checked against the per-doc vault and the
   slice extended on the rare clash. Cross-document collisions are expected and harmless
   (different namespaces).

---

## ADR-3: Obfuscation strategies — tokenization vs pseudonymization

Shared `ObfuscationStrategy` ABC; both selectable by config; zero harness changes to
add a third. The README needs a per-entity-type comparison — here's the reasoning:

We implement **both** strategies behind a shared `ObfuscationStrategy` ABC (PRD
requirement + the swappability NFR), and both are graded — so both must be good. But for
**this domain** we take an opinionated default, and the reason is deeper than utility.

### 🔒 The real rationale for tokenization: bias neutralization, not just privacy

A realistic pseudonym hides the *identity* but preserves the *bias vectors*. A name —
even a fake one — still moves clinical decisions:
- name → inferred **ethnicity** → medication bias (the pharmacogenomic case, ADR-3-clinical),
- name → inferred **gender / class** → unconscious treatment disparity,
- name → raw **personal animus** ("this clinician reacts badly to the name Bradley").

`patient_b4mai` carries **none** of that — it is affect-neutral by construction.
**Tokenization is the only bias-neutral strategy; pseudonymization reintroduces exactly
the signal we want gone.** So the obfuscation layer is also a **de-biasing layer** — a
differentiated framing: this isn't only privacy, it's *removing identity-driven bias
from the reasoning context.*

This sharpens the ADR-3 rule into a clean split:
> **Identity carries bias signal we eliminate; clinical attributes carry decision signal
> we preserve.** Strip identity to a neutral token AND surface the clinical facts
> **explicitly and separately.** The model sees `patient_b4mai, Han Chinese descent,
> age 62, male` — full decision signal, zero identity bias.

**Mechanism ("advanced find-and-replace"):** choose the canonical token once per entity,
iterate it across every variant mention in the document (overscrub similar forms), and
**prime the LLM** so the token *is* the patient's identity for the session — the model is
pre-tokened into using `patient_*****` as required, so the token effectively becomes the
entity's name.

### Per-entity strategy table (updated)

| Entity type | Default | Why |
|---|---|---|
| **NAME** | **Tokenize** | Bias neutralization (above) + de-obf is catchable (fails loud); pseudonym reintroduces bias *and* fails silent |
| **SSN / MRN / account #** | **Tokenize** | Opaque, zero re-identification surface; a fake ID can collide with a real one |
| **DIAGNOSIS / condition** | Tokenize (default) | Model can't safely reason on a *fake* diagnosis; preserve real clinical facts as explicit signal instead |
| **DATE (all individual dates)** | **Generalize to year** (drop month+day) | Exact Safe Harbor; year is truthful → no reversal needed. See date rules below |
| **Clinical signal** (age<90, sex, ethnicity) | **Preserve** (not obfuscated) | Decision-relevant, Safe-Harbor-permitted (regulatory-entity-rules.md) |

Pseudonymization is still implemented and correct for **restore-tolerant / fluency /
synthetic-test-data** contexts — we just don't default to it clinically, and we say why.

### Date handling — generalization to year (the simple, compliant answer)

**All individual-related dates (DOB, admission, discharge, death, …) → drop month + day,
keep year.** This is exactly HIPAA Safe Harbor #3, applied uniformly. It introduces a
**third obfuscation primitive: generalization** (reduce precision), alongside
tokenization and pseudonymization.

- **One-way and vault-free by design.** The kept year is *truthful*, so there is **nothing
  to reverse** — the LLM sees `1973`, references `1973`, the user sees `1973` (and holds
  the full record anyway). Date de-obfuscation disappears as a problem. (Interface note:
  not every `ObfuscationStrategy` needs a reverse mapping — generalization/suppression
  populate no vault entry.)
- **Age ≥ 90 → "90+".** The one case where even the year is generalized — Safe Harbor's
  own exception, because extreme age is itself a re-identifier.
- **Known gap — pediatric under-5:** dates *preserved* (dosing can be day-precise; the
  clinical need and the identifier are the same field). Known PHI leak → **route to
  human.** This is exactly why fail-closed-to-human exists as a real path.
- 🔴 **Detection recall still matters:** a missed date leaks the full real date. But it's
  now a *plain* leak, not a correlation tell (no per-doc randomization to be inconsistent
  with). Dates get label-driven + format-regex detection; misses fail closed.
- **Accepted limitation:** within-year intervals ("length of stay", "3 days before") are
  lost — inherent to Safe Harbor, consistent with "months and days matter less." Not a
  novel gap; the regulation's own trade.

🔒 Q3 (adversary has obfuscated doc + response): with **tokenization** the adversary sees
affect-neutral tokens — no name, no bias vector, no realistic values to anchor on. They
can still infer *structure* (one patient, one physician, a diagnosis) and attempt
re-identification via **quasi-identifier combination** (rare diagnosis + age + preserved
ethnicity + ZIP-3). This is *k-anonymity territory* — obfuscation reduces but doesn't
eliminate inference risk. The preserved clinical quasi-identifiers are the residual
surface, and that's a **conscious, documented** safety-vs-linkage tradeoff (ADR-3), not
an oversight. Saying that out loud is the 10%.

### 🔒 The insight most candidates miss: anonymization is not attribute-neutral

**Some attributes are pure identifiers. Others carry clinical signal that must survive
de-identification — or the model reasons on wrong data and you get liability without
knowing it.**

- **Pure identifiers** — name, SSN, MRN, address, email. Removing them costs *zero*
  clinical utility. Scrub aggressively (tokenize).
- **Clinically-load-bearing quasi-identifiers** — ethnicity/race, age, sex, weight,
  pregnancy status. These are re-identification risk *and* medical signal. Blanket
  anonymization here is actively **dangerous**, because pharmacogenomics is real:
  - HLA-B\*15:02 screening before carbamazepine in patients of Han Chinese / SE-Asian
    descent (Stevens-Johnson risk);
  - warfarin dose sensitivity, G6PD-deficiency prevalence, codeine ultra-rapid-
    metabolizer frequency — all vary by ancestry.
  Strip or falsify ethnicity and the model may recommend a drug that is less effective
  or unsafe for that patient. **Silent harm.**

**Why pseudonymizing a name across ethnic groups is *worse* than tokenizing it:** it
doesn't merely lose signal, it **injects false signal.** A model infers ancestry from
"Michael Torres" and reasons on a wrong pharmacogenomic prior. Corollary design rule:

> **Never encode clinical signal in an identifier you are about to scramble.**
> Separate *identity* from *clinical attributes*. Tokenize the identifier; preserve the
> load-bearing attribute as an explicit, **de-linked structured fact** ("patient is of
> Han Chinese descent") — not smuggled through a name.

**The tension, named honestly (this is the liability point):** preserving ethnicity
improves clinical safety but *raises* re-identification risk — it's a quasi-identifier
for k-anonymity. There is no free lunch. The answer is that this must be an **explicit,
auditable policy decision**, surfaced as config — never a silent default buried in a
generic NER library. The liability lives in the *unexamined* choice.

- Design surface: `preserve_clinical_signal: [ETHNICITY, AGE_BAND, SEX, WEIGHT]` — a
  visible, reviewable knob. Default preserves-with-documentation; a compliance officer
  can tighten it. The point is the decision is *made on purpose and logged*, not made
  by accident by whatever the anonymizer happened to catch.

🎯 This reframes the whole task from "scrub PII" to "**maximize privacy subject to
preserving decision-relevant signal**" — and it's the clearest evidence that a human,
not an LLM, designed this system.

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

## ADR-7: Runtime & language — deliverable vs. production

**Deliverable = Python 3.10+.** The PRD *hard-requires* it (asyncio, pytest,
pytest-asyncio, Presidio). Deviating risks auto-rejection for a non-differentiating
reason. We ship Python. Not a debate.

**But the prod language is a legitimate senior discussion, and it exposes a flaw in the
PRD's own framing** — worth surfacing in the README/live review:

> "asyncio required throughout" conflates I/O-bound and CPU-bound work. Async helps the
> **LLM call, storage, and vault I/O** (genuinely I/O-bound — asyncio is perfect). It does
> **nothing** for the **structural parse**, which is **CPU-bound** string/span scanning.
> Under the GIL, that runs on **one core**. To hit "obfuscate a 2,000-word doc < 2s" —
> and especially the 50-page brief — Python must push parsing into **multiprocessing or a
> native extension**; asyncio alone won't do it. Naming this is the senior signal.

Prod candidates for the parsing hot path:

| | For | Against (this workload) |
|---|---|---|
| **C#** *(lead prod pick)* | No GIL → parsing **parallelizes across cores** natively; `Span<T>`/`Memory<T>` give **zero-copy, cache-friendly** string scanning (directly kills the cache-miss problem); **OpenXML SDK** parses DOCX structure *exactly* (our best parsing tier); enterprise-grade crypto (`System.Security.Cryptography`), first-class async. Fast end, robust, deep libraries. | Heavier runtime than Go; not as raw-fast as Rust |
| **Rust** | Fastest, memory-safe, ideal for the span-scanning loop | Slower dev velocity; PDF/DOCX ecosystem thinner; smaller enterprise talent pool |
| **Go** | Light, great concurrency (goroutines) | "Library-free" — weak PDF/DOCX ecosystem; less expressive typing for a rich parser |
| **Python** *(the deliverable)* | Fastest to build; best PII/NER ecosystem (Presidio/spaCy); mandated | GIL blocks CPU-bound parsing; boxed objects are **cache-hostile** in tight scan loops |

**Position:** ship the deliverable in Python; in the README argue **C# for prod** — the
`Span<T>` cache story + OpenXML exact-DOCX + no-GIL parallel parse make it the best fit
for a document-parsing PHI pipeline, with Rust as the max-throughput alternative. That
"I know the mandated stack and I know why I'd swap it, specifically" is exactly the
not-outsourcing-my-brain signal.

⚠️ Pragmatic Python mitigation in the deliverable: keep the parse hot path in tight,
allocation-light functions; hit the 2,000-word benchmark directly; treat the 50-page
brief as the *scale* discussion (per-page multiprocessing) rather than a live benchmark.

---

## Safety posture (threat-model line)

**Automation is augmentation, not elimination.** In healthcare the dial is set to
*as-safe-as-possible*: every ambiguous boundary overcuts, unparseable structure is
rejected to human review, and low-confidence detection redacts. The system removes toil
and scales review; it does not remove the human backstop. Fail closed, always.

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

## Settled decisions

1. **Ship a custom recognizer in the demo** (e.g. synthetic MRN format) — proves the
   "new entity = config only" extensibility path end-to-end, not just in prose.
2. **Per-entity strategy routing is in-scope for the demo** — a config map:
   identifiers → tokenize, DOB → keyed date-shift, clinical quasi-identifiers →
   preserve-per-policy. This *is* the ADR-3 insight made executable; it's the visible
   differentiator, not a nice-to-have.
3. **Vault store: app-layer AES-256-GCM in the demo, SQLCipher/Postgres+KMS in prod.**
   App-layer is the better *teaching* choice — a reviewer watches encryption applied
   correctly rather than trusting the DB to do it invisibly. README states the prod swap.

## Standing design rules (carry into every module)

- **Determinism in the security path.** No stochastic component decides what leaves the
  boundary. HMAC tokenization + rule-first detection. (THESIS)
- **Text-layer assumption.** Extractable text in; scanned-image OCR is a documented gap.
- **Never encode clinical signal in an identifier you scramble.** Identity and
  clinical attributes are separate concerns. (ADR-3)
- **Confidence-gated degradation is a security default:** below threshold → redact,
  never pass through.
- **Nothing original in logs or on the wire** — token IDs and ciphertext only; assert
  it with tests, don't assume it.
