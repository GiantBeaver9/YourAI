# Secure Context Pipeline

PII/PHI obfuscation between a document store and external LLM providers. Raw PII, PHI,
and privileged content never leave our infrastructure in a form a provider can read or
reconstruct — while the obfuscated context keeps enough structure for the model to reason,
and the model's response is transparently restored before the user sees it.

**detect → obfuscate → call LLM → restore.**

### 🔗 Live service

- **Base URL:** https://merry-playfulness-production-d238.up.railway.app
- **Browser test console:** https://merry-playfulness-production-d238.up.railway.app/demo
- **Interactive API docs:** https://merry-playfulness-production-d238.up.railway.app/docs
- **Health:** https://merry-playfulness-production-d238.up.railway.app/health

Write endpoints require the `X-API-Key` header (see [API reference](#api-reference)).

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

No API key is required — the pipeline defaults to a deterministic `MockProvider`. To use a real
model, set a provider key: **`GEMINI_API_KEY`** (Google Gemini, the intended real provider) or
`ANTHROPIC_API_KEY`. The provider auto-selects by whichever key is present (Gemini preferred);
force one with `SCP_LLM_PROVIDER=gemini|anthropic|mock`.

### Run as an HTTP service

```bash
pip install -e ".[api]"
uvicorn secure_context_pipeline.api:app --host 0.0.0.0 --port 8000   # $PORT on a PaaS
# GET /health  ·  GET /  ·  POST /process  ·  POST /obfuscate  ·  /docs (OpenAPI)
```

`POST /process` runs the full round-trip on one request (its own ephemeral session, crypto-shred
on return) and returns the restored answer; `POST /obfuscate` returns only the outbound payload
for inspection. Set `SCP_API_KEY` to require `X-API-Key` on the write endpoints (they are open if
it's unset). The Dockerfile serves this on `$PORT` by default — see **Deploy** below.

### Deploy (Railway, GitHub-pull)

The repo ships a `Dockerfile` (serves the API on `$PORT`) and `railway.json` (build + healthcheck
on `/health`). In Railway: **New Project → Deploy from GitHub repo → this repo**, pick the branch
(`main`), and set variables — `SCP_MASTER_KEY` (64 hex), `SCP_API_KEY` (a secret), and optionally
`GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`). Railway injects `PORT`; do **not** set it yourself.
Generate a public domain under the service's **Settings → Networking**.

**End users:** see [`USAGE.md`](USAGE.md) for a plain-language guide. Once deployed, the browser
test console lives at **`/demo`**, interactive API docs at **`/docs`**, and a full test battery
runs via [`scripts/test_api.bat`](scripts/test_api.bat) (Windows) or
[`scripts/test_api.sh`](scripts/test_api.sh) (macOS/Linux).

---

## API reference

All endpoints. Write endpoints require the `X-API-Key` header when `SCP_API_KEY` is set
(otherwise they're open). JSON bodies are `{"text": "...", "task": "...", "doc_id": "optional"}`.

| Method | Path | Auth | What it does |
|---|---|---|---|
| `GET` | `/health` | — | Liveness probe (Railway healthcheck). Returns `{"status":"ok"}`. |
| `GET` | `/` | — | Service info: active detector, provider, `custom_rules_loaded`, endpoint list. |
| `GET` | `/demo` | — | Browser test console (obfuscate + round-trip, token highlighting). |
| `GET` | `/docs` | — | OpenAPI / Swagger UI. |
| `GET` | `/rules` | key | Lists **all** rules — custom rows in full + built-in Safe Harbor rules. |
| `POST` | `/obfuscate` | key | Returns only the **outbound payload** (obfuscated, no PII) + entity-type counts. |
| `POST` | `/process` | key | Full round-trip **detect → obfuscate → LLM → restore**; returns restored text + meta. |
| `POST` | `/process-pdf` | key | **PDF upload → extract text → same pipeline.** Scanned/image PDFs are quarantined. |

### How each behaves

- **`/obfuscate`** and **`/process`** take the same JSON body. `/obfuscate` stops at the wire
  (great for proving zero PII leaves); `/process` runs the whole thing and restores tokens in
  the reply. `/process` meta includes `detector`, `provider`, `entities_detected`,
  `tokens_restored`, `deobfuscation_clean`.
- **`/process-pdf`** is a `multipart/form-data` upload (`file=@doc.pdf`, optional `task` field).
  Flow: **PDF bytes → text extraction (pypdf) → the normal `/process` pipeline.** A PDF with no
  extractable text (scanned/image) returns `routed_to_human: true` with a reason instead of
  being mis-processed — the image-quarantine policy.
- **`/rules`** returns `{custom_rules, standard_rules, …counts}` so you can see exactly what's
  active. Add custom rules via the file path above (there is no runtime write endpoint yet).

### How to test (Windows CMD)

```cmd
set BASE=https://merry-playfulness-production-d238.up.railway.app
set KEY=scp_yourkey

curl %BASE%/health
curl -H "X-API-Key: %KEY%" %BASE%/rules
curl -s -X POST %BASE%/obfuscate -H "Content-Type: application/json" -H "X-API-Key: %KEY%" -d "{\"text\":\"SSN: 482-19-7734, MRN 4457812\"}"
curl -s -X POST %BASE%/process   -H "Content-Type: application/json" -H "X-API-Key: %KEY%" -d "{\"text\":\"Patient: Jonathan Reyes, SSN 482-19-7734\",\"task\":\"Summarize.\"}"
curl -s -X POST %BASE%/process-pdf -H "X-API-Key: %KEY%" -F "file=@note.pdf" -F "task=Summarize."
```

Or run the whole battery: `scripts\test_api.bat` (Windows) / `scripts/test_api.sh` (macOS/Linux).
The browser console at `/demo` needs no shell quoting.

---

## Design thesis: determinism is the security control

The core requirement — *"contractual guarantees are not sufficient; we need a
technical guarantee"* — extends one step: **a probabilistic detector is not a sufficient
technical guarantee either.** Making an LLM's recall your security boundary means you can't
*prove* anything, can't reproduce a leak to fix it, and — if the model is remote — you've
leaked raw PII to detect the leak.

So the security-critical path is **deterministic and rule-anchored**: the load-bearing
transform is a keyed HMAC (same input + same session key → same token, every run), and
detection is rules-first. This is what makes "zero leakage across 100 runs" a *meaningful*
test instead of a dice roll. LLMs, where present, are a recall backstop *inside* the trust
boundary — never the boundary itself. (Scoping assumption: extractable-text documents — see
Known gaps.)

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

### Updating detection rules (no code change)

Because detection is rules-as-data, a deployment can add or adjust rules without touching code.
Author them in a JSON file and point `SCP_CUSTOM_RULES_PATH` at it, then redeploy:

```json
{ "rules": [
  { "id": "hospital_mrn", "entity_type": "MRN", "regex": "AH-\\d{6}", "confidence": 0.95 },
  { "id": "badge", "entity_type": "GENERIC_ID",
    "preceding": "(?:Badge|Employee\\s*ID)\\s*[:#]\\s*", "regex": "[A-Z]{2}\\d{4,8}" }
] }
```

Each row is a `Rule` (anchor `preceding` · value `regex` · boundary `succeeding` · `entity_type`
· `action`), the same shape the built-in Safe Harbor rules use — so "a new identifier is a row,
not a deploy of new code." Rules are **additive** (they can catch *more*, never suppress a
standard rule); every pattern is compiled-validated at load, and a bad one is skipped-and-logged
rather than crashing the service. See [`custom_rules.example.json`](custom_rules.example.json)
and `GET /` reports how many custom rules loaded.

The fully self-service, *runtime* authoring surface — RE2 intake validation that provably
rejects catastrophic-backtracking regexes, plus a live example/preview panel — is the documented
next step (`docs/detection-rules-engine.md`); this operator-file path is its trusted-input
subset, safe to ship today.

---

## Obfuscation — three primitives, routed per entity

| Primitive | Reversible? | Vault entry? | Used for |
|---|---|---|---|
| **Tokenize** `[TYPE_hex]` | yes — parse + lookup, **fails loud** | yes | identifiers, names |
| **Pseudonymize** (realistic fake) | fuzzy — entity-resolution, **fails silent** | yes | restore-tolerant / fluency |
| **Generalize** (date → year) | no — truthful, one-way | **no** | dates |

### Tokenization vs. pseudonymization — worked, per entity type

Both reversible strategies are implemented behind a shared ABC and are config-swappable. Here
both NAME and SSN land on **tokenize** — for *different* reasons — which is exactly why the
choice is per-entity, not global:

**NAME → tokenize, not pseudonymize.** (1) *Bias:* a realistic fake name still carries bias
vectors — inferred ethnicity → a real pharmacogenomic prior, inferred gender/class →
treatment disparity. A token carries none; tokenization is a de-biasing layer, not just
privacy. (2) *De-obf asymmetry:* tokens fail **loud** (an un-restored token still looks like a
token → the leftover-guard catches it); a missed pseudonym looks like a real name and ships
**silently**. For a fail-closed system that asymmetry disqualifies pseudonyms for names.

**SSN → tokenize, not pseudonymize.** A *pseudonymized* SSN still looks like a valid SSN and
can **collide with a real person's number** — inventing valid-looking identifiers is a
liability. An opaque token can't be mistaken for a real one.

**DATE / DOB → neither: generalize** to year (Safe Harbor #3). Truthful, one-way, no vault
entry, nothing to restore — strictly better than a fake specific date, which would inject
false precision the model could reason on.

**Where pseudonymization earns its keep:** a **de-identified export mode** — a realistic-but-
fake shareable copy of a document (demos, synthetic test datasets, training corpora) where
realistic values are the *point* and no reversal is needed. Selectable per entity via
`ObfuscationPolicy`; the fail-silent de-obf risk is moot because these outputs are never
restored.

---

## The vault

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
- **Vault compromise** → holds ciphertext + one-way tokens; without the session key, no PII.
  *(Demo caveat: `K_s` and the vault live in one process, so a live-process compromise takes
  both; split custody — vault store separate from a KMS-held key — is the prod hardening.)*
- **Cross-session & cross-document correlation** → a new session mints a fresh random `K_s`,
  and each document derives its own `k_doc = HKDF(K_s, doc_id)`, so the same patient
  tokenizes differently across sessions *and* across documents — no persistent pseudonym to
  link on.

Residual, and named: **quasi-identifier combination** (Sweeney: ZIP + DOB + sex uniquely
identifies the vast majority of the US population) survives
because the model must reason per-patient — governed by an explicit, logged `ObfuscationPolicy`
knob, not hidden.

---

## What makes this more than a scrubber

PHI is not a checklist of scary words — it's a **re-identification risk judgment** (health
context ∩ identifiability). Every decision above follows from that one framing rather than
from a field blocklist — which is why we preserve clinically-load-bearing quasi-identifiers
(ethnicity/age/sex) under a logged policy instead of blindly stripping them.

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
