"""Score the loop (stage 1) and the graph (this stage) on the same 12 cases, with the real model.

For each case and design we reset the platform, run, then read what actually happened on the
platform: how much money moved for the order, and whether a manager was asked. That is the
outcome the business cares about, measured the same way for both designs.

    make platform                                   # in another terminal
    python 06-reliability/evaluate.py               # about 25 minutes on the free tier
    python 06-reliability/evaluate.py late-catering fraud    # or some cases

Results go to 06-reliability/results/ as JSON and a markdown table.
"""
import importlib.util
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

from agentkit.cases import CASES
from agentkit.llm import Gemini
from agentkit.ops import ManagerAtDesk
from agentkit.platform import Platform
from agentkit.trace import Tracer

sys.stdout.reconfigure(line_buffering=True)
ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def load(path, name):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


loop = load(ROOT / "01-agent-loop" / "agent.py", "loop_agent")
graph = load(HERE / "refund.py", "graph_refund")
Checkpointer = sys.modules["checkpoint"].Checkpointer


def outcome(platform, order_id):
    paid = platform.payments(order_id)["total"] if order_id else 0
    asked = [a for status in ("pending", "approved", "rejected")
             for a in platform._get("/approvals", status=status) if a["order_id"] == order_id]
    return {"paid": round(paid, 2), "asked_manager": bool(asked)}


def run_loop(case, platform):
    llm = Gemini()
    tracer = Tracer("6 · evaluation", f"loop: {case['id']}", loop.graph_spec(), usage=llm.usage)
    t0 = time.monotonic()
    with ManagerAtDesk(delay=1):
        _, _, trace = loop.agent_loop(case, llm, platform, approval_wait=30, log=lambda *_: None,
                                      tracer=tracer)
    tracer.finished("done")
    return {"seconds": time.monotonic() - t0, "usage": llm.usage.snapshot(),
            "path": [name for name, _ in trace]}


def run_graph(case, platform, cp):
    llm = Gemini()
    thread = f"eval-{case['id']}-{int(time.time())}"
    tracer = Tracer("6 · evaluation", f"graph: {case['id']}", usage=llm.usage, run_id=thread)
    g = graph.build(llm, platform, checkpointer=cp, tracer=tracer)
    tracer.start(g.g.spec())
    t0 = time.monotonic()
    result = g.invoke({"request": case, "run_id": thread}, thread=thread, log=lambda *_: None, tracer=tracer)
    if result["status"] == "interrupted":                  # the manager approves straight away
        platform.decide_approval(result["interrupt"]["approval_id"], "approved")
        result = g.resume(thread, "approved", log=lambda *_: None, tracer=tracer)
    tracer.finished("done")
    path = [c["next"] for c in cp.history(thread)]
    return {"seconds": time.monotonic() - t0, "usage": llm.usage.snapshot(),
            "path": [n for step in path for n in step], "attempts": result["state"]["attempts"]}


def score(case, got):
    e = case["expect"]
    want_paid = e["amount"] if e["action"] == "refund" else 0
    return abs(got["paid"] - want_paid) < 0.01 and got["asked_manager"] == e["approval"]


def main(ids):
    platform = Platform()
    platform.check()
    cp = Checkpointer(ROOT / "var" / "eval-checkpoints.db")
    cases = [c for c in CASES if not ids or c["id"] in ids]
    rows = []
    for case in cases:
        order_id = (re.search(r"A-\d+", case["message"]) or [None])[0]
        row = {"case": case["id"], "expect": case["expect"]}
        for design, run in (("loop", lambda: run_loop(case, platform)),
                            ("graph", lambda: run_graph(case, platform, cp))):
            platform.reset()
            try:
                r = run()
            except Exception as e:                      # a crash is a wrong outcome, not a stop
                r = {"error": f"{type(e).__name__}: {e}"[:200], "seconds": 0, "usage": {}, "path": []}
            r.update(outcome(platform, order_id))
            r["correct"] = score(case, r) and "error" not in r
            row[design] = r
            mark = "✓" if r["correct"] else "✕"
            print(f"  {case['id']:<24} {design:<5} {mark} paid ${r['paid']:<6} manager={r['asked_manager']!s:<5} "
                  f"calls={r['usage'].get('calls', 0)} tokens_in={r['usage'].get('input_tokens', 0):,} "
                  f"{r['seconds']:.0f}s {r.get('error', '')}")
        rows.append(row)
    save(rows)


def save(rows):
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    (out / f"eval-{stamp}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    lines = ["| Case | Expected | Loop | Graph |", "|---|---|---|---|"]
    for r in rows:
        e = r["expect"]
        want = f"{e['action']} ${e['amount']}" + (" + manager" if e["approval"] else "")
        cell = lambda d: (("✓ " if d["correct"] else "✕ ") + f"paid ${d['paid']:g}"
                          + (" + manager" if d["asked_manager"] else "") + (" (error)" if "error" in d else ""))
        lines.append(f"| {r['case']} | {want} | {cell(r['loop'])} | {cell(r['graph'])} |")
    totals = []
    for design in ("loop", "graph"):
        ds = [r[design] for r in rows]
        totals.append({
            "design": design, "correct": sum(d["correct"] for d in ds), "cases": len(ds),
            "calls": sum(d["usage"].get("calls", 0) for d in ds),
            "input_tokens": sum(d["usage"].get("input_tokens", 0) for d in ds),
            "output_tokens": sum(d["usage"].get("output_tokens", 0) for d in ds),
            "cost": round(sum(d["usage"].get("cost", 0) for d in ds), 5),
            "seconds": round(sum(d["seconds"] for d in ds)),
        })
    lines += ["", "| Design | Correct | Model calls | Tokens in | Tokens out | Cost (list price) | Time |",
              "|---|---|---|---|---|---|---|"]
    for t in totals:
        lines.append(f"| {t['design']} | {t['correct']}/{t['cases']} | {t['calls']} | {t['input_tokens']:,} | "
                     f"{t['output_tokens']:,} | ${t['cost']:.4f} | {t['seconds']}s |")
    (out / f"eval-{stamp}.md").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1:])
