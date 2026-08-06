# Structured-ID Detection — SSN / phone / account / MRN and the number problem

We went deep on names and dates. Structured IDs are the **strongest case for deterministic
detection** (formats, checksums) — but they hide the single sharpest tension in the whole
detector: **telling an identifier-number from a clinical-number.** Over-redact and you
delete `BP 120/80` or `2 tablets` (clinical harm); under-redact and you leak an SSN.

---

## 1. Two paths (same structure as everything else)

**a. Structural (label-driven) — deterministic, primary.** `SSN:`, `MRN:`, `Account #:`
→ the label *types* the value, so anything in the value slot is that type **even if
malformed** (`123-4-56789` after `SSN:` is a leaked SSN, and we catch it). Boundary rules
from the parsing doc. This is where structured IDs are safest.

**b. Free-text (format regex + validation) — for unlabeled prose.** No label → match by
format, then validate. Weaker; this is where the number problem lives.

---

## 2. Per-type rules

| Type | Format | Validator (confidence, not gate) | Notes |
|---|---|---|---|
| **SSN** | `\d{3}-\d{2}-\d{4}` / `\d{9}` | area≠000/666/900-999, group≠00, serial≠0000; known test SSNs | dashes make it format-specific → safe in free text |
| **Phone** | NANP grouped, `+CC` | length / grouping | ⚠️ the classic trap — see §3 |
| **Email** | `\S+@\S+\.\S+` | domain sanity | low false-positive, easy |
| **Credit card** | `\d{13,19}` | **Luhn** | Luhn-fail near a "card" label → still redact (A5) |
| **MRN** | *no universal format* | per-institution config | **label-driven only** — regexing arbitrary numerics in prose = false-positive storm |
| **Account / member / policy #** | varies | per-type config | same as MRN — label-driven |
| **ZIP** | `\d{5}(-\d{4})?` | ZIP-3 rule | regulatory-entity-rules.md |
| **IP / URL / VIN / device** | standard regex | format | low ambiguity |

**Validation is confidence, never a redaction gate.** A checksum failure lowers confidence
in *free text* (don't redact a random 9-digit order number that's neither SSN-valid nor
labeled). But a PII-shaped value that is **labeled or format-specific** redacts regardless
of checksum — A5's overcut rule. Validation's job is cutting free-text false positives,
not permitting leaks.

---

## 3. The number problem — magnitude is the discriminator

A bare digit run looks ambiguous, but it isn't, because **physiology and sane units bound
clinical numbers.** Nobody writes `15000mg` — it's `15g`, or `6 x 2500mg`. Units are
*chosen* to keep the value human-sized (`2 tablets`, `BP 120/80`, `HbA1c 6.5`, `O2 94%`).
Identifiers have no such bound. So the discriminator is **size and shape**, deterministic
and tiny — no "clinical region" inference, no allow-list to complete:

1. **5+ consecutive digits → scrub.** Past the unit's job, that magnitude is an identifier,
   not a measurement. Catches long account/MRN/record numbers and solid-written phones.
2. **Leading zeros → scrub.** IDs zero-pad; a dose is never `007mg`. Near-zero false positives.
3. **Separator formats → scrub via regex.** Dashed SSN / grouped phone break the
   consecutive-digit run (`123-45-6789` → runs of 3/2/4), so they get their own patterns.
4. **Everything short + separator-free → keep.** It's a clinical value (decision signal).

This replaces the earlier fuzzy "identifier-region vs clinical-region" gating — that
wasn't even deterministic. Magnitude is. And short identifiers aren't a real worry: a
medical org with a 1–2 digit account number doesn't exist; real IDs are long or zero-padded
→ caught. Labeled IDs of *any* length are caught upstream by the structural label path.

**Carve-out (decided): keep a 5+ run when a clinical unit is adjacent.** Some genuinely
clinical numbers *are* 5–6 digits — HIV/HCV viral load (copies/mL), platelet / ANC counts.
A scrubbed viral load can swing treatment, so rule (1) yields when a unit token sits next
to the number (`copies/mL`, `/µL`, `x10^9`). This is **not a fuzzy heuristic** — it rides
the structural parser: clinical values live under a column header or beside their unit, so
the adjacency read is the same deterministic structural signal we already use for fields.
One adjacency check, no allow-list to complete.

---

## 4. Extensibility (the NFR, again)

Every row in §2 is a **config-declared recognizer**: `labels + regex + optional validator
+ strategy`. `PASSPORT_NUMBER` = one config entry, zero engine change. The clinical
allow-list (§3.3) is config too. Detection config is the *only* thing that changes to add
or tune a type.

---

## Where numbers get decided in a lab table
A results table is where the ID rules and the clinical-keep rules meet: **column headers
type the columns** — scrub the identifier columns (MRN, account, accession), keep the
value columns — while magnitude + unit-adjacency handle anything free-floating. Same
structural parser, three rules composing over one grid.
