# PHI Domain Brief — what to hold cold for the room

Not lines to memorize — the *mental models* that let you reason live when they probe past
the first answer. Everything here is accurate HIPAA/de-identification knowledge. Understand
the **why**; the answers to specific questions fall out of it.

---

## 1. What PHI is (say it cold)

**PHI = Protected Health Information: individually identifiable health information created,
held, or transmitted by a covered entity or business associate, in any form.**

"Individually identifiable health information" has **two prongs — both required**:
1. it **relates to** past/present/future physical or mental health, the provision of care,
   or **payment** for care, AND
2. it **identifies** the individual, or could reasonably be used to identify them.

**The core insight:** PHI is the *intersection* of **health context** and **identifiability**.
A diagnosis with no way to tie it to a person is not PHI. A name in a medical record is PHI
because it *links* the health data to a human. **The protected thing is the LINK, not the
medical fact.** De-identification = breaking that link.

- **Covered entities:** providers, health plans, clearinghouses.
- **Business associate:** a vendor that handles PHI on their behalf. **YourAI is a business
  associate** → operates under a **BAA** (Business Associate Agreement). Say this — it shows
  you know where the company legally sits.

---

## 2. The identifiability principle (the intellectual core)

De-identification is **not** "delete the sensitive fields." It's **breaking linkability**,
and linkage happens through *combinations*:

- **Latanya Sweeney (the citation to know):** ~**87% of Americans are uniquely identified by
  just {5-digit ZIP, gender, date of birth}.** None of those three is "sensitive" alone;
  together they're a fingerprint. (Later estimates vary with dataset — ~63% in some — but 87%
  is the canonical number and the point stands.)
- This is *why* DOB is PHI even though "born in March" sounds harmless — the **precision** is
  the identifier. It's why our date rule kills month/day (identifier) but keeps year (age =
  clinical signal).
- **Quasi-identifiers** (ZIP, age, sex, ethnicity) are PHI **in combination** even when weak
  alone. This is the whole reason the multi-patient-table row is dangerous.
- Formal models to name-drop correctly: **k-anonymity** (each record indistinguishable from
  ≥ k−1 others on quasi-identifiers), and its refinements **l-diversity** and **t-closeness**.

---

## 3. The two ways to de-identify under HIPAA (§164.514) — MOST CANDIDATES MISS THIS

Knowing there are **two** methods, and which one you built, is a senior differentiator:

1. **Safe Harbor (§164.514(b)(2)):** remove all **18 enumerated identifiers** → deemed
   de-identified. Mechanical, conservative, no statistician required. **This is what our
   pipeline automates.**
2. **Expert Determination (§164.514(b)(1)):** a qualified statistician certifies the
   re-identification risk is "very small." Lets you *keep more* data (e.g., some dates) when
   the statistical risk is low.

**Your power move:** "We automate Safe Harbor by default, but the quasi-identifier policy
layer is a step toward Expert Determination — we preserve clinically-load-bearing attributes
under an explicit, logged risk decision rather than blindly stripping them." That sentence
alone shows you understand the regulatory frame, not just a library.

---

## 4. The 18 Safe Harbor identifiers (compressed) + the non-obvious rules

Names · geography < state · **all dates except year + ages ≥ 90** · phone · fax · email ·
SSN · MRN · health-plan # · account # · certificate/license # · vehicle IDs · device IDs ·
URLs · IPs · biometrics · full-face photos · **any other unique identifier (catch-all)**.

The rules that prove you actually read the reg:
- **Dates:** strip to year; **ages ≥ 90 → "90+"** (extreme age is a re-identifier — the 90+
  population is tiny).
- **ZIP:** keep first 3 digits **only if** that 3-digit area has **> 20,000 people**, else
  `000` (~17 restricted prefixes).
- **Catch-all (#18):** the reg's admission that you can't enumerate everything → this is the
  regulatory justification for our **fail-closed / reject-to-human** posture.

---

## 5. The clinical nuance that makes you stand out (this is the differentiator)

Over-anonymizing can *cause clinical harm* — **pharmacogenomics is real**:
- **HLA-B\*15:02 + carbamazepine** in patients of **Han Chinese / SE Asian** descent →
  Stevens-Johnson syndrome / toxic epidermal necrolysis. **FDA recommends genetic screening.**
- **Warfarin** dosing varies by **CYP2C9 / VKORC1** genotype; **codeine** by **CYP2D6**
  metabolizer status; **G6PD deficiency** prevalence varies by ancestry.

So **ethnicity/ancestry is decision-relevant clinical signal**, and Safe Harbor **does not
list race/ethnicity, sex, or age < 90** as identifiers → they're **preservable**. Preserving
them isn't a liberty we took; it's *inside the regulation* and *clinically necessary*.

**The bias corollary:** a realistic pseudonym (fake name) reintroduces bias vectors
(name → inferred ethnicity/gender/class → treatment disparity). A neutral token
`[PATIENT_a3f2]` carries none. So obfuscation is *also* a **de-biasing** layer — privacy +
fairness in one move.

---

## 6. Adjacent regimes (one line each — know they exist)

- **HIPAA** — US health. **GDPR** — EU personal data; "pseudonymisation" is a *defined term*
  (Art 4(5)); right to erasure (Art 17). **CCPA/CPRA** — California. **GLBA** — financial
  privacy. **PCI-DSS** — card data. **42 CFR Part 2** — substance-use-disorder records,
  *stricter* than HIPAA. **Attorney-client privilege / work-product** — the legal track.

---

## 7. The honest limits (maturity, not weakness)

- Safe Harbor de-id **≠ zero risk** — residual re-identification survives, especially in
  **small populations** and **rare diseases** (a rare dx + a region can re-identify with no
  name).
- **Free text is the hard part** — structured fields are easy; narrative notes leak.
- **Recall is the security-critical axis** — a false negative leaks; a false positive just
  over-redacts.

---

## 8. Mapped to the live-review questions

- **Q2 (co-reference):** unsolvable by token replacement; name the mitigations, don't fake it.
- **Q3 (adversary infers):** quasi-identifier combination = k-anonymity residual; conscious
  documented tradeoff (Sweeney is your citation).
- **Q4 (vault SPOF):** split custody of token-map vs key (KMS); crypto-shred on logout.
- **Q5 (HIPAA auditor, prove no PHI in 30 days):** Safe-Harbor-mapped recognizers + token-only
  audit prove obfuscation *executed*; the gap is *recall* (a miss is invisible to the log) →
  closed by egress DLP + detection metrics. **Naming the gap is the senior answer.**
- **Q6 (cross-session non-determinism):** "the key that could reverse it no longer exists";
  also defeats **cross-session linkage** (same patient → same token across sessions would let
  a provider correlate — a persistent pseudonym is itself a re-identifier).

---

## The one framing that ties it all together
> "PHI isn't a checklist of scary words — it's a **re-identification risk judgment**. Our job
> is to maximize privacy *subject to* preserving decision-relevant clinical signal, under an
> explicit, auditable policy. That's why we tokenize identity, generalize dates, preserve
> clinical quasi-identifiers under a logged knob, and fail closed on anything we can't read."

If you can say that and *mean* it — and defend each clause with the why above — you're not
hanging with Jesus. You're the one asking *them* whether they've read §164.514.
