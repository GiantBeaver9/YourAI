# Design Decisions & Scope Cut Line

This document is the staff-level artifact: it records the load-bearing decisions with
their alternatives, and — most importantly — the **designed-but-not-built** table that
says which edge cases the prototype deliberately omits and *why*. The goal is to make the
scope boundary a decision, not an accident. A reviewer should never wonder "did they not
know about X?" — every X is here with a reason.

---

## Part A — Architecture Decision Records

Format: Context → Options → Decision → Consequences.

### ADR-001 — At-least-once delivery
- **Context:** A delivery guarantee must be chosen; it dictates the whole model.
- **Options:** at-most-once (delete-on-read), at-least-once (lease+ack), exactly-once.
- **Decision:** **At-least-once.** Exactly-once across a network boundary requires a
  distributed transaction between broker and consumer, which we don't have and which
  every real system fakes with idempotent consumers anyway.
- **Consequences:** Consumers must dedupe on event `id`. We make that cheap (stable id +
  `delivery_count` on every delivery) and document it as a first-class contract.

### ADR-002 — Visibility-timeout lease, not delete-on-read
- **Context:** How does `GET /inbox` interact with storage?
- **Options:** delete on read (simple, at-most-once); lease with visibility timeout + ack.
- **Decision:** **Lease + ack.** A consumer that reads then crashes must not silently
  lose the event. The lease makes the event invisible but recoverable; expiry redelivers.
- **Consequences:** More state (receipt, lease_expires_at) and a sweep loop, but it is the
  only crash-safe model without 2PC. This is the design's spine.

### ADR-003 — Validate the envelope, treat payload as opaque
- **Context:** How strictly do we validate incoming JSON?
- **Options:** strict schema on payload; opaque payload with envelope-only validation.
- **Decision:** **Opaque payload.** TriggersAPI is a transport for *any* system; imposing
  a payload schema defeats the "any system" goal. We validate structure (valid JSON,
  size, payload present) and let consumers own semantics.
- **Consequences:** Forward-compatible and generic. Producers self-describe via
  `event_type`/`source` for consumer-side routing.

### ADR-004 — ULID event ids
- **Context:** Ids must be unique and ideally sortable by time for FIFO-ish ordering.
- **Decision:** **ULID** over UUIDv4 — lexicographically sortable by creation time, so
  best-effort ordering is a free sort with no separate sequence counter.
- **Consequences:** Slightly larger than an int sequence; avoids a global counter
  bottleneck at scale.

### ADR-005 — In-memory store behind an interface (prototype durability)
- **Context:** "In-memory or lightweight persistence is fine" per the brief.
- **Options:** in-memory only; SQLite; abstracted interface with in-memory impl.
- **Decision:** **Interface + in-memory impl**, with DynamoDB (conditional writes + TTL)
  as the documented production target and Postgres (`FOR UPDATE SKIP LOCKED`) as the
  alternative. The atomic-lease operation is identical in shape across all three.
- **Consequences:** Restart loses data (accepted, documented). Swapping durability is an
  implementation of one interface, not a rewrite — this is what makes the cut defensible.

### ADR-006 — Best-effort ordering; `ordering_key` designed, not built
- **Context:** Do we promise ordering?
- **Decision:** **Best-effort FIFO by ULID.** Strict global ordering is incompatible with
  competing consumers and redelivery. A per-key ordering mechanism (`ordering_key`,
  at most one in flight per key) is fully designed but out of the v1 build.
- **Consequences:** Simpler, higher-throughput core. See cut table for the why.

### ADR-007 — Producer idempotency key with a bounded dedup window
- **Context:** Producers retry POSTs on timeout; naive ingestion duplicates events.
- **Decision:** Optional `idempotency_key`; a repeat within the window returns the original
  event (`200`). The window is **bounded** so the dedup map can't grow without limit.
- **Consequences:** Solves the most common real duplication source cheaply; bounded memory.

### ADR-008 — `problem+json` (RFC 7807) errors
- **Decision:** Every error is a machine-readable `application/problem+json` object with
  `type/title/status/detail/instance`. Predictable errors are half of good DX.

### ADR-009 — Pull + long-poll, not push
- **Context:** The brief asks to "simulate wake-up behavior."
- **Options:** pull short-poll; pull long-poll; push (webhooks/WebSocket/SSE).
- **Decision:** **Pull with optional long-poll.** Long-poll delivers the wake-up feel with
  none of push's delivery-tracking, retry-to-endpoint, and connection-management cost.
- **Consequences:** Consumers hold a request open; trivially simulated by the example client.

### ADR-010 — Dead-letter after `max_receive_count`
- **Decision:** A poison event that exceeds the redelivery cap moves to a DLQ and stops
  being delivered, with an operator `redrive`. Prevents one bad event from starving a
  consumer forever.

### ADR-011 — Know when not to build the broker (build vs buy)
- **Context:** SQS already implements visibility timeouts, DLQ, and at-least-once.
- **Decision:** The prototype hand-rolls the queue to *demonstrate understanding*, but the
  doc explicitly states that a production TriggersAPI should likely back the HTTP contract
  with SQS + a thin metadata table. The product's value is the public contract and DX, not
  a novel broker.
- **Consequences:** Signals judgment: we understand the mechanism *and* when reinventing it
  is the wrong call.

---

## Part B — The scope cut line (designed vs built)

Everything below is **designed in the PRD** and **intentionally excluded from the 6–7h
prototype build.** This table is the point of the whole exercise.

| # | Capability | Why it's designed | Why it's cut from v1 | Cost to add later |
|---|---|---|---|---|
| 1 | **Durable storage (DynamoDB/Postgres)** | Real reliability needs cross-restart durability | Brief allows in-memory; the interface (ADR-005) makes it a swap, so building it now proves nothing extra | ~0.5 day: one `EventStore` impl, conditional writes |
| 2 | **Producer auth (HMAC signature)** | Public ingress must authenticate | Auth is orthogonal to the delivery semantics being demonstrated; adding it risks eating the whole timebox on secret management | ~0.5 day: middleware + secret store |
| 3 | **Rate limiting / backpressure (429)** | Protects the service under burst | No adversarial load in a prototype; token-bucket is well-understood and low-risk to defer | ~2 hrs: token bucket + `Retry-After` |
| 4 | **`ordering_key` (per-key serialization)** | Some consumers need ordered processing per entity | Adds a second locking dimension; core value (crash-safe at-least-once) is shown without it | ~0.5 day: per-key in-flight guard |
| 5 | **Server-side `event_type` filtering on inbox** | Consumers often want a subset | Cross-cuts into subscriptions; single-queue competing-consumers is enough to demonstrate delivery | ~2 hrs: predicate in `lease()` |
| 6 | **Topics / subscriptions (fan-out)** | Multiple independent consumers per event | Changes the data model (per-subscription cursors); a v2 concern, not a delivery-semantics one | ~1–2 days |
| 7 | **Multi-instance / horizontal scale** | Production throughput | Requires the shared store (#1) first; single-instance proves correctness | Follows from #1 |
| 8 | **Distributed tracing** | Prod observability | Metrics + structured logs cover the demo; tracing is additive | ~2 hrs |
| 9 | **AWS deployment** | "AWS preferred" | A `docker run` + curl examples demonstrates operability; live infra burns hours with no design signal | ~0.5 day: ECS/Lambda + IaC |

### What *is* in the v1 build (the cut's other side)
The prototype implements the crash-safe core end to end: `POST /events` with idempotent
replay, `GET /inbox` with atomic leasing and long-poll, `ack`/`nack`/`extend` with receipt
authenticity, lease-expiry redelivery with backoff, dead-lettering + redrive, the event
inspect endpoint, `/metrics`, an `EventStore` interface with an in-memory impl, an example
client, and a test suite that proves each semantic (§14 of the PRD). That is the minimum
that actually demonstrates *reliable delivery* rather than a store-and-list inbox.

### The one-line rationale for the whole cut
Every deferred item is either (a) orthogonal to the thing being graded (delivery
semantics + clean DX), or (b) gated behind the durable-store swap that the interface
already anticipates. Nothing cut would change the core design; each is an implementation
of a seam that already exists. That is the difference between "ran out of time" and
"scoped deliberately."
