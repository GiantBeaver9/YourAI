# TriggersAPI — Decisions

Record of decisions made. Author-driven. No design added beyond what's decided.

## Decided

1. **Storage.** Events are stored in the DB immediately on `POST`. Durable — not held in memory, not lost.

2. **Ingress idempotency = composite `(customer_id, event_id)` unique key (resolved, attack #6).** `customer_id` is derived from the API key (#10); `event_id` is **producer-supplied and required**. The composite is the unique constraint on the events table, so a repeat insert of the same `(customer_id, event_id)` fails and is deduped (return the existing event). Load-bearing points:
    - **The events table itself is the dedup structure** — no separate dedup set to grow or prune (kills the unbounded-set concern). The event's own key is the guard.
    - **Producer owns the sameness signal:** same `event_id` = "the same event" (swallowed); a genuinely new occurrence uses a new `event_id`. So legitimate duplicates are never accidentally merged.
    - **Bounded by retention `R` (#16):** dedup holds while the event is in the hot table; a duplicate arriving after archival (R = days/weeks, retries = seconds → theoretical only) would re-insert.
    - The same producer `event_id` does double duty: ingest dedup key *and* the id consumers dedupe on for delivery dupes (#13). Idempotency is an ingest-only concern.

3. **Delivery = against the grain (anti-join).** `/inbox` returns events not yet delivered to the customer — a `LEFT JOIN` against the delivered/read set, returning the rows with no match. We serve, then record the read (not mark-then-serve) — chosen to avoid locking/serializing the hot poll path, **not** for write economy. The anti-join is a **stateless "what hasn't this customer seen" query** (no per-consumer offset, no ack round-trip), **not** a dedup mechanism. `/inbox` is therefore **at-least-once**: under concurrent polls the same event may return twice, and consumers dedupe on event id (the standard webhook contract). This pull anti-join inbox is **one of the two defining features** separating this project from ST6 (ST6 is push-only: a `status` column and a `LEFT JOIN LATERAL` attempt-rollup, no per-recipient read concept).

4. **Why against the grain — fan-out-on-read, not ordering.** The win is *not* serve-then-mark vs mark-then-serve (identical write count — conceded). It's **fan-out-on-read**: an event is **one insert at ingest**; per-customer delivered markers are written **lazily, only for events a customer actually reads**. The alternative (fan-out-on-write / push-materialization) inserts one row per subscriber at ingest — N rows for N subscribers — plus per-delivery status updates, whether or not they ever consume. For M subscribers of whom k actually poll: ~`1 + k` writes vs `M inserts + M updates`. Lightest footprint wins. **Honest tradeoff:** this moves cost from write-time to *read*-time (every poll is an anti-join over growing sets), acceptable only because the read side is bounded — cold-start by the `created_at` floor (#14), steady-state by the watermark question (open). The read path is also stateless and lock-free — no per-consumer cursor, no ack round-trip.

5. **Finality.** The read is recorded when the `/inbox` call returns `200`. That 200 is the delivery event.

6. **Accepted razor: the transport-drop window (scoped, reconciled with #13).** The one loss case on the pull inbox is a response *sent but never received* — mark-read commits, the consumer never gets the body. Accepted for the demo; this is **not** a general at-most-once claim (the pull is at-least-once at the server boundary, #13). **Recoverable in v1:** a consumer that loses an `/inbox` response can re-read the batch via `/last` (delivery-neutral peek, #12) within the last-N window — so the razor is *recoverable*, not silent loss. The explicit `ack`/`delete` endpoint is the stricter prod closer. Not designing further for it in v1.

7. **Endpoints.** `/inbox` (undelivered events) and `/last?num=x` (last x items).

8. **Multi-customer.** Infinite customers supported. Partition later (by customer). The DB-backed design keeps partitioning open as a future move.

9. **Delivered set is per-customer.** Each customer has its own unread set. The anti-join is keyed on `customer_id` — an event is delivered/read per customer, not globally. That's the whole reason there's a customer key alongside the event ID.

10. **Customer identification = per-customer API key (resolved, attack #3).** The caller sends a per-customer key/secret; the server **derives `customer_id` from the key** (indexed middleware lookup) and ignores any `customer_id` in the request — no id to spoof, cross-tenant reads structurally impossible, and the demo actually *shows* isolation (#9). **Kept frictionless:** seed 2–3 demo customers with fixed known keys at startup (**published in the README quickstart** so a reviewer can run it immediately, no signup) + ship an example client / curl snippets / Makefile targets (`make inbox-a`) that carry the key, so demoing is one command, not header-typing — and that example client is itself a graded DX deliverable per the brief. **Prod-auth roadmap is written up, not built:** OAuth / MFA, key-secret rotation, TLS, rate-limiting — a short "how this hardens in prod" section. Minimal demo security now, documented path to much more.

11. **Two directions, two writes — don't conflate.** (a) Ingest `POST`: the event is persisted durably *immediately*, before any delivery (decision #1). (b) Endpoint push delivery (stretch): retry with backoff and persist the *delivered marker* only on a `200`. The event is always written first; only the delivered/read marker is retry-gated. ("Retry before writing to the DB" refers to the marker write, not the event write.)

12. **Three surfaces — logically separate, jointly coherent.**
    - `POST /events` (ingest): an append-only **event stream**, not a pure mutable list.
    - `/inbox`: **pure new** — the per-customer anti-join over undelivered events; records the read on the `200`.
    - `/last?num=x`: **pure read / recovery peek** — read-only, does *not* record a read or touch delivery state. Purpose: let a customer see the last x events **regardless of delivered status** — a circumvention/recovery path for retrieval failures (e.g. they pulled `/inbox` and lost or immediately closed the response). *Not* a delivery channel; it's the safety net that makes the #6 transport-drop razor **recoverable** (within the last-N window; consumer dedupes on event id), not silent loss. Showing already-consumed events is the feature, not a bug (resolves attack #8). **Future extension (documented, not built):** `/timeframe` — pull events between two timestamps (also capped at 200) for targeted recovery beyond the last-N window.

    They're semantically distinct but coherent within the one application. The **event-stream framing (vs ST6's pure list)** is the *second* of the two defining differences from ST6 — the anti-join inbox (decision #3) is the first.

13. **Delivery guarantees are per-surface — two mechanisms, not one.**
    - **Pull `/inbox`:** at-least-once **at the server boundary**, **lock-free by design** (#17) — concurrent reads may return the same event; we accept the dupe rather than lock (consumer idempotent on event id), which *is* the at-least-once philosophy. Read is marked on the response (mark-on-read, no deferred-ack machinery). The one residual gap — response *sent but not received* (transport drop / consumer OOM on receipt) — is the accepted razor (#6). We do **not** claim strict *end-to-end* at-least-once on the pull; the explicit `ack`/`delete` endpoint is the documented prod closer. **Compliance:** delete-on-consume already satisfies the brief's "acknowledgment or deletion flow" — implicit ack chosen deliberately for latency/simplicity.
    - **Push subscription (stretch):** at-least-once — POST to the consumer URL, **await their `200`**, retry-with-backoff until confirmed. Their `200` = delivered; their internal processing failures are theirs, not ours.

    Separate paths, separate delivery stories, by design — do not conflate them. The anti-join is a stateless unseen-set query, not a dedup — dupes are the accepted currency, not a bug.

14. **New-subscriber floor (`created_at` watermark).** A customer's `/inbox` anti-join is filtered `event.created_at > subscription.created_at` — a new subscriber starts from their subscription point, not the beginning of the stream. Kills the cold-start "bombarded with all history" blowup and its marker-write storm. Pivot on *subscription* created_at (not account created_at), so subscribing to a new event_type later starts from that subscription. Cold-start floor only — steady-state growth of the delivered set is separate (open holes).

15. **Scaling = partition by customer (elaborates #8).** Shard across multiple DBs keyed on `customer_id`, with a routing API layer over the shards. The per-customer anti-join never crosses a shard, so it composes cleanly. This scales the **customer-count** axis. It does **not** bound a single long-lived customer's history growth — that is a separate, *temporal* axis handled by retention (#16). Don't conflate the two.

16. **Steady-state read bound = retention / archival window.** Delivered markers and cold events are archived off the hot DB on a cadence (daily/weekly/monthly — an ops tuning knob, out of scope to pin). The `/inbox` anti-join then scans only the hot window; with the `created_at` floor (#14) and sharding (#15), steady-state read cost is bounded. **State plainly:** archiving *undelivered* events is an **event-age TTL** — `/inbox` has a max event age and events older than retention `R` expire from the inbox. That is an accepted, *stated* expiry, not a silent drop.

17. **Lock-free hot path — latency over strict consistency.** We reject stop-the-world / row-locking on the inbox path; sequential locks destroy latency and responsiveness under load. Concurrent `/inbox` calls are **not** serialized — we accept that two readers may get the same event and rely on idempotent consumers (event id). No lock is needed to make concurrent polls "correct" because returning an event twice is *acceptable* (= at-least-once, #13), not a bug. This is the explicit answer to "what isolation/locking serializes concurrent `/inbox` calls?" — **none, by design.**

18. **Delivered set is shared per customer, path-agnostic; push-vs-pull is a per-customer mode (resolved, attack #7).** One `(customer, event)` delivered fact — not per-path ledgers. Push-vs-pull is a **per-customer mode** (a customer operates as a poller *or* a subscriber, not both at once), so "delivered to C" is one fact served by whichever mode C is in. The both-modes-at-once case is not a normal operating state; if it ever occurred it degrades to the same accepted dupe (#13/#17) — no new failure mode. Per-path delivery tracking (separate send/read ledgers) is a **deferred, extensible feature** — a secondary table checked later, small lift, not built without a driving use case (YAGNI).

19. **Operational posture (resolved, attack #9).**
    - **Failing endpoint = circuit breaker, not per-event DLQ (push).** Retry on capped backoff up to ~2h (a longer window than ST6's 5-attempt default, stretch #2), then **temporarily disable the endpoint** and let events **queue as undelivered** until it recovers / is re-enabled (health probe or customer action). The undelivered queue + disabled endpoint *is* the DLQ. Honest boundary: the queue is bounded by retention `R` (#16), so an outage longer than `R` expires those events (the stated TTL) — say it, don't hide it.
    - **No head-of-line blocking on the pull core.** `/inbox` is a set/query, not a FIFO — an unprocessable event stays undelivered for that one customer and blocks no one; it TTLs out. DLQ/head-of-line is a push-only concern.
    - **Response item cap = 200.** `/inbox`, `/last`, and the future `/timeframe` return at most 200 events per call (pagination bound). Separately, **ingest body size**: no *tuned* cap (defer until real event size/shape is known — an array of ~200 events is trivial), but a loose **sanity ceiling** on the POST body + `413` is documented as an abuse/accident guardrail (the "2 GB body" case) — a safety limit, not a product limit.
    - **Rate limit / backpressure (`429`).** Documented as prod, not built in v1.
    - **Crash recovery.** Pull core is DB-durable and stateless → restart is a non-event; push re-enqueues pending events on startup (ST6 `LoadPendingEvents` pattern).
    - These were **considered and scoped**, not missed (answers the "Open: None" signal).

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
