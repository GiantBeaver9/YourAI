# De-obfuscation & Response Handling — the under-explored 20%

De-obfuscation is 20% of the grade and demands: restore **all** token references
including grammatically inflected forms, with **no tokens leaked**. It is *not* the
mirror image of obfuscation — the response is free-form model output we don't control,
which makes reversal a different (and strategy-dependent) problem.

---

## 1. The strategy asymmetry — the load-bearing insight

De-obfuscation recall differs *radically* by obfuscation strategy, and it inverts which
strategy is "safe."

### Tokenization reversal = a PARSING problem (safe, catchable)
- Tokens follow a **strict grammar** (`patient_[0-9a-f]{6}` / `[PHI_TYPE_hex]`).
- LLMs **copy opaque strings verbatim** — there is nothing to paraphrase in a
  meaningless code — so recall is inherently high.
- Reversal: regex the grammar → tolerate affixes (`'s`, punctuation, casing) → vault
  lookup → replace.
- **Failure fails LOUD:** an un-restored token still *looks like a token*, so the
  **leftover-token guard catches it** and we redact + audit + surface. No silent leak.

### Pseudonymization reversal = an ENTITY-RESOLUTION problem (fragile, silent)
- The obfuscated value is a **realistic name** ("Michael Torres") — **no grammar to
  anchor on.**
- The model emits partial / inflected / coreferent forms: "Mr. Torres", "Michael",
  "Torres", "he." To reverse, you must find *every* mention — which is exactly the
  coreference problem we deliberately scoped OUT on the input side, now back on output.
- 🟠 **Failure fails SILENT and UNCATCHABLE:** a missed pseudonym mention does **not**
  look like a leaked token — it looks like a real name — so the leftover-guard never
  fires. The un-restored fake ships to the user *as if it were genuine*. A wrong name on
  a clinical document is a real-world harm, and our safety net is blind to it.
- Extra failure: if the model independently invents a name matching a live pseudonym,
  naive reversal restores the *wrong* value.

> **Conclusion:** tokenization fails loud and catchable; pseudonymization fails silent
> and uncatchable. For a fail-closed, provable system, **tokenization is the default for
> anything that must round-trip.**

### The one safe pseudonymization: date-shift
Date-shifting reverses by **arithmetic** (apply the inverse per-record offset), not by
string search — no matching, no ambiguity, no coreference. It is the single
pseudonymization immune to the asymmetry. Keep it.

### Routing consequence (feeds ADR-3 config)
| Entity | Strategy | Why |
|---|---|---|
| Identifiers (name, SSN, MRN, acct) — **round-trip-critical** | **Tokenize** | Safe, catchable reversal |
| Dates / DOB | **Date-shift** | Arithmetic reversal, preserves intervals |
| Fluency-critical, restore-tolerant | Pseudonymize **+ response-side net** | Only where fluency > perfect restore |
| Clinical signal (age<90, sex, ethnicity) | **Preserve** | Not removed at all (ADR-3 / Safe Harbor) |

---

## 2. Response-side safety net (covers pseudonymization's blind spot)

Because pseudonyms evade the grammar-based guard, when pseudonymization is used at all
we add a **response-side pass** before returning to the user:
1. **Known-pseudonym search:** the session vault knows every fake string it emitted;
   search the response for each (+ inflections). Exact/near matches → restore.
2. **Response-side NER:** run detection on the *model's output* to catch (a) any
   pseudonym partial we missed and (b) any *new* PII-shaped content the model
   hallucinated (a made-up SSN). Anything detected that isn't a known token/pseudonym →
   redact, don't emit.
3. **Leftover-token guard (always on):** assert no token-grammar residue survives.
4. On any un-resolvable residue → fail closed (redact + audit), never pass through.

This is why identifiers are tokenized: it keeps the *cheap, catchable* path for the
data that must be perfect, and confines the *expensive, fuzzy* path to entities where a
miss is redacted rather than leaked.

---

## 3. Token-preservation is also a PROMPT-DESIGN problem

De-obf recall starts before the LLM call. The system prompt materially changes how
faithfully tokens come back:
- Instruct the model: tokens like `patient_02938d` are **opaque placeholders**; reuse
  them **verbatim**; never expand, translate, reformat, or invent them.
- Tell it the *roles* (patient_, provider_, contact_) so it can reason relationally
  without needing real values.
- Ask it to reference entities **by token** rather than by paraphrase — this is our only
  (partial) lever against the co-reference gap (Q2: "the patient's condition" has no
  token to restore). We reduce indirect references at the source; we can't eliminate them.

⚠️ Prompt-level mitigation is best-effort, not a guarantee. The guard + response-side
net are the guarantees; the prompt just raises baseline recall.

---

## 4. De-obfuscation edge cases (each → a test)
- Affixed/inflected tokens `[..]'s`, `([..])`, `[..].` → affix-tolerant grammar.
- Case/spacing mangling → conservative match (case-insensitive type+hex, known affixes);
  do NOT fuzzy-match arbitrary edits (wrong restore 🟠 > caught miss).
- Concatenated tokens → grammar requires delimiters; unmatched → guard.
- Vault miss (unknown/hallucinated token) → redact + audit, never guess.
- Co-reference / indirect reference → unsolvable by replacement; named limit (Q2).
- Pseudonym partial mention ("Torres") → response-side net (§2); residual documented.
- Model regurgitates a real value it inferred → response-side NER (§2).
- Cross-session token replayed → different key space → vault miss → guard.

---

## 5. Performance (PRD: 500-token response de-obf < 500ms)
- Tokenized path: single regex scan + O(1) in-memory vault lookups → trivially < 500ms.
- Response-side NER (pseudonymization only) is the cost; scope it to when pseudonyms
  were actually emitted, not every response.
