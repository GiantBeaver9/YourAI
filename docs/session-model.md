# Session Model — the keystone

Every crypto guarantee we've written ("irreversible across sessions", "vault destroyed on
logout", "Session B can't read Session A") rests on this, and it had been assumed, not
drawn. Here it is. **Kept deliberately small — three objects, no ceremony.**

---

## 1. What a session *is*

A session is an **authenticated working period for one user** — login → logout (or TTL
expiry). It can span **many documents**. It owns exactly one secret: the **session root
key `K_s`** — random, ephemeral, never persisted in a form that outlives the session.

That ephemerality is the whole security model: destroy `K_s` and every token this session
minted becomes unresolvable ciphertext. "Irreversible across sessions" is not a policy —
it's *"the key that could reverse it no longer exists."*

---

## 2. Two independent key hierarchies (don't conflate them)

| | Protects | Lifetime | Key source |
|---|---|---|---|
| **User key** | the **document store** (PHI at rest) | durable, survives sessions | per-user (KDF from user secret / KMS) |
| **Session key `K_s`** | the **session vault** (token↔original map) | **ephemeral**, dies at logout | **random** per session |

They are **not** derived from each other. If `K_s` came from the user key it would be
reconstructable → not ephemeral → "irreversible across sessions" would be a lie. So the
PHI's durable home is the store (user key); the vault is a *transient reversal map*
(session key). A destroyed vault loses nothing permanently — the originals still live in
the store — which is why crypto-shredding it is safe.

```
K_s  (256-bit random, in-memory / KMS-wrapped; zeroized at logout)
 ├─ k_enc = HKDF(K_s, "vault-enc")     → AES-256-GCM key for vault entries
 └─ k_doc = HKDF(K_s, doc_id)          → per-document token namespace
      └─ token = "patient_" + HMAC(k_doc, normalize(value))[:6]
```
Two subkeys, domain-separated by HKDF `info`. `k_doc` per document gives cross-document
non-linkability (ADR-2b); `k_enc` at session level encrypts the reversal map. That's all
the key material there is.

---

## 3. Lifecycle — a 3-state machine

```
        create_session(user_id)
              │
              ▼
        ┌───────────┐   logout / TTL expiry    ┌────────────┐
        │  ACTIVE   │ ───────────────────────▶ │  DRAINING  │
        └───────────┘                          └────────────┘
              │                                       │  leases → 0 (or hard timeout)
              │ admits obfuscate / de-obfuscate       ▼
              │ work under a lease             ┌────────────┐
              └───────────────────────────────│ DESTROYED  │  K_s zeroized, vault cleared
                                               └────────────┘
```

- **ACTIVE** — normal. Admits work; each in-flight operation holds a **lease**.
- **DRAINING** — logout or expiry fired. **No new work admitted** (typed `SessionClosed`).
  Existing leases run to completion, bounded by a hard timeout.
- **DESTROYED** — `K_s` zeroized, vault cleared. Any later token lookup → vault miss →
  leftover-guard. A new session gets a fresh random `K_s` and cannot derive or decrypt
  anything from a prior one.

---

## 4. Concurrency — leases, not locks-everywhere

Async means multiple documents / chunks in flight in one session, all touching one vault.
Kept simple:

- **Keys are read-only after derivation** → concurrent `k_doc`/`k_enc` use is safe, no lock.
- **Vault writes are idempotent**, keyed by token. Determinism guarantees concurrent
  writers of the same value agree on the token → upsert is a no-op; a *different* value
  under a colliding token triggers the A4 extend-check. One short async lock around the
  write path; reads are lock-free dict gets (< 5ms trivially).
- **Leases** are an async refcount: `async with session.lease(): …` wraps a unit of work.
  Teardown waits for the count to reach 0. That's the entire concurrency surface — no
  actor system, no per-entity locks.

---

## 5. The expiry-mid-flight race (the one that bites)

TTL fires while an LLM call is out; the response comes back needing a vault that's being
torn down.

**Resolution:** the lease spans the **whole round-trip** — obfuscate → LLM → de-obfuscate
— so DRAINING cannot destroy the vault until de-obf finishes. Cost: logout may wait a few
seconds for an in-flight call. Bounded by a **hard timeout**: if the LLM hangs past it,
the request is cancelled, de-obf never runs, and the caller gets a typed error.

**Fail-closed on forced teardown:** if the vault is gone when de-obf runs, we do **not**
emit raw tokens or guess — the leftover-guard fires and the caller gets an error. Note the
response only ever contained *tokens* (no PHI), so the worst case is a failed restore, not
a leak. Safe by construction.

---

## 6. Teardown & crypto-shredding

On DESTROYED:
- **Zeroize `K_s`.** Honest caveat: Python can't guarantee wiping immutable `bytes`; we
  hold `K_s` in a `bytearray` and overwrite it, and note that true zeroization / mlock is
  a prod concern (KMS-held key never in app memory in the first place is the real answer).
- **Vault entries were AES-GCM ciphertext under `k_enc`** — even in the in-memory demo we
  encrypt them, so destroying `K_s` *crypto-shreds* the map: the ciphertext is unrecoverable
  without the key, independent of whether the dict is also dropped. That's the demonstrable
  version of "originals cannot be recovered from tokens alone."
- Durable side holds only `token ↔ doc_id` references (no PHI, ADR-2b) → survives for leak
  tracing without being a re-identification liability.

---

## 7. The three objects (anti-bloat)

- **`SessionManager`** — `create_session(user_id) → Session`, `get`, `destroy`. Enforces
  isolation: each session is independent; nothing shared across sessions.
- **`Session`** — holds `session_id, user_id, K_s, state, expires_at`, the vault, the lease
  refcount. `derive_doc_key(doc_id)`, `lease()`, `is_expired()`, `destroy()`.
- **`SessionVault`** — token map; `store`, `resolve`, `contains`; encrypts under `k_enc`;
  cleared on destroy. In-memory demo (meets ephemerality trivially); Redis/Postgres+TTL prod.

Three objects. If a fourth shows up "for flexibility," it's bloat until proven otherwise.

---

## 8. What this unlocks (the PRD-required tests fall straight out)

- **Expired session:** create → expire → de-obfuscate → `SessionClosed` / vault miss, no
  leak.
- **Concurrent session isolation:** two sessions, same user, same value → **different
  tokens** (different `K_s`); Session B `resolve()` on Session A's token → miss.
- **Vault miss on de-obf:** unknown/hallucinated token → guard, no crash, no guess.
- **Destroy-on-logout:** after `destroy()`, the reversal map is crypto-shredded; a replay
  of old tokens resolves to nothing.
