# Deterministic Structural Parsing — the real detection core

The naive framing is "run NER over the text." That's the *fallback* layer. In real
medical / legal / financial documents, most PII lives in **structure** — labeled form
fields, tables, headers, signature blocks — where the *layout*, not the content,
tells you what is PII and where it ends. Structure is deterministic. A label named
`SSN:` guarantees the value after it is an SSN even if it's mistyped `123-4-56789`.
Content-pattern matching would miss that; a structural parser cannot.

This is the concrete expression of the determinism thesis: **parse the structure →
provable, auditable removal with zero model in the loop for the bulk of fields.** NER
only mops up the unlabeled prose residue.

⚠️ This entire layer depends on **structure-preserving extraction** — the "readable
document" assumption made precise. See §5: DOCX gives structure natively, PDF requires
coordinate reconstruction, scanned images give nothing (out of scope).

---

## 1. The document-structure taxonomy (what we actually parse)

Every pattern below is a real layout in intake forms, EHR exports, billing statements,
and legal captions. Each needs a *different* anchor→direction→boundary rule.

### 1a. Inline labeled field, same line
```
Patient Name: John Smith
SSN:  123-45-6789
DOB - 01/02/1980
Member ID .......... 0043215
```
Value runs from the delimiter to **end of line (the carriage return)**. The delimiter
varies: `:`, `-`, dot-leaders, or *pure whitespace alignment* with no delimiter at all.
→ **Anchor** label; **Direction** INLINE_AFTER; **Boundary** to-EOL *or to-next-label*.

### 1b. Multiple fields on one line
```
Name: John Smith    DOB: 01/02/1980    MRN: 00432
```
Boundary-to-EOL is **wrong here** — it would swallow `DOB:` and `MRN:` and their values
into the name. Boundary must stop at **the next label**, not the carriage return.
🔴 This is a leak-adjacent bug: mis-bounding either over-deletes (utility) or, if the
name value is taken as "everything to EOL," you tokenize the DOB *inside* the name token
and de-obf can't restore it cleanly.

### 1c. Label above value (value on next line)
```
Patient Name
John Smith
```
→ **Direction** NEXT_LINE; value is the next non-blank line.

### 1d. Value above label — "delete things prior" (signature blocks)
```
John Smith
_______________________
Patient Signature / Print Name
```
The label is *below* the value, often with a rule line between. → **Direction** PREV_LINE
(skip rule/underscore lines). This is the case flat top-down parsing misses entirely.

### 1e. Tabular / columnar
```
Name           DOB          MRN
John Smith     01/02/1980   00432
Jane Doe       03/04/1975   00433
```
Header row types the columns; every data row's cell **under** a PII column is a value.
→ Detect header, map **column character-ranges (or PDF x-coordinates)**, redact each
cell below a PII-typed header. Needs column-boundary detection, not line parsing.

### 1f. Multi-line / continuation values
```
Address: 123 Main St
         Apt 4B
         Springfield, IL 62704
```
Value continues across lines until the **next label or a blank line**. Indentation is a
signal but not reliable. → **Boundary** TO_NEXT_LABEL_OR_BLANK, multi-line capture.

### 1g. Running headers / footers
Patient name / case caption repeated on every page of a 50-page document. → Detect the
repeated structural element once; redact **all** occurrences (they share one token).

### 1h. Blank / unfilled fields
```
Emergency Contact: ______________
Secondary Insurance:
```
Label present, **value empty**. Must NOT grab the next line as the value (would delete
non-PII content, or worse, tokenize a real sentence). → Distinguish empty-value from
value-on-next-line. **This is the nastiest ambiguity — see P2.**

### 1i. Free-prose narrative (the residue)
Clinical notes, legal argument — no labels. `"The patient, an active smoker, reported..."`
→ Structural parser has nothing to anchor on. **This** is where NER + content-regex +
graceful degradation live. It is the *minority* of a form-heavy document but the
*majority* of a narrative one — the mix determines how much we lean deterministic.

---

## 2. The parsing model — anchor → direction → boundary → unit

Not a regex list. A small, config-driven rule engine. Each field type declares a rule:

```
FieldRule:
  labels:     [ "Patient Name", "Name", "Pt Name", "Guarantor" ]   # lexicon, config-only
  entity_type: NAME
  direction:  INLINE_AFTER | NEXT_LINE | PREV_LINE | COLUMN_BELOW | RIGHT_OF
  boundary:   TO_EOL | TO_NEXT_LABEL | TO_BLANK_LINE | COLUMN_WIDTH | N_LINES(k)
  unit:       VALUE_ONLY | WHOLE_LINE            # what gets replaced/cleared
  when_empty: SKIP                                # guard against grabbing next line
```

- **Anchor** — a label match. Position-sensitive: a label at line-start followed by a
  delimiter is a field; the same words mid-sentence in prose are *not* (P4).
- **Direction** — where the value sits relative to the anchor. Covers 1a–1e.
- **Boundary** — where the value ends: the carriage return, the next label, a blank
  line, a column edge, or a fixed line count. Covers 1b, 1f.
- **Unit** — replace the *value* with a token (keep the label — "there is a patient" is
  useful, non-identifying context the LLM needs), OR clear the **whole line** (signature
  blocks, where the label itself is on a different line).

**Extensibility (the NFR, for real):** adding `PASSPORT_NUMBER` = add labels + pick a
rule template in config. Zero parser-logic change. This is a stronger extensibility
story than "add a Presidio recognizer" because it's *deterministic* and *auditable*.

**Precedence:** structural layer runs **first** and wins. A field it claims is frozen
(A2/A6 in the failure register) and never re-examined by NER. NER only sees the gaps.

---

## 3. Parsing-specific failure modes (each → a test)

Distinct from the pipeline register — these are about **grabbing the wrong span**,
where the two failure directions are: 🔴 under-grab = PII leaks, 🔵 over-grab = utility
loss / a real sentence destroyed.

- **P1 Delimiter variety** — `:`, `-`, dot-leaders, whitespace-only alignment. The
  no-delimiter case (`Name        John Smith`) needs column-position inference; you
  cannot split label/value by a delimiter that isn't there. *test: all delimiter styles.*
- **P2 Empty field vs value-on-next-line** 🔵🔴 — `Name:` followed by a blank vs `Name:`
  followed by `John Smith` on the next line. If we always take NEXT_LINE we delete
  innocent content on empty fields; if we never do, we leak the next-line case. **No
  clean rule** — heuristic: NEXT_LINE only if same-line value is empty *and* the next
  line is non-blank *and* doesn't itself start with a label. Document the residual risk.
- **P3 Boundary stops at next label, not EOL** (1b) 🔴 — *test: multi-field line masks
  each value independently.*
- **P4 Label-shaped prose** 🔵 — "the date of birth requirement applies" is not a DOB
  field. Anchor must require line-start + delimiter / form-region context, not a bare
  substring. *test: label words in a sentence are not treated as a field.*
- **P5 PREV_LINE lookback depth** (1d) — how many lines back past rule/underscore/blank
  lines before giving up? Signature blocks vary. Bound it (e.g. skip pure-punctuation
  lines, take the nearest text line within k). *test: signature block with a rule line.*
- **P6 Column detection** (1e) 🔴🔵 — whitespace-aligned vs true PDF cells; ragged
  columns; wrapped multi-line cells that break alignment; a header spanning two columns.
  Whitespace alignment is fragile; PDF x-coordinates are reliable; DOCX table cells are
  exact. *test: aligned-text table AND a real DOCX table.*
- **P7 Continuation capture** (1f) 🔵🔴 — where does a multi-line address stop? Over-
  capture deletes following content; under-capture leaks line 2–3 of the address.
  *test: 3-line address bounded by the next label.*
- **P8 Reading order / column interleave** 🔴 — THE deterministic-parser killer. A
  two-column legal document extracted by a naive PDF reader interleaves left and right
  columns into garbage adjacency, so "after X" points at the wrong text. Breaks the core
  assumption. → must reconstruct lines by **y-coordinate** and columns by **x-clustering**
  before any anchor→direction reasoning. *test: two-column PDF preserves reading order.*
- **P9 Line-ending / soft-wrap normalization** — CR vs LF vs CRLF vs form-feed; a hard
  field break vs a display soft-wrap. Normalize; distinguish. *test: mixed line endings.*
- **P10 Repeated header/footer** (1g) — redact all occurrences, one token. *test.*
- **P11 Value is itself prose with embedded PII** (1k) — a "Chief Complaint:" field whose
  value is a paragraph → hand the captured span back to NER, don't assume it's atomic.
- **P12 Redaction unit choice** (VALUE_ONLY vs WHOLE_LINE) — wrong choice either leaves
  the label's value or destroys structural context. Per-rule decision. *test both.*
- **P13 Relationship-typed duplicate labels** — "Patient Name" vs "Emergency Contact
  Name" vs "Referring Physician" — all NAME, all redacted, but *different people/tokens*.
  Lexicon must cover all; each occurrence tokenizes independently. *test.*
- **P14 Over- vs under-grab is the whole game** — restate as a policy: when the span
  boundary is ambiguous, **grab more** (utility cost) rather than less (leak). Security
  default, consistent with A5.

---

## 4. How this layers with everything else

```
extract (structure-preserving)
   → normalize lines / reading order            [§5, P8, P9]
   → STRUCTURAL PARSE  (deterministic, high-confidence, config-driven)   ← the core
        anchor → direction → boundary → unit; freeze claimed spans
   → NER + content-regex on the RESIDUE only    (prose, §1i; graceful degradation)
   → conflict resolution over the union         [A3]
   → offset-safe replacement / tokenization      [A2]
   → chunk → LLM → de-obf → restore
```

The structural layer is the deterministic, provable majority. NER is the probabilistic
minority, confined to prose, with redact-on-low-confidence. This inverts the usual
design (NER-first) and *that inversion is the differentiator* — it's why we can claim a
technical guarantee for form fields instead of a model's best guess.

---

## 5. Extraction fidelity — the "readable document" assumption, made precise

Structural parsing is only as good as the structure we can recover:

| Format | Structure available | Parsing confidence |
|---|---|---|
| **DOCX** | Native XML: paragraphs, **tables (`<w:tbl>`)**, runs. Columns/cells are *exact*. | Highest — deterministic by construction |
| **PDF (text layer)** | Words with **x/y bounding boxes** (pdfplumber/pdfminer). Reconstruct lines by y, columns by x. Reading order must be rebuilt (P8). | High, *after* coordinate reconstruction |
| **TXT** | Line breaks + whitespace only. Inline/next-line/prev-line work; column tables are whitespace-heuristic. | Medium |
| **Scanned PDF / image** | None without OCR → probabilistic → **out of scope** (documented gap). | N/A |

**Demo vs prod:** demo parses DOCX + text-layer PDF + TXT with a coordinate-aware
extractor. Prod adds an OCR pre-stage *behind the boundary* that emits structure with
confidence scores, feeding the same rule engine — but OCR output is probabilistic, so
those fields drop to the NER/graceful-degradation confidence tier, never the
deterministic tier. Keeping that tier distinction explicit is the honest move.

---

## 6. Open questions worth resolving before code

1. **Extractor:** pdfplumber (coordinates, tables) vs pdfminer.six (lighter, more manual)
   vs PyMuPDF (fast, good layout). I lean **pdfplumber** for the demo — words-with-bboxes
   and table extraction out of the box map straight onto §2. Heavier dep; acceptable.
2. **P2 empty-field heuristic** — the single most ambiguous rule. Do we accept the
   documented residual risk of the "empty vs next-line" guess, or require a delimiter +
   same-line-empty + non-label-next-line conjunction (safer, misses some next-line
   layouts)? This is a real precision/recall dial with no clean setting.
3. **How much column/table logic in the demo** — full x-coordinate column clustering is
   real work. Minimum credible: DOCX native tables (exact, cheap) + whitespace-aligned
   text tables (heuristic) + document PDF-column-clustering as prod. Or do we invest in
   PDF coordinate columns for the demo because tables are where dense PHI lives?
4. **Label lexicon scope** — how broad for the demo? A focused medical+legal+financial
   starter lexicon (~40–60 labels) demonstrates the config-driven claim without trying to
   be exhaustive. The *mechanism* is the deliverable, not lexicon completeness.
5. **Reading-order reconstruction (P8)** — do we handle multi-column PDFs in the demo, or
   assume single-column and document multi-column as a gap? Multi-column is common in
   legal briefs (the PRD's own Q1 example is a 50-page legal brief). Might be worth it.
