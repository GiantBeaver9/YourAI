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

## 3. The number problem — identifier vs clinical (the crux)

A bare digit run is ambiguous. Medicine is *full* of load-bearing numbers we must NOT
touch: `2 tablets`, `every 4 hours`, `BP 120/80`, `HbA1c 6.5`, `500 mg`, `O2 94%`. Blanket
"redact all numbers" is **clinically dangerous** — same class of harm as ADR-3's
pharmacogenomics point. So:

**Resolution — format-specific + context-gated, never bare-digit-greedy:**
1. **Format-specific IDs are safe to catch anywhere** — a dashed SSN, a grouped phone, an
   email, a Luhn-valid 16-digit card carry enough structure that clinical collision is
   near-zero. Catch them in prose freely.
2. **Bare/ambiguous digit runs are context-gated:**
   - in an **identifier region** (a form field, near an ID label) → redact (overcut);
   - in a **clinical region** (near dosing/vitals/lab units, or matching a known clinical
     pattern) → **keep** — it's decision signal.
3. **Known clinical patterns are protected** (units, vitals, dose forms) — an explicit
   allow-list that *prevents* redaction, the inverse of the label lexicon.

This **inverts** the naive "scrub every number." The default for an ambiguous bare run in
a clinical context is **keep** (preserve signal), because a labeled/format-specific ID is
what we actually leak on, and those we catch deterministically. This is a *conscious*
inversion, justified by the clinical-signal principle — and it's the one place the
overcut-everything reflex is deliberately restrained, because here overcut = clinical harm.

⚠️ The residual: a truly bare, unlabeled SSN-as-9-digits with no dashes sitting in prose is
the hard miss. Mitigations: the `\d{9}` variant with SSN validation + proximity to
name/DOB raises its confidence; below threshold in an identifier-plausible region → redact.
A genuinely context-free `123456789` is the documented edge — rare, and the structural/label
path catches the vastly more common labeled case.

---

## 4. Extensibility (the NFR, again)

Every row in §2 is a **config-declared recognizer**: `labels + regex + optional validator
+ strategy`. `PASSPORT_NUMBER` = one config entry, zero engine change. The clinical
allow-list (§3.3) is config too. Detection config is the *only* thing that changes to add
or tune a type.

---

## Open call
The §3 inversion (ambiguous bare digits in clinical context default to **keep**) trades a
rare, hard-to-catch bare-SSN-in-prose miss for not destroying clinical numbers. Given the
domain I believe it's correct — clinical-number destruction is a silent safety event,
bare-unlabeled-SSN-in-prose is rare and partly caught by validation+proximity. But it *is*
a deliberate loosening of overcut, so it's your call to ratify.
