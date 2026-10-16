# Stage 5: side effects

Checkpoints make replays normal: resume runs the interrupted round again. That's harmless for a lookup and dangerous for a payment. If the process dies inside `refund`, after the provider has moved the money but before the checkpoint, resume runs `refund` again from the top, and the system can't tell whether the first payment went out.

The standard defence comes from payment APIs. Each request carries an **idempotency key** built from something stable, here the run and the node (`t-605b79:refund`), and the provider refuses to act twice on the same key. Running the node again becomes harmless.

```python
def refund(s):
    key = f"{s['run_id']}:refund"            # the same on every retry of this node in this run
    return {"payment": platform.pay(order_id, amount, idempotency_key=key)}
```

The second change: because spending is a **field in the state**, an ordinary edge can stop the run when it crosses a limit. Here the run goes to a person instead of paying:

```python
def route_after_decision(s):
    if s["spent"] > s["budget"]:
        return "over_budget"
    ...
```

The engine is stage 4's, unchanged; [`refund.py`](refund.py) imports it. Only the nodes and one edge change.

## Run it

```bash
.venv/bin/python 05-side-effects/run.py           # with `make platform` running
```

## What happened on a real run (gemini-3.5-flash, 2026-10-16)

The case is a missing-dessert refund of $18. The process **really dies** (`os._exit(137)`) right after the payment API answers. A new process then resumes from the last checkpoint, whose next node is `refund`.

| | Payments for order A-8120 |
|---|---|
| **No key** | `pay_49ea7c91 $18 key=None`, `pay_6c7c44ab $18 key=None`: **paid twice, $36** |
| **Key = run + node** | `pay_358e3448 $18 key=t-605b79:refund`: **once**. The replay got the original payment back |
| **Budget $0.0002** | `decide` spent $0.0005, so the edge routed to `over_budget`. Escalated to a person; no money moved |

## Where it breaks

1. **The model's judgments are still unchecked.** If `decide` cites a clause that doesn't exist or refunds more than the policy allows, the graph pays it faithfully.
2. **No measurement yet.** We've argued that the graph is more reliable than the loop but haven't measured it.

**Next:** [stage 6](../06-reliability/) puts a verifier after `decide`, retries when it fails, and scores both designs on 12 cases.
