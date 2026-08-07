# TriggersAPI — Decisions

Record of decisions made. Author-driven. No design added beyond what's decided.

## Decided

1. **Storage.** Events are stored in the DB immediately on `POST`. Durable — not held in memory, not lost.

2. **Ingress idempotency.** Enforced by a DB constraint on the way in. Idempotency is only a concern on ingest; not on delivery.

3. **Delivery = against the grain (anti-join).** `/inbox` returns events not yet delivered to the customer — a `LEFT JOIN` against the delivered/read set, returning the rows with no match. We serve, then record the read (not mark-then-serve) — chosen to avoid locking/serializing the hot poll path, **not** for write economy. The anti-join is a **stateless "what hasn't this customer seen" query** (no per-consumer offset, no ack round-trip), **not** a dedup mechanism. `/inbox` is therefore **at-least-once**: under concurrent polls the same event may return twice, and consumers dedupe on event id (the standard webhook contract). This pull anti-join inbox is **one of the two defining features** separating this project from ST6 (ST6 is push-only: a `status` column and a `LEFT JOIN LATERAL` attempt-rollup, no per-recipient read concept).

4. **Why against the grain — fan-out-on-read, not ordering.** The win is *not* serve-then-mark vs mark-then-serve (identical write count — conceded). It's **fan-out-on-read**: an event is **one insert at ingest**; per-customer delivered markers are written **lazily, only for events a customer actually reads**. The alternative (fan-out-on-write / push-materialization) inserts one row per subscriber at ingest — N rows for N subscribers — plus per-delivery status updates, whether or not they ever consume. For M subscribers of whom k actually poll: ~`1 + k` writes vs `M inserts + M updates`. Lightest footprint wins. **Honest tradeoff:** this moves cost from write-time to *read*-time (every poll is an anti-join over growing sets), acceptable only because the read side is bounded — cold-start by the `created_at` floor (#14), steady-state by the watermark question (open). The read path is also stateless and lock-free — no per-consumer cursor, no ack round-trip.

5. **Finality.** The read is recorded when the `/inbox` call returns `200`. That 200 is the delivery event.

6. **Accepted razor: the transport-drop window (scoped, reconciled with #13).** The one loss case on the pull inbox is a response *sent but never received* — mark-read commits, the consumer never gets the body. Accepted for the demo; this is **not** a general at-most-once claim (the pull is at-least-once at the server boundary, #13). The prod closer is the explicit `ack`/`delete` endpoint. Not designing for it in v1.

7. **Endpoints.** `/inbox` (undelivered events) and `/last?num=x` (last x items).

8. **Multi-customer.** Infinite customers supported. Partition later (by customer). The DB-backed design keeps partitioning open as a future move.

9. **Delivered set is per-customer.** Each customer has its own unread set. The anti-join is keyed on `customer_id` — an event is delivered/read per customer, not globally. That's the whole reason there's a customer key alongside the event ID.

10. **Customer identification = per-customer API key (resolved, attack #3).** The caller sends a per-customer key/secret; the server **derives `customer_id` from the key** (indexed middleware lookup) and ignores any `customer_id` in the request — no id to spoof, cross-tenant reads structurally impossible, and the demo actually *shows* isolation (#9). **Kept frictionless:** seed 2–3 demo customers with fixed known keys at startup (**published in the README quickstart** so a reviewer can run it immediately, no signup) + ship an example client / curl snippets / Makefile targets (`make inbox-a`) that carry the key, so demoing is one command, not header-typing — and that example client is itself a graded DX deliverable per the brief. **Prod-auth roadmap is written up, not built:** OAuth / MFA, key-secret rotation, TLS, rate-limiting — a short "how this hardens in prod" section. Minimal demo security now, documented path to much more.

11. **Two directions, two writes — don't conflate.** (a) Ingest `POST`: the event is persisted durably *immediately*, before any delivery (decision #1). (b) Endpoint push delivery (stretch): retry with backoff and persist the *delivered marker* only on a `200`. The event is always written first; only the delivered/read marker is retry-gated. ("Retry before writing to the DB" refers to the marker write, not the event write.)

12. **Three surfaces — logically separate, jointly coherent.**
    - `POST /events` (ingest): an append-only **event stream**, not a pure mutable list.
    - `/inbox`: **pure new** — the per-customer anti-join over undelivered events; records the read on the `200`.
    - `/last?num=x`: **pure read** — a read-only peek at the last x items; does *not* record a read or touch delivery state.

    They're semantically distinct but coherent within the one application. The **event-stream framing (vs ST6's pure list)** is the *second* of the two defining differences from ST6 — the anti-join inbox (decision #3) is the first.

13. **Delivery guarantees are per-surface — two mechanisms, not one.**
    - **Pull `/inbox`:** at-least-once **at the server boundary**, **lock-free by design** (#17) — concurrent reads may return the same event; we accept the dupe rather than lock (consumer idempotent on event id), which *is* the at-least-once philosophy. Read is marked on the response (mark-on-read, no deferred-ack machinery). The one residual gap — response *sent but not received* (transport drop / consumer OOM on receipt) — is the accepted razor (#6). We do **not** claim strict *end-to-end* at-least-once on the pull; the explicit `ack`/`delete` endpoint is the documented prod closer. **Compliance:** delete-on-consume already satisfies the brief's "acknowledgment or deletion flow" — implicit ack chosen deliberately for latency/simplicity.
    - **Push subscription (stretch):** at-least-once — POST to the consumer URL, **await their `200`**, retry-with-backoff until confirmed. Their `200` = delivered; their internal processing failures are theirs, not ours.

    Separate paths, separate delivery stories, by design — do not conflate them. The anti-join is a stateless unseen-set query, not a dedup — dupes are the accepted currency, not a bug.

14. **New-subscriber floor (`created_at` watermark).** A customer's `/inbox` anti-join is filtered `event.created_at > subscription.created_at` — a new subscriber starts from their subscription point, not the beginning of the stream. Kills the cold-start "bombarded with all history" blowup and its marker-write storm. Pivot on *subscription* created_at (not account created_at), so subscribing to a new event_type later starts from that subscription. Cold-start floor only — steady-state growth of the delivered set is separate (open holes).

15. **Scaling = partition by customer (elaborates #8).** Shard across multiple DBs keyed on `customer_id`, with a routing API layer over the shards. The per-customer anti-join never crosses a shard, so it composes cleanly. This scales the **customer-count** axis. It does **not** bound a single long-lived customer's history growth — that is a separate, *temporal* axis handled by retention (#16). Don't conflate the two.

16. **Steady-state read bound = retention / archival window.** Delivered markers and cold events are archived off the hot DB on a cadence (daily/weekly/monthly — an ops tuning knob, out of scope to pin). The `/inbox` anti-join then scans only the hot window; with the `created_at` floor (#14) and sharding (#15), steady-state read cost is bounded. **State plainly:** archiving *undelivered* events is an **event-age TTL** — `/inbox` has a max event age and events older than retention `R` expire from the inbox. That is an accepted, *stated* expiry, not a silent drop.

17. **Lock-free hot path — latency over strict consistency.** We reject stop-the-world / row-locking on the inbox path; sequential locks destroy latency and responsiveness under load. Concurrent `/inbox` calls are **not** serialized — we accept that two readers may get the same event and rely on idempotent consumers (event id). No lock is needed to make concurrent polls "correct" because returning an event twice is *acceptable* (= at-least-once, #13), not a bug. This is the explicit answer to "what isolation/locking serializes concurrent `/inbox` calls?" — **none, by design.**

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
