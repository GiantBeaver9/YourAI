# Test & Fixture Harness — proving the guarantee

The headline requirement: **"zero PII leakage across 100 automated test runs on varied
fixture documents."** That is a *property* test, not 100 hand-written files. This draws
the harness that makes the zero-leakage claim mechanically true, plus the required
scenario tests.

---

## 1. The zero-leakage property test (the headline)

**Property:** *for any document, the outbound LLM payload contains none of that document's
original PII values.*

The enabler is **synthetic fixtures with ground truth**: because we *generate* the PII, we
know exactly which strings must never appear downstream.

```
@given(doc=clinical_documents())          # Hypothesis strategy → varied docs
def test_no_pii_in_outbound_payload(doc):
    known_pii = doc.injected_pii           # ground-truth set, by construction
    payload = pipeline.build_llm_payload(doc.text, session)   # intercept at injector boundary
    for value in known_pii:
        assert value not in normalize(payload)      # exact + normalized
        assert not partial_leak(value, payload)     # last-name / digit-run fragments
```

- `clinical_documents()` composes templates (form fields, tables, signature blocks, prose)
  with Faker-generated PII under a **known seed** → the ground truth is free. Hypothesis
  makes "100 runs on varied documents" a *generator*, not a folder of files — **less code,
  stronger coverage.**
- Intercept at the **injector boundary** — assert on the exact bytes that would leave our
  infra, which is what "verifiable by inspection" means.

---

## 2. The leakage oracle (the subtle part)

Asserting "no PII in payload" is trickier than `in`:
- **False positive (coincidence):** a fake first name "John" is also a common word →
  spurious "leak." Fix: generate **sufficiently unique** synthetic PII (rare Faker values /
  tagged tokens) so a match means a real leak, not a coincidence.
- **False negative (partial leak):** the detector masked "John Smith" but a boundary bug
  left "Smith." The oracle checks **fragments** (surname, digit substrings), not just the
  whole value.
- **Normalization parity:** the oracle normalizes the payload the same way detection does
  (casefold, whitespace, punctuation, common ID formattings) so a reformatted leak still
  trips it.

This oracle is itself worth a couple of unit tests (a doc with a *deliberately* planted
leak must **fail** the oracle — proving the oracle can see leaks).

---

## 3. Required scenario tests (example-based, pytest-asyncio)

| Scenario | Assert |
|---|---|
| **Happy path** | doc → obfuscate → mock LLM echoes tokens → restore → originals present, **zero tokens** left |
| **Entity not found** | PII-free doc → unchanged, no vault entries, no crash |
| **Vault miss** | response with unknown token → leftover-guard fires, no crash, no raw token to user |
| **Expired session** | expire → de-obfuscate → `SessionClosed`/miss, **no leak, no raw output** |
| **Concurrent isolation** | two sessions, same value → **different tokens**; B.resolve(A_token) → miss (run truly concurrently) |

---

## 4. Property tests for the core guarantees (25% + 20%)

- **Deterministic in session:** same doc + same session → identical tokens.
- **Non-deterministic across sessions:** different `K_s` → different tokens for the same value.
- **Round-trip identity:** tokenized entity obfuscate→de-obfuscate == original.
- **Inflection tolerance:** `token's`, `(token)`, `token.` all restore.
- **Leftover-guard soundness:** any un-restored token-grammar residue → caught, never shipped.
- **Audit is PHI-free:** grep the audit output for every ground-truth value → **zero hits**.

---

## 5. Performance benchmarks (PRD numbers, mock LLM so they're deterministic)

| Target | Method |
|---|---|
| Obfuscate 2,000 words < 2s | timed assert on the generative corpus |
| Vault lookup < 5ms/token | in-memory dict → micro-timing |
| De-obf 500-token response < 500ms | timed assert |
| Full pipeline < 15s | **mock LLM** (real-LLM latency reported separately, not gated in CI) |

Real provider call (Gemini / Anthropic / any `LLMProvider`) → a single integration test behind
an env-var marker; never in the default suite (flaky, keyed).

---

## 6. Two metrics, not one pass/fail

Because we bias to **overcut**, report both:
- **Leakage rate** — must be **0** (the hard guarantee).
- **Over-redaction rate** — how much *non*-PII got masked (the utility cost of overcut).

Reporting both makes the safety/utility tradeoff *quantified*, not asserted — and it's the
honest evidence for the live-review "where does it over-cut?" question.

---

## 7. The bundled fixture (the PRD's "provided test document")
One realistic synthetic doc exercising every parsing pattern — labeled inline fields, a
multi-field line, a stacked-label block, a table, a signature block (value-above-label),
running headers, and a prose paragraph with embedded PII + protected clinical numbers.
`demo.py` runs the full pipeline on it end-to-end. It doubles as the human-readable proof
and the parsing regression fixture.
