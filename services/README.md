# The platform

A small food-delivery backend that every stage talks to over HTTP. It's real FastAPI, and everything in it is made up.

```bash
make platform          # http://127.0.0.1:8700/docs for the interactive API
```

| Endpoint | What it does | Why it's here |
|---|---|---|
| `GET /orders/{id}` | Order with items, total and `minutes_late` | Lateness is a fact computed by code, not a model judgment |
| `GET /policy` | Refund policy clauses §1–§6 | What the model must cite |
| `GET /fraud/{customer}` | Fraud score and signals | The third independent lookup |
| `POST /approvals`, `POST /approvals/{id}/decision` | An operations manager approves or rejects | A person in the process |
| `POST /payments` | Moves money; honours an `Idempotency-Key` header | Side effects that must not happen twice |
| `POST /notifications` | Message to the customer | |
| `POST /admin/faults` | `{"fraud": 1}`: the next fraud call hangs | Failure on demand for the demos |
| `POST /admin/reset` | Clears payments, approvals, faults | |

Each read takes `PLATFORM_LATENCY` seconds (default 2), like real downstream services. Writes go to SQLite in `var/platform.db`, so they outlive the agent: if the agent crashes after paying, the money has still moved.

The refund policy:

| Clause | Rule |
|---|---|
| §1 | More than 45 minutes late: full refund of the order total |
| §2 | Missing or spoiled items: refund those items only |
| §3 | Refunds over $500 need an operations manager's approval first |
| §4 | Fraud score above 0.7: refuse and escalate |
| §5 | Cancelled after the restaurant started cooking: no refund, $5 credit |
| §6 | 45 minutes late or less, nothing missing: apologise, $5 credit |
