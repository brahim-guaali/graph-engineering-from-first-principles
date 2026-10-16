"""The food-delivery platform our agent works against: a real HTTP API.

    make platform        # or: python -m uvicorn services.app:app --port 8700

Read endpoints are slow on purpose (2 s each, like real downstream services), so you can
see what running them one after another costs. Payments, approvals and notifications are
stored in SQLite, so they outlive the agent: if the agent crashes after paying, the money
has still moved.

Faults are injected over HTTP, so a demo can make the fraud service hang the next time
it is called:  POST /admin/faults {"fraud": 1}
"""
import asyncio
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from services import data

LATENCY = float(os.environ.get("PLATFORM_LATENCY", "2.0"))   # seconds per read call
HANG = 30                                                    # longer than any client timeout
DB_PATH = Path(os.environ.get("PLATFORM_DB", Path(__file__).resolve().parents[1] / "var" / "platform.db"))

app = FastAPI(title="Food-delivery platform (demo)")
_lock = threading.Lock()
_faults: dict[str, int] = {}


@contextmanager
def db():
    """A connection that commits on success and is always closed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        create table if not exists payments (
            id text primary key, order_id text, amount real, idempotency_key text unique, created_at text);
        create table if not exists approvals (
            id text primary key, run_id text, order_id text, amount real, reason text,
            status text, decided_by text, created_at text, decided_at text);
        create table if not exists notifications (
            id text primary key, customer text, text text, created_at text);
    """)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _slow(service):
    """Every read takes LATENCY seconds; an injected fault makes it hang instead."""
    if _faults.get(service, 0) > 0:
        _faults[service] -= 1
        await asyncio.sleep(HANG)
    await asyncio.sleep(LATENCY)


# --- reads ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "latency": LATENCY}


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    await _slow("orders")
    if order_id not in data.ORDERS:
        raise HTTPException(404, f"no order {order_id}")
    return data.ORDERS[order_id]


@app.get("/policy")
async def get_policy():
    await _slow("policy")
    return {"version": data.POLICY_VERSION, "clauses": data.POLICY}


@app.get("/fraud/{customer}")
async def get_fraud(customer: str):
    await _slow("fraud")
    return {"customer": customer, **data.FRAUD.get(customer, data.DEFAULT_FRAUD)}


# --- writes: approvals (a person), payments (money), notifications ---------------------
class ApprovalRequest(BaseModel):
    run_id: str
    order_id: str
    amount: float
    reason: str


class Decision(BaseModel):
    decision: str            # approved | rejected
    by: str = "ops-manager"


@app.post("/approvals")
def request_approval(req: ApprovalRequest):
    aid = "apr_" + uuid.uuid4().hex[:8]
    with _lock, db() as conn:
        conn.execute("insert into approvals values (?,?,?,?,?,?,?,?,?)",
                     (aid, req.run_id, req.order_id, req.amount, req.reason, "pending", None, now(), None))
    return {"id": aid, "status": "pending"}


@app.get("/approvals")
def list_approvals(status: str = "pending"):
    with db() as conn:
        return [dict(r) for r in conn.execute("select * from approvals where status = ?", (status,))]


@app.get("/approvals/{aid}")
def get_approval(aid: str):
    with db() as conn:
        row = conn.execute("select * from approvals where id = ?", (aid,)).fetchone()
    if not row:
        raise HTTPException(404, f"no approval {aid}")
    return dict(row)


@app.post("/approvals/{aid}/decision")
def decide_approval(aid: str, d: Decision):
    if d.decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision must be approved or rejected")
    with _lock, db() as conn:
        conn.execute("update approvals set status = ?, decided_by = ?, decided_at = ? where id = ?",
                     (d.decision, d.by, now(), aid))
    return get_approval(aid)


class PaymentRequest(BaseModel):
    order_id: str
    amount: float


@app.post("/payments")
def create_payment(p: PaymentRequest, idempotency_key: str | None = Header(default=None)):
    """Moves money. With an Idempotency-Key header, the same key never moves it twice."""
    with _lock, db() as conn:
        if idempotency_key:
            row = conn.execute("select * from payments where idempotency_key = ?",
                               (idempotency_key,)).fetchone()
            if row:
                return {**dict(row), "replayed": True}
        pid = "pay_" + uuid.uuid4().hex[:8]
        conn.execute("insert into payments values (?,?,?,?,?)",
                     (pid, p.order_id, p.amount, idempotency_key, now()))
        row = conn.execute("select * from payments where id = ?", (pid,)).fetchone()
    return {**dict(row), "replayed": False}


@app.get("/payments")
def list_payments(order_id: str):
    with db() as conn:
        rows = [dict(r) for r in conn.execute("select * from payments where order_id = ?", (order_id,))]
    return {"order_id": order_id, "count": len(rows), "total": sum(r["amount"] for r in rows), "payments": rows}


class Notification(BaseModel):
    customer: str
    text: str


@app.post("/notifications")
def notify(n: Notification):
    nid = "msg_" + uuid.uuid4().hex[:8]
    with _lock, db() as conn:
        conn.execute("insert into notifications values (?,?,?,?)", (nid, n.customer, n.text, now()))
    return {"id": nid}


# --- admin: fault injection and reset, for the demos --------------------------------------
@app.post("/admin/faults")
def set_faults(faults: dict[str, int]):
    _faults.update(faults)
    return _faults


@app.post("/admin/reset")
def reset():
    _faults.clear()
    with _lock, db() as conn:
        conn.executescript("delete from payments; delete from approvals; delete from notifications;")
    return {"ok": True}


# --- live run viewer: runs post events here, the page at /ui streams them ------------------
_runs: dict[str, dict] = {}
_listeners: set[asyncio.Queue] = set()
UI = Path(__file__).resolve().parent / "ui" / "index.html"


@app.post("/events")
async def post_events(events: list[dict]):
    for e in events:
        run = _runs.setdefault(e["run"], {"id": e["run"], "events": []})
        if e["type"] == "run_started":
            run.update(stage=e["stage"], title=e["title"], status="running", started=now())
        elif e["type"] == "run_finished":
            run["status"] = e["status"]
        run["events"].append(e)
        for q in list(_listeners):
            q.put_nowait(e)
    return {"ok": True}


@app.get("/runs")
def list_runs():
    return [{k: v for k, v in r.items() if k != "events"} for r in reversed(_runs.values())]


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in _runs:
        raise HTTPException(404, f"no run {run_id}")
    return _runs[run_id]


@app.get("/events/stream")
async def stream_events():
    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        _listeners.add(q)
        try:
            yield ": connected\n\n"
            while True:
                try:
                    e = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _listeners.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/ui", response_class=HTMLResponse)
def ui():
    return UI.read_text()


@app.post("/admin/clear-runs")
def clear_runs():
    _runs.clear()
    return {"ok": True}
