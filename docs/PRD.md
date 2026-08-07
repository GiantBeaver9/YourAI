# TriggersAPI — PRD & Technical Design

> Build spec **and** defense doc. Every load-bearing choice traces to `../decision.md`
> — the decision log that survived two adversarial review passes. Refs like **(D4)**
> point to that file's numbered decisions; **(S2)** points to its stretch options.

## 1. Summary

TriggersAPI is a public HTTP ingress for events plus a pull inbox for consumers.
A system POSTs events; the customer's workflow/agent pulls its undelivered events from
`/inbox` to "wake up" and react. It is the *reacting* half of an automation platform.

**Core model: producer = consumer, per customer (D4).** A customer POSTs events into its
own stream and reads them back from its own inbox. Events are an **append-only stream**
(D12); `/inbox` is an **anti-join** returning what that customer hasn't yet read (D3).

## 2. Goals / Non-goals

**Goals**
- Durable ingestion with idempotency (D1, D2).
- A pull inbox: undelivered events, **at-least-once**, **lock-free** (D3, D13, D17).
- Per-customer isolation enforced by **credential, not trust** (D10).
- Bounded storage and **never-stale** reads via retention (D16).
- Clean DX: predictable routes/responses, a seeded demo, an example client (D10) — the graded axis per the brief ("clean code first, well documented").

**Non-goals in v1 (documented as the prod path, not built)**
- OAuth / MFA / key rotation / TLS / rate-limiting (D10, D19).
- Explicit consumer `ack`/`delete` endpoint — v1 uses **implicit ack on read** (D13).
- Push delivery, subscriptions/fan-out, a metrics subsystem — stretch (S1–S5).
- `/timeframe` date-range read — documented extension (D12).

## 3. Core model & the one non-obvious choice

Producer and consumer are the same customer, so per event there is exactly **one reader**
(M = 1). Given that, why an anti-join with a separate `delivered` table instead of a
`delivered` boolean column on the event? (D4)

1. **Events stay immutable / append-only** (D12) — a status column would mutate the event row; markers keep the stream append-only.
2. **One read model from 1 to N consumers** — the multi-consumer/subscription stretch (S1) drops in with **zero schema change**; a status column would have to be rebuilt.

The fan-out-on-read *write-economy* argument (`~1+k` writes vs `M inserts + M updates`)
applies only to the **subscription path** (M > 1) — it is **not** claimed for the M = 1
core (D4). Stating that correctly is the difference between a defensible design and an
overclaim a reviewer punctures.

## 4. Data model (Postgres)

```sql
-- identity: the key IS the customer's credential; customer_id is derived from it (D10)
CREATE TABLE customers (
  customer_id   TEXT PRIMARY KEY,
  api_key       TEXT UNIQUE NOT NULL,      -- demo: seeded, published in README. prod: hashed + rotated
  subscribed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- the event stream. composite key = idempotency (D2). event_id is producer-supplied + required.
CREATE TABLE events (
  customer_id TEXT NOT NULL REFERENCES customers,
  event_id    TEXT NOT NULL,               -- producer-supplied idempotency key
  event_type  TEXT,
  payload     JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (customer_id, event_id)
);
CREATE INDEX ON events (customer_id, created_at);   -- drives the anti-join + floor scan

-- delivery markers. lazy, one per (customer, event) actually read (D3, D4).
CREATE TABLE delivered (
  customer_id  TEXT NOT NULL,
  event_id     TEXT NOT NULL,
  delivered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (customer_id, event_id)
);
```

The `events` table is itself the ingest dedup structure — no separate dedup set to grow
or prune (D2). Markers are written lazily on read, not fanned out at ingest (D4).

## 5. API

Base path `/v1`. JSON only. Auth: `Authorization: Bearer <api_key>` → server derives
`customer_id` (D10); any `customer_id` in the request is ignored.

| Method | Route | Purpose | Success |
|---|---|---|---|
| `POST` | `/v1/events` | Ingest an event (idempotent) | `201` new / `200` deduped |
| `GET`  | `/v1/inbox?limit=` | Undelivered events, marks them read | `200` |
| `GET`  | `/v1/last?num=` | Most-recent N events (read-only peek) | `200` |
| `GET`  | `/healthz` | Liveness | `200` |

**`POST /v1/events`** — body `{ "event_id": "evt-9931", "event_type": "order.created", "payload": {...} }`.
`event_id` is required (idempotency key). Ingest:
```sql
INSERT INTO events (customer_id, event_id, event_type, payload)
VALUES (:cid, :eid, :etype, :payload)
ON CONFLICT (customer_id, event_id) DO NOTHING;   -- duplicate -> 200 deduped, event untouched
```
Response `201`: `{ "event_id": "evt-9931", "status": "stored", "deduped": false }`.

**`GET /v1/inbox?limit=200`** — the anti-join, capped at 200 (D19), floor per D16:
```sql
SELECT e.event_id, e.event_type, e.payload, e.created_at
FROM events e
LEFT JOIN delivered d USING (customer_id, event_id)
WHERE e.customer_id = :cid
  AND d.event_id IS NULL
  AND e.created_at > GREATEST(:subscribed_at, now() - interval '31 days')
ORDER BY e.created_at
LIMIT 200;
-- then: INSERT the returned (customer_id, event_id) into delivered ON CONFLICT DO NOTHING  (implicit ack, D13)
-- then: compute unread_remaining = count of still-undelivered rows
```
Response `200`: `{ "events": [...], "count": 12, "unread_remaining": 0 }`.
The `unread_remaining` count is the caught-up signal (D6, D12). The read is recorded when
we return the 200 — that is the delivery (D5). We send the 200; we do not control whether
the network delivers it (the accepted transport-drop razor, D6). Consumers dedupe on
`event_id` (at-least-once, D13).

**`GET /v1/last?num=50`** — most-recent N events **regardless of delivered status**, a
read-only debug peek that does not touch delivery state (D12). Cap 200.

## 6. Delivery semantics (D13)

- **Pull `/inbox`: at-least-once, lock-free.** Concurrent polls are not serialized (D17); two readers may get the same event; consumers are idempotent on `event_id`. The read is an **implicit ack** on the `200` — which *is* the brief's "acknowledgment or deletion flow" (the event leaves the inbox on consume). An explicit `ack`/`delete` is the documented prod upgrade.
- **The one accepted gap:** a `200` we send but the network drops. We own what we control (sending the response); the internet is not ours to guarantee (D6). Not a general at-most-once claim.
- **Idempotency (D2):** composite `(customer_id, event_id)`; producer owns the sameness signal (same `event_id` = same event). Holds within retention; a replay older than 30 days is treated as new (D2).
- **Push (stretch, S3): at-least-once** — POST to the consumer URL, await *their* `200`, retry with backoff; their `200` = delivered.

## 7. Retention & "never stale" (D16)

- Events **and their markers** are archived out of the hot DB at **age 30 days**.
- The `/inbox` floor trails at **31 days**: effective floor = `max(subscribed_at, now − 31d)`.
- Because the floor sits a day *behind* archival, advancing it only ever retires
  already-archived events — **no resurrection** (event + marker retire together) and **no
  premature loss** (the floor never skips a live event). Net: **never a stale event.**
- An undelivered event older than 30 days expires (a stated TTL, surfaced via
  `unread_remaining`), not a silent drop.

## 8. Isolation & auth (D10)

Per-customer API key → server derives `customer_id`; there is no `customer_id` parameter to
spoof, so cross-tenant reads are structurally impossible. Demo seeds 2–3 customers with
fixed keys **published in the README** so a reviewer runs it in one command. Prod roadmap
(written, not built): OAuth/MFA, key rotation, TLS, rate-limiting.

## 9. Scaling (D15)

Shard by `customer_id` across DBs behind a routing API layer; the per-customer anti-join
never crosses a shard. Scales the customer-count axis; the temporal axis is bounded by
retention (§7). Sharding ≠ the retention bound — two different axes.

## 10. Stretch (only if the core is clean, S1–S5)

- **Subscriptions & filtering (S1):** one-hot `(customer, event_type) → URL` matrix behind a read-through cache; late/delivery-time filtering. This is where fan-out (M > 1) and the write-economy argument actually live.
- **Push delivery (S2/S3):** per-customer worker queues, exponential backoff (3s→10s→30s→1m→2m then flat 2-min), circuit-breaker disables a flapping endpoint after ~2h; events queue as undelivered (bounded by retention). A dead-endpoint push customer can fall back to pulling `/inbox` (D18).
- **Observability (S5):** a documented thought — push has a per-attempt log; pull backlog/expiry are derivable from `events` + `delivered`. No metrics subsystem built.

## 11. Testing (the correctness proof — doubles as documentation)

- ingest happy path + idempotent replay returns `deduped: true`, same event untouched
- `/inbox` returns undelivered, marks them read; a second poll doesn't re-return (single reader)
- concurrent polls may dupe (accepted) — never lose (at-least-once)
- `unread_remaining` reflects backlog after a capped 200 read
- cross-tenant: customer A's key cannot read customer B's inbox
- retention: event older than 30 days is gone; floor math never skips a live event
- `/last` returns most-recent N regardless of delivered status, records nothing

## 12. Tech stack

Python + **FastAPI** + **Postgres** (brief suggests Python; AWS-preferred deploy is a
`docker run` + documented ECS/RDS path). Storage sits behind a thin interface so the
in-memory dev backend and Postgres share one shape. *(Assumption — flip to another stack
if preferred; nothing above is language-specific except the SQL.)*
