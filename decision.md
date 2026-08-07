# TriggersAPI — Decisions

Record of decisions made. Author-driven. No design added beyond what's decided.

## Decided

1. **Storage.** Events are stored in the DB immediately on `POST`. Durable — not held in memory, not lost.

2. **Ingress idempotency.** Enforced by a DB constraint on the way in. Idempotency is only a concern on ingest; not on delivery.

3. **Delivery = against the grain (anti-join).** `/inbox` returns events that have not yet been delivered — a `LEFT JOIN` against the delivered/read set, returning the rows with no match. We do **not** mark-then-serve. We serve, then record the read.

4. **Why against the grain.** Marking items as read first means too many DB reads/writes (write amplification). Read-heavy anti-join is the deliberate choice to keep the write load down. This is the core thesis of the design.

5. **Finality.** The read is recorded when the `/inbox` call returns `200`. That 200 is the delivery event.

6. **Out of scope: the after-200 window.** If we return 200 and the consumer dies immediately after, that is not the API's problem. At-most-once on that razor is accepted. Not designing for it.

7. **Endpoints.** `/inbox` (undelivered events) and `/last?num=x` (last x items).

8. **Multi-customer.** Infinite customers supported. Partition later (by customer). The DB-backed design keeps partitioning open as a future move.

9. **Delivered set is per-customer.** Each customer has its own unread set. The anti-join is keyed on `customer_id` — an event is delivered/read per customer, not globally. That's the whole reason there's a customer key alongside the event ID.

10. **Customer identification.** `POST`/`GET` on inbox requires a `customer_id` or password to identify the customer. **Preferred for production:** an auth token that identifies the customer instead. That's out of scope now — recorded as the production direction, not built in v1.

## Stretch options (brief's advanced list — "explore 1 or 2")

1. **Subscriptions & filtering — approach per ST6.** Push by event type with matching. Filtering via a cache controlled by the customers, who choose how and where to receive items. Workers iterate over a list of queues guarded by mutexes to stay fair and avoid unwarranted double-sends. (Ref: ST6 project.)

2. **Push delivery — approach per ST6.** Same mechanism as #1: deliver to registered consumer URLs with retry and backoff. (Ref: ST6.)

3. **Delivery guarantees — at-least-once (per ST6).** Backoff and await a `200`; retry *before* writing into the DB. Exactly-once is explicitly not wanted in this situation. Rationale as stated: at-least-once will cause frustration if the end user's service ever goes down, so backoff + retry prior to the DB write is the correct course. (Ref: ST6.)

4. **Shared inbox / multi-consumer — decided.** We write per customer (see decision #9); otherwise one customer ruins the read for everyone.

5. **Monitoring & observability — approach per ST6.** Event counts, latencies, retries, delivery success rates. (Ref: ST6.)

6. **DX enhancements (CLI / dashboard) — out of scope.** New; not worth getting into. Brief says explore 1–2, and several above are already covered.

7. **Explorer UI — out of scope.** Not worth the time.

## Open (not yet decided — do not invent)

- **`/last` and delivery state:** does `/last` record a read, or is it a read-only peek?
- **Contrast vs ST6:** what specifically the "finality" change is relative to the prior project.
