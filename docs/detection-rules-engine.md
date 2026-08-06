# Detection Rules Engine — detection as data, not code

The endpoint of the config-driven thesis: detection is **one interpreter iterating a table
of rules.** The structural parser, the magnitude rules, the Safe Harbor recognizers, and
deployment-specific rules are all the *same shape* — an anchor, a pattern, a boundary, an
action. Standard rules ship vetted with an on/off flag; custom rules are deployment rows in
their own table. Adding an entity type is a **row**, not a deploy — the extensibility NFR at
its strongest.

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

## 3. Guardrails — the engineering of "users supply regex"

1. 🔴 **ReDoS.** A user regex like `(a+)+$` can catastrophically backtrack and hang the
   pipeline on a single document — and break the determinism/latency guarantees. Build the
   matcher on a **linear-time engine (RE2 / Rust `regex` / Go `regexp`)**, or enforce a
   per-rule match timeout + validate at creation. RE2 is the clean answer — no backtracking,
   linear, and it *reinforces* the determinism thesis.
2. 🔒 **Additive-only.** A custom rule may **escalate** (catch more) but must **never
   suppress** a standard rule's catch. One fat-fingered row must not open a leak.
3. **Fail closed.** Validate regex at creation (reject bad rows there); at runtime a broken
   rule is **skipped-and-flagged**, never crashes, never silently drops protection.
4. **Default `scrub`.** New rules redact unless someone deliberately opts into obfuscate —
   consistent with the overcut dial.

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
