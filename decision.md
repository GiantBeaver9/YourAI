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

## Open (not yet decided — do not invent)

- **`/last` and delivery state:** does `/last` record a read, or is it a read-only peek?
- **Contrast vs ST6:** what specifically the "finality" change is relative to the prior project.
