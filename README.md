# Secure Context Pipeline

PII/PHI obfuscation between a document store and external LLM providers. Raw PII, PHI,
and privileged content never leave our infrastructure in a form a provider can read or
reconstruct — while the obfuscated context keeps enough structure for the model to reason,
and the model's response is transparently restored before the user sees it.

**detect → obfuscate → call LLM → restore.**

---

## Quick start

```bash
# option A — docker
docker-compose up          # runs the end-to-end demo on the bundled fixture

# option B — local
python -m venv .venv && . .venv/bin/activate
pip install -e .
python demo.py             # end-to-end on the fixture (no API key needed — mock LLM default)
pytest                     # full suite incl. the zero-leakage property test
```

No API key is required — the pipeline defaults to a deterministic `MockProvider`. Set
`ANTHROPIC_API_KEY` to route the LLM leg to a real provider.

---

## Design thesis: determinism is the security control

The challenge's own framing — *"contractual guarantees are not sufficient; we need a
technical guarantee"* — extends one step: **a probabilistic detector is not a sufficient
technical guarantee either.** Making an LLM's recall your security boundary means you can't
*prove* anything, can't reproduce a leak to fix it, and — if the model is remote — you've
leaked raw PII to detect the leak.

So the security-critical path is **deterministic and rule-anchored**: the load-bearing
transform is a keyed HMAC (same input + same session key → same token, every run), and
detection is rules-first. This is what makes "zero leakage across 100 runs" a *meaningful*
test instead of a dice roll. LLMs, where present, are a recall backstop *inside* the trust
boundary — never the boundary itself. (Scoping assumption: extractable-text documents;
scanned-image pages are quarantined, not OCR-and-hoped.)

---

## Architecture

```
[upload] → [encrypted store]  AES-256-GCM, per-user envelope keys
              → [detection]   rules-as-data (+ Presidio), Safe Harbor + magnitude, coreference
              → [obfuscation] tokenize | pseudonymize | generalize, routed per entity + policy
                   → [session vault]  encrypted token↔original, crypto-shredded on logout
              → [injector]    inline, token-preserving prompt, VERIFY-BEFORE-SEND ── external LLM
              → [de-obf]      token grammar + affix + leftover-guard ←────────────── LLM response
              → [restored response to user]
[audit] token-only, every event, with policy_version
```

Async at the true I/O seams (LLM, store); the parse/obfuscate core is synchronous and honest.

---

## Entity detection — approach and why

**Presidio is the execution substrate; our front end (rules, recognizers, domain logic)
configures it.** We build *around* the defined tool rather than reinventing NER or leaning on
a remote LLM (which would reintroduce the leak). Detection is **rules-as-data** — each rule
(anchor, pattern, boundary, action) compiles to a Presidio recognizer, so a new entity type
is a config row, not a code change (the extensibility NFR).

- **Safe Harbor as the spec** — the detector implements HIPAA's 18 identifiers, with the
  real rules: dates → year, age ≥ 90 → "90+", ZIP → 3 digits (restricted-prefix table).
- **Magnitude rule for bare numbers** — physiology bounds clinical numbers (nobody doses
  15000mg), so 5+ consecutive digits or leading zeros → scrub; short, separator-free numbers
  are clinical values → keep. Carve-out: a 5+ run beside a clinical unit (`copies/mL`) is a
  lab value, kept. This avoids the real harm of deleting a dose or a viral load.
- **Coreference** — string-level clustering before tokenization, so "John Smith" / "John" /
  "Mr. Smith" collapse to one token (we hash the cluster canonical, not the surface string).
- **Graceful degradation** — below the confidence threshold, **redact** rather than pass
  through. Under-detection is the only true leak; the whole system biases to over-cut.

Why not LLM-based detection? A remote LLM detector ships raw PII to find PII — self-defeating
for this exact threat model. A *local* model is a legitimate prod recall-backstop; it's noted
as designed-not-built here.

---

## Obfuscation — three primitives, routed per entity

| Primitive | Reversible? | Vault entry? | Used for |
|---|---|---|---|
| **Tokenize** `[TYPE_hex]` | yes — parse + lookup, **fails loud** | yes | identifiers, names |
| **Pseudonymize** (realistic fake) | fuzzy — entity-resolution, **fails silent** | yes | restore-tolerant / fluency |
| **Generalize** (date → year) | no — truthful, one-way | **no** | dates |

### Tokenization vs. pseudonymization — worked, for two entity types

**NAME.** We **tokenize** (`[NAME_a3f2b1c9d0e1]`), not pseudonymize, for two reasons the
usual "pseudonyms read better" analysis misses:
1. **Bias.** A realistic fake name still carries bias vectors — inferred ethnicity → a real
   pharmacogenomic prior, inferred gender/class → treatment disparity. A token carries none.
   Tokenization is the only *bias-neutral* strategy; it's a de-biasing layer, not just privacy.
2. **De-obfuscation.** Tokens fail **loud** — an un-restored token still looks like a token,
   so the leftover-guard catches it. A missed pseudonym looks like a real name and ships
   silently. For a fail-closed system, that asymmetry is close to disqualifying for names.

**DATE / DOB.** We **generalize** to year (drop month/day) — Safe Harbor #3 verbatim. The
kept year is *truthful*, so there is nothing to reverse and no vault entry; date de-obf
disappears. Pseudonymizing dates (a fake specific date) would inject false precision the model
could reason on. Generalization is strictly better here.

Both strategies are implemented behind a shared ABC and are config-swappable; the routing is
per-entity because the right answer *is* per-entity.

---

## The vault (30% of the security story)

A session owns one **random, ephemeral root key `K_s`**. From it we HKDF two subkeys:
`k_token` (HMAC → deterministic one-way tokens) and `k_enc` (AES-256-GCM → the encrypted
token↔original map). Two properties fall out of one construction:

- **Reversible within a session** — vault lookup restores originals.
- **Irreversible across sessions** — on logout `K_s` is zeroized and the AES-GCM map is
  **crypto-shredded**: the ciphertext is unrecoverable without the key, which no longer
  exists. "Irreversible across sessions" is a *cryptographic property*, not a policy promise.

`K_s` is random (not derived from any durable key), which is what makes the guarantee real —
the durable per-user store key and the ephemeral `K_s` are deliberately independent hierarchies.

---

## Threat model — what this resists

- **Provider / network inspection** → sees only tokens + preserved (non-identifier)
  quasi-identifiers; verify-before-send asserts zero direct-identifier PII at the wire.
- **Prompt injection** → the model never receives the PII, so there is **nothing to
  exfiltrate**; injection can degrade the task, never leak PHI.
- **Stolen store / DB dump** → AES-256-GCM ciphertext, per-user keys, AAD-bound; cross-user
  and blob-swap attacks fail cryptographically.
- **Vault compromise** → holds ciphertext + one-way tokens; without the (KMS-held, in prod)
  session key, no PII. Token-map custody is split from key custody.
- **Cross-session correlation** → per-document keying means the same patient tokenizes
  differently across documents; no persistent pseudonym to link on.

Residual, and named: **quasi-identifier combination** (Sweeney: ZIP+DOB+sex ≈ 87%) survives
because the model must reason per-patient — governed by an explicit, logged `ObfuscationPolicy`
knob, not hidden.

---

## What makes this more than a scrubber

PHI is not a checklist of scary words — it's a **re-identification risk judgment** (health
context ∩ identifiability). That framing drives every decision: generalize dates (kill
identifier precision, keep clinical year), tokenize identity (bias-neutral), preserve
clinically-load-bearing quasi-identifiers (ethnicity/age/sex — Safe Harbor permits them and
pharmacogenomics needs them) under a logged policy, and fail closed on anything unreadable.

---

## Known gaps (honest)

- **Free-text coreference** is string-level only; pronominal ("the patient") is not restored
  by token replacement — named as unsolvable-by-replacement, not faked.
- **Quasi-identifier combination** re-identification (above) — reduced, not eliminated; needs
  k-anonymity generalization to fully close.
- **Scanned/image pages** — quarantined (never OCR-and-hoped); a per-tenant threshold routes
  the document to human review.
- **Pediatric (age ≤ 5)** — dates preserved (day-precise dosing) and routed to human; a known,
  accepted PHI-handling exception.
- **Multi-column / complex PDF layout** — detected and rejected to human, not mis-parsed.
- **Audit proves obfuscation *ran*, not detection *recall*** — a missed entity is invisible to
  the log; closed in prod by egress DLP + detection metrics.

## With another day

Local-LLM recall backstop (inside the boundary); k-anonymity generalization for multi-patient
tables; the runtime rule-authoring surface (RE2-validated custom regex + example/preview
panel); PDF coordinate-aware structural parsing; KMS-backed key custody; map-reduce chunking
for global reasoning over over-context documents.

---

## Repo layout & design docs

`secure_context_pipeline/` — `store · detection · obfuscation/{strategies,engine} · vault ·
pipeline · deobfuscation · audit`. `tests/`, `demo.py`, `docker-compose.yml`.

Full design record in `docs/` — architecture decisions, threat model, the failure-mode
register, the session model, the PHI domain brief, and the build spec.
