# TriggersAPI — Decisions

Record of decisions made. Author-driven. No design added beyond what's decided.

## Decided

1. **Storage.** Events are stored in the DB immediately on `POST`. Durable — not held in memory, not lost.

2. **Ingress idempotency.** Enforced by a DB constraint on the way in. Idempotency is only a concern on ingest; not on delivery.

3. **Delivery = against the grain (anti-join).** `/inbox` returns events not yet delivered to the customer — a `LEFT JOIN` against the delivered/read set, returning the rows with no match. We serve, then record the read (not mark-then-serve) — chosen to avoid locking/serializing the hot poll path, **not** for write economy. The anti-join is a **stateless "what hasn't this customer seen" query** (no per-consumer offset, no ack round-trip), **not** a dedup mechanism. `/inbox` is therefore **at-least-once**: under concurrent polls the same event may return twice, and consumers dedupe on event id (the standard webhook contract). This pull anti-join inbox is **one of the two defining features** separating this project from ST6 (ST6 is push-only: a `status` column and a `LEFT JOIN LATERAL` attempt-rollup, no per-recipient read concept).

4. **Why against the grain.** Marking items as read first means too many DB reads/writes (write amplification). Read-heavy anti-join is the deliberate choice to keep the write load down. This is the core thesis of the design.

5. **Finality.** The read is recorded when the `/inbox` call returns `200`. That 200 is the delivery event.

6. **Out of scope: the after-200 window.** If we return 200 and the consumer dies immediately after, that is not the API's problem. At-most-once on that razor is accepted. Not designing for it.

7. **Endpoints.** `/inbox` (undelivered events) and `/last?num=x` (last x items).

8. **Multi-customer.** Infinite customers supported. Partition later (by customer). The DB-backed design keeps partitioning open as a future move.

9. **Delivered set is per-customer.** Each customer has its own unread set. The anti-join is keyed on `customer_id` — an event is delivered/read per customer, not globally. That's the whole reason there's a customer key alongside the event ID.

10. **Customer identification.** `POST`/`GET` on inbox requires a `customer_id` or password to identify the customer. **Preferred for production:** an auth token that identifies the customer instead. That's out of scope now — recorded as the production direction, not built in v1.

11. **Two directions, two writes — don't conflate.** (a) Ingest `POST`: the event is persisted durably *immediately*, before any delivery (decision #1). (b) Endpoint push delivery (stretch): retry with backoff and persist the *delivered marker* only on a `200`. The event is always written first; only the delivered/read marker is retry-gated. ("Retry before writing to the DB" refers to the marker write, not the event write.)

12. **Three surfaces — logically separate, jointly coherent.**
    - `POST /events` (ingest): an append-only **event stream**, not a pure mutable list.
    - `/inbox`: **pure new** — the per-customer anti-join over undelivered events; records the read on the `200`.
    - `/last?num=x`: **pure read** — a read-only peek at the last x items; does *not* record a read or touch delivery state.

    They're semantically distinct but coherent within the one application. The **event-stream framing (vs ST6's pure list)** is the *second* of the two defining differences from ST6 — the anti-join inbox (decision #3) is the first.

13. **Delivery guarantees are per-surface — two mechanisms, not one.**
    - **Pull `/inbox`:** at-least-once via the anti-join. Duplicates possible under concurrent polls; the consumer is idempotent on event id. The anti-join is a stateless unseen-set query, not a dedup — dupes are the accepted currency, not a bug.
    - **Push subscription (stretch):** at-least-once via retry-with-backoff until a downstream `200` (stretch #2/#3).

    Separate paths, separate delivery stories, by design — do not conflate them. (The post-`200` client-crash razor on `/inbox` stays the accepted negligible gap per decision #6; whether an explicit ack should close it is the open ack-flow question.)

## Stretch options (brief's advanced list — "explore 1 or 2")

1. **Subscriptions & filtering — approach per ST6.** Customer-controlled one-hot `(customer, event_type) → URL` subscription matrix behind a read-through cache (`RWMutex`; a read colliding with a refresh briefly pauses). Filtering is late / delivery-time, so a subscription change takes effect on the next config poll with no redeploy. (Ref: ST6 — `customer-service/internal/cache`, `event-handler/internal/deliver`.)

2. **Push delivery — approach per ST6.** Deliver to registered consumer URLs. Workers iterate a per-customer list of queues, each guarded by a `TryLock` mutex ("skip, never steal") with a round-robin cursor for fairness → per-customer FIFO, no double-send. Retry with fixed exponential backoff (3s→10s→30s→1m→2m), 5 attempts then `suspended` (kept at head, never dropped); redirects not followed (3xx = failure). No jitter (future seam, not claimed). (Ref: ST6 — `event-handler/internal/queue`, `.../deliver`.)

3. **Delivery guarantees — at-least-once (per ST6).** Backoff and await a `200`; retry before writing the **delivered marker** (see decision #11 — the event itself is already durable from ingest). Exactly-once is explicitly not wanted here. Rationale: at-least-once would hammer a downed consumer, so backoff + retry before marking delivered is the correct course. (Ref: ST6.)

4. **Shared inbox / multi-consumer — decided.** We write per customer (see decision #9); otherwise one customer ruins the read for everyone.

5. **Monitoring & observability — DB-backed log, not a metrics subsystem.** Right framework from ST6, not its exact tooling: a durable, queryable per-attempt / per-event log (attempt #, status code, error, timestamp) plus status per event. No Prometheus / `/metrics` / latency-histogram layer — that's not required. Counts/latencies/success-rates are derivable from the log if asked. (Ref: ST6 — `attempts` table.)

6. **DX enhancements (CLI / dashboard) — out of scope.** New; not worth getting into. Brief says explore 1–2, and several above are already covered.

7. **Explorer UI — out of scope.** Not worth the time.

## Open (not yet decided — do not invent)

_None open._
- **Contrast vs ST6:** what specifically the "finality" change is relative to the prior project.
