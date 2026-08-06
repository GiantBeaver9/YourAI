# Detection Rules Engine — detection as data, not code

The endpoint of the config-driven thesis: detection rules are **data**, and the executor is
**Presidio** — we do NOT hand-roll a matcher. The magnitude rules, the Safe Harbor
recognizers, and deployment-specific rules are all the *same shape* — an anchor, a pattern,
a boundary, an action — and each **compiles into a Presidio recognizer** (`PatternRecognizer`
with context words for the anchors, or a custom `EntityRecognizer`) and registers into
Presidio's `RecognizerRegistry`. Our front end holds the logic and the rules; **Presidio
runs detection.** Standard rules ship vetted with an on/off flag; custom rules are deployment
rows that compile the same way. Adding an entity type is a **row**, not a deploy — the
extensibility NFR at its strongest, and it rides the defined tool instead of replacing it.

---

## 1. The rule shape (custom-rule fields drive it)

```
Rule:
  id, name
  entity_type                     # what this catches (for audit/tokens)
  preceding:  str | null          # anchor BEFORE the value — the "after X" (structural)
  regex:      str | null          # the value pattern
  succeeding: str | null          # boundary AFTER the value
  action:     scrub | obfuscate   # default scrub (overcut); obfuscate opt-in
  enabled:    bool                # on/off without deleting
  source:     standard | custom
  priority:   int
```

- `preceding="DEA#:"`, `succeeding="\n"`, `action=scrub` → a structural field rule (as data).
- `regex="\d{5,}"`, `action=scrub` → the magnitude rule (as data).
- `preceding="MRN:"`, `regex="[0-9]{6,10}"` → a hospital's MRN format (as data).

The parser we designed **is** this interpreter — `anchor → boundary` was always this;
now it's rows.

---

## 2. Standard vs custom

| | Standard rules | Custom rules |
|---|---|---|
| Origin | vetted built-ins (Safe Harbor, magnitude, formats) | deployment-authored, own DB table |
| Mutable | **on/off only** | full row (pre/regex/post/action/enabled) |
| Trust | code-reviewed | runtime-supplied → needs guardrails (§3) |
| Scope | global | per-tenant/org (a hospital's DEA format applies org-wide) |

Precedence: standard + custom rules merge into one set; the conflict-resolution pass
(disjoint-union, mask-the-union) from the parsing doc still applies over the merged spans.

---

## 3. Guardrails — validate at the door, once

1. 🔴 **ReDoS, caught at intake — the RE2 compile *is* the validator.** A user regex like
   `(a+)+$` can catastrophically backtrack and hang the pipeline on one document. Rather
   than pay a runtime check on every scan forever, **validate at rule creation**: try to
   compile the regex under a **linear-time engine (RE2)**. If it compiles, that *is* the
   proof it can't backtrack (RE2 rejects backreferences/lookaround — the constructs that
   make backtracking possible). If it doesn't, **reject at the door with a reason**, never
   persist it. Acceptance = safety proof; the DB only ever holds provably-safe rules. A
   cheap runtime match-timeout stays as belt-and-suspenders (§3.3).
2. 🔒 **Additive-only.** A custom rule may **escalate** (catch more) but must **never
   suppress** a standard rule's catch. One fat-fingered row must not open a leak.
3. **Fail closed.** Reject malformed rules at intake; at runtime a broken/timed-out rule is
   **skipped-and-flagged**, never crashes, never silently drops protection.
4. **Default `scrub`.** New rules redact unless someone deliberately opts into obfuscate —
   consistent with the overcut dial.

### Tradeoffs of intake-validation + RE2 (logged, because a reviewer will poke)

- **Expressiveness loss.** RE2 has no backreferences / lookaround, so some regexes get
  rejected. Good trade for a security-critical scrubber (a rule needing a backreference is
  too clever for this path), but a documented limitation, not a free lunch.
- **Safety ≠ correctness (the important one).** Intake proves a rule is *safe to run*, not
  that it *matches what the author intended*. A safe regex can still over-match (destroy
  clinical data) or under-match (leak). Mitigation: the **example/preview panel** at
  creation (§3a).
- **Trust-at-runtime.** Intake-validated rules are trusted later → keep the cheap runtime
  timeout against DB tampering / engine-version drift.

### 3a. The example / preview panel (correctness at the door)

When an author enters a regex, show — before it saves — what it would do. This is the
quick-review guardrail against the over-match half of §3's safety≠correctness, which is the
failure that causes *clinical harm* (eating a lab value), not just a leak.

- **Generated examples (regex → strings).** A generator (`exrex` / `Xeger`) produces a
  handful of strings the pattern matches: "catches things like `123-45-6789`,
  `000-00-0000`." Over-breadth jumps out — type `\d+` and it returns `5`, `0`, `12`, and the
  author sees it would eat dosages. (RE2-restricted → generation is well-defined, no
  backreferences to confound it.)
- **In-context matches (text → highlights).** Run the candidate over a bundled sample doc
  (or a pasted snippet) and highlight exactly what it would scrub, in situ.
- **Breadth warning.** If the pattern matches the empty string, matches 1–2-char strings, or
  is unbounded → soft warn "⚠️ broad rule, likely to over-scrub." Turns the panel from a
  confirmation box into a real guardrail.

Intake flow: type regex → RE2 compiles (safe / rejected-with-reason) → panel shows generated
examples + in-context matches + breadth warning → author confirms → saved.

---

Tradeoff vs. Presidio-style code-registered recognizers: runtime flexibility (ops adds a
rule with no deploy) bought at the cost of these guardrails that code review gives for free.
Worth it — runtime-configurable rules is a stronger extensibility demo.

---

## 4. Ties to the rest

- **Policy object (ADR-3):** the quasi-identifier knobs and these rules are the same idea at
  two altitudes — policy toggles *behavior* on entity classes; rules toggle *detection* of
  patterns. Both are config, both audited.
- **Audit:** rule `id`/`version` can be logged per obfuscation event (not PHI) → provable
  which rule caught what, and which were active for a given document.
- **Extensibility NFR:** "new entity type = config only" is now literally "insert a row."

---

## Open calls
- **Scope:** per-tenant/org (assumed) vs per-user. Org-wide fits the DEA/MRN-format case.
- **`obfuscate` granularity:** does the rule author pick the strategy (tokenize vs
  generalize), or is it binary **scrub vs tokenize**? Leaning binary for the demo — avoids a
  strategy-picker in the rules surface; strategy routing stays in the policy layer.
