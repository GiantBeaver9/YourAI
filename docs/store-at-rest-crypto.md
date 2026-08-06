# Store At-Rest Crypto — the durable side

The session doc established the split: the **user key** protects the durable document
store; `K_s` protects the ephemeral vault. This draws the user-key side. Requirement
(PRD #1): documents encrypted at rest with **AES-256**, **per-user key isolation**.

---

## 1. Envelope encryption (the whole design in one idea)

Do **not** encrypt every document directly with the user key. Use envelope encryption —
the pattern KMS/S3-SSE use — because it makes rotation and per-doc shredding cheap:

```
Master key (env / KMS root)
   └─ wraps →  per-USER KEK        (random 256-bit, one per user)
                  └─ wraps →  per-DOCUMENT DEK   (random 256-bit, one per doc)
                                 └─ AES-256-GCM encrypts the document bytes
```

Stored with each document: `nonce ‖ wrapped_DEK ‖ key_version ‖ AAD-info ‖ ciphertext`.
The DEK in plaintext exists only transiently in memory during read/write.

Why envelope beats direct encryption:
- **Rotation is cheap.** Rotate a user's KEK → re-wrap their DEKs (decrypt+encrypt a
  32-byte key each), the document ciphertext is **never touched**.
- **Per-doc crypto-shred.** Destroy one DEK → that document is irrecoverable, nothing else.
- **Per-user isolation is cryptographic.** User A's KEK cannot unwrap User B's DEKs —
  isolation is a key boundary, not just an access-control `if`.

---

## 2. AES-256-GCM, not Fernet — and bind the context

The PRD lists "Fernet / AES-GCM." **Fernet is AES-128-CBC** — it does *not* meet the
"AES-256" requirement. Use **AES-256-GCM** (`cryptography` `AESGCM`) directly:
- 256-bit, AEAD (confidentiality + integrity in one).
- **AAD = (user_id, doc_id, key_version).** Binding the ciphertext to its owner/doc means
  a stolen blob can't be **swapped** into another user's store and decrypted — the AAD
  won't match. Fernet has no AAD; this is a concrete reason to skip it.
- **Fresh random 96-bit nonce per encryption** (same discipline as the vault). Random DEK
  per doc + random nonce → reuse risk negligible.

---

## 3. Where the keys come from — demo vs prod

| Layer | Demo | Prod |
|---|---|---|
| Master key | 256-bit from `.env` (never committed) | **KMS/HSM root** — never leaves the boundary |
| User KEK | random, wrapped by master, stored | KMS `GenerateDataKey` w/ per-user **encryption context** |
| Doc DEK | random, wrapped by user KEK, in the file header | same |

The demo shows the *full hierarchy* with a master key in env; prod swaps the master into
KMS with **no code change to the envelope logic** — the master is just a wrap/unwrap oracle.

**Multi-device** falls out for free: the KEK's availability is "wherever the user
authenticates to the master/KMS." No device-local key state. (A *password-derived* KEK via
Argon2id is the alternative — simpler, but couples rotation to password changes; KMS is the
cleaner prod answer, so we model the random-KEK-wrapped-by-master path.)

---

## 4. Disk layout (lean)

```
store/{user_id}/{doc_id}.enc     # header + ciphertext, self-describing
store/{user_id}/{doc_id}.meta    # (optional) filename, type, upload ts — non-PII metadata
```
Per-user directory is coarse hygiene; the **crypto** (per-user KEK) is the real isolation.
`key_version` in the header lets a doc encrypted under an old KEK coexist with rotated ones.

---

## 5. The leak vector nobody lists: extracted text

The encrypted-at-rest story is easy to get right for the *upload*. The trap is the
**extracted plaintext** (post-extraction, pre-obfuscation) — it is full PHI and must
**never** hit disk unencrypted. Process in memory; if a large doc must spill (the 50-page
brief), the spill is encrypted too. Temp files, swap, and logs are all PHI-at-rest
surfaces. Demo: in-memory only. Prod: encrypted scratch + `mlock`/no-swap posture. Naming
this is the senior move — the obvious box (upload) is encrypted; the subtle box
(intermediate text) is where real systems leak.

---

## 6. Threats resisted (for the README threat model)
- **Stolen disk / DB dump** → ciphertext only; no key material on disk in plaintext.
- **Cross-user access** → per-user KEK; A cannot decrypt B (cryptographic, not policy).
- **Blob swapping / substitution** → AAD binds ciphertext to (user, doc).
- **Single-document compromise** → per-doc DEK; shred one without touching others.
- **Key rotation under breach** → re-wrap DEKs, fast, no bulk re-encryption.
- **Residual-plaintext leak** → intermediate text never persisted unencrypted (§5).

Open call: password-derived KEK (Argon2id, simplest, self-contained demo) vs
master-wraps-random-KEK (prod-shaped, models KMS). I lean the latter — it *is* the prod
pattern and costs little more.
