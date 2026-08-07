# TriggersAPI

A public HTTP ingress for events plus a **pull inbox** for consumers. A system `POST`s
events; a customer's workflow/agent pulls its undelivered events from `/inbox` to wake up
and react. The *reacting* half of an automation platform.

- **Design & rationale:** [`docs/PRD.md`](docs/PRD.md)
- **Decision log** (survived two adversarial review passes): [`decision.md`](decision.md)

## The design in 60 seconds

- **Producer = consumer, per customer.** A customer POSTs to its own stream and reads it back.
- **`/inbox` is an anti-join**, not a queue drain: it returns the events this customer hasn't read yet (`events LEFT JOIN delivered` where no marker), marks them read on the `200`, and reports `unread_remaining`.
- **At-least-once, lock-free.** Concurrent polls aren't serialized; duplicates are the accepted currency and consumers dedupe on the producer-supplied `event_id`. No stop-the-world locking on the hot path.
- **Idempotent ingest.** `(customer_id, event_id)` is the primary key — a replayed `POST` is a no-op. The events table *is* the dedup structure.
- **Per-customer isolation by credential.** The API key derives `customer_id`; there is no id to spoof.
- **Never stale.** Events + markers archive at 30 days; the inbox floor trails at 31 days, so it only ever retires already-archived events.

## Quickstart

```bash
docker compose up          # API on :8000, Postgres seeded with demo customers
```

The seed publishes two demo customers with fixed keys (no signup needed):

| Customer | API key |
|---|---|
| `cust_a` | `demo-key-a` |
| `cust_b` | `demo-key-b` |

Ingest an event, then pull it back:

```bash
# produce
curl -sX POST localhost:8000/v1/events \
  -H "Authorization: Bearer demo-key-a" \
  -H "Content-Type: application/json" \
  -d '{"event_id":"evt-1","event_type":"order.created","payload":{"order_id":9931}}'
# -> {"event_id":"evt-1","status":"stored","deduped":false}

# consume (marks it read, reports remaining)
curl -s localhost:8000/v1/inbox -H "Authorization: Bearer demo-key-a"
# -> {"events":[{"event_id":"evt-1",...}],"count":1,"unread_remaining":0}

# poll again -> already read, empty
curl -s localhost:8000/v1/inbox -H "Authorization: Bearer demo-key-a"
# -> {"events":[],"count":0,"unread_remaining":0}
```

Or use the Makefile targets: `make produce-a`, `make inbox-a`, `make inbox-b`.

**Isolation demo** — B's key can't see A's events:

```bash
curl -s localhost:8000/v1/inbox -H "Authorization: Bearer demo-key-b"   # -> {"events":[],...}
```

## API

Base path `/v1`. Auth: `Authorization: Bearer <api_key>`.

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/v1/events` | Ingest an event (idempotent on `event_id`). `201` new / `200` deduped. |
| `GET`  | `/v1/inbox?limit=200` | Undelivered events (≤200), marks them read, returns `unread_remaining`. |
| `GET`  | `/v1/last?num=50` | Most-recent N events, read-only peek (does **not** mark read). |
| `GET`  | `/healthz` | Liveness. |

Full request/response shapes and the SQL behind each route: [`docs/PRD.md`](docs/PRD.md) §5.

## Layout

```
app/
  main.py            # FastAPI app + routes
  auth.py            # Bearer key -> customer_id
  store.py           # EventStore interface (in-memory + Postgres impls)
  models.py          # Pydantic request/response models
  retention.py       # 30d archive / 31d floor
migrations/          # schema (customers, events, delivered)
client/example.py    # produce -> poll -> loop, using a demo key
tests/               # pytest: idempotency, anti-join, isolation, retention, dupes
docker-compose.yml   # api + seeded postgres
Makefile             # produce-a / inbox-a / test targets
```

## Tests

```bash
make test        # or: pytest
```

Covers: idempotent replay, anti-join correctness, at-least-once (dupes ok, no loss),
cross-tenant isolation, retention floor never skipping a live event, `/last` neutrality.

## Production notes (documented, not built in v1)

Deliberately scoped out and written up rather than implemented — see `decision.md` for the
full rationale on each: OAuth/MFA + key rotation + TLS + rate-limiting (D10, D19); an
explicit consumer `ack`/`delete` endpoint (v1 uses implicit ack on read, D13); push
delivery + subscriptions/fan-out + a metrics subsystem (stretch, S1–S5); `/timeframe`
date-range reads (D12); customer sharding for horizontal scale (D15).
