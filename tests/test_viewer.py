import time

import httpx

from agentkit.trace import Tracer


def test_a_traced_run_reaches_the_viewer(platform):
    t = Tracer("test", "a run", {"nodes": [{"id": "a"}], "edges": []}, url=platform)
    t.node_started("a")
    t.node_finished("a", {"x": 1})
    t.finished("done", "ok")
    run = httpx.get(f"{platform}/runs/{t.run_id}").json()
    assert run["status"] == "done"
    assert [e["type"] for e in run["events"]] == ["run_started", "node_started", "node_finished", "run_finished"]


def test_the_page_is_served(platform):
    assert "Live runs" in httpx.get(f"{platform}/ui").text
