# Regulatory Entity Rules — HIPAA Safe Harbor as the detection spec

Grounding detection in **HIPAA Safe Harbor (45 CFR §164.514(b)(2))** does three things
at once: gives the detector a **completeness checklist** (not an ad-hoc entity list),
hands us **concrete deterministic rules** for the hard types (dates, ages, geography),
and **validates the clinical-signal preservation** decision (ADR-3) as *inside* what the
regulation permits. This is the pre-written answer to Q5 (the HIPAA auditor).

---

## The 18 Safe Harbor identifiers → our recognizers

| # | Identifier | Our handling | Tier |
|---|---|---|---|
| 1 | Names | structural (labels + Title-Case run) + NER residue → **tokenize** | det + prob |
| 2 | Geography < state (street, city, county, ZIP) | tokenize; **ZIP rule** below | det |
| 3 | Dates (birth/admit/discharge/death), ages > 89 | **date/age rules** below | det |
| 4–6 | Phone, fax, email | validated regex → tokenize | det |
| 7 | SSN | validated regex (area/group rules) → tokenize | det |
| 8 | MRN | label-driven + configured formats → tokenize | det |
| 9 | Health-plan beneficiary # | label-driven + regex → tokenize | det |
| 10 | Account numbers | label-driven + regex → tokenize | det |
| 11 | Certificate / license # | label-driven + regex → tokenize | det |
| 12 | Vehicle / plate identifiers | regex → tokenize | det |
| 13 | Device identifiers / serials | regex → tokenize | det |
| 14 | URLs | regex → tokenize | det |
| 15 | IP addresses | regex → tokenize | det |
| 16 | Biometric identifiers | out of scope for text pipeline (documented) | — |
| 17 | Full-face photos | out of scope for text pipeline (documented) | — |
| 18 | **Any other unique identifying number/characteristic/code** | **this is why we fail closed** — the reject/degradation path IS the answer to the catch-all | policy |

The catch-all (#18) is the regulator's admission that *you cannot enumerate every
identifier*. Our fail-closed reject path and confidence-gated redaction are the
principled response: when structure is unparseable or confidence is low, redact/reject
rather than pass through. **#18 is the regulatory justification for the whole
determinism-or-refuse posture.**

---

## The hard deterministic rules (most candidates miss these)

### Dates
- Date elements *directly related to an individual* (DOB, admission, discharge, death)
  → **generalize to year** (drop month + day). This is Safe Harbor #3 verbatim. The kept
  year is truthful → one-way, vault-free, no restoration. Uniform rule, all such dates.
- **Not every date is an individual date:** a dosing interval ("every 4 hours"), a
  lab-result *value*, a generic protocol date are not dates-about-the-individual and stay.
  Classify by **context/label** so temporal reasoning survives where it isn't an identifier.
- **Accepted limitation:** within-year intervals (length of stay) are lost — Safe Harbor's
  own trade. **Exception — pediatric under-5:** dates preserved (day-precise dosing) →
  route to human.

### Ages
- Age **< 90** is **preserved** — Safe Harbor permits it and it's clinical signal.
- Age **≥ 90** → aggregate to **"90+"** (extreme ages are re-identifiers). Deterministic
  rule, cheap, and a concrete detail that signals real HIPAA fluency.

### Geography (ZIP)
- Keep **first 3 digits** of ZIP **only if** that 3-digit area has **> 20,000 people**;
  otherwise → **`000`**. There is a published list of the ~17 restricted 3-digit
  prefixes. Encode it as config (extensible, auditable). Street/city → tokenize.

### Structured IDs
- Validate where a checksum/format exists (SSN area/group constraints, card-like Luhn);
  **validation failure on a PII-shaped string → redact anyway** (A5), never emit raw.

---

## The reconciliation that ties the design together

Safe Harbor **removes** DOB, exact dates, geography, and the direct identifiers — but it
does **not** list **race/ethnicity, sex, or age < 90**. Those are *not* Safe Harbor
identifiers. Therefore preserving them (ADR-3, for pharmacogenomic safety) is **not a
liberty we invented — it is inside what the regulation permits and what clinical
reasoning requires.** The clinical-signal policy and HIPAA de-identification are
*aligned*, not in tension.

> Q5 answer, pre-written: "We implement the 18 Safe Harbor identifiers as deterministic
> recognizers (mapping above), apply the date/age/ZIP generalization rules, tokenize the
> rest, and handle the §164.514(b)(2)(R) catch-all by failing closed. We preserve age<90,
> sex, and ethnicity because they are not Safe Harbor identifiers and are clinically
> load-bearing. The audit log proves *what* was obfuscated per document; the residual gap
> is detection *recall*, which we bound with confidence-gated redaction and the reject
> path — see the audit-gap discussion."

---

## Extensibility check
Every row above is a **config-declared recognizer** (label lexicon + regex/validator +
strategy). Adding `PASSPORT_NUMBER` or a new plan format = a config entry, zero engine
change. Safe Harbor is the *starting* lexicon, not a hardcoded ceiling.
