"""Charts for the README and the write-up, drawn from real runs.

    python viz/make_charts.py

Inputs:  viz/data/run-*.json (events of traced runs, exported from the platform's /runs)
         06-reliability/results/eval-*.json (the latest evaluation)
Outputs: viz/*.png
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Palette: categorical slots in fixed order, text in ink tokens, recessive grid.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#fcfcfb"
KIND_COLOR = {"model": BLUE, "http": AQUA, "person": ORANGE}

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "sans-serif", "font.size": 11, "text.color": INK, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.edgecolor": GRID,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
})


def kind_of(node, graph):
    k = next((n.get("kind", "code") for n in graph["nodes"] if n["id"] == node), "code")
    return "model" if k.startswith("model") else "person" if k == "person" else "http"


def spans(run):
    graph = next(e["graph"] for e in run["events"] if e["type"] == "run_started")
    open_, out = {}, []
    for e in run["events"]:
        if e["type"] == "node_started":
            open_[e["node"]] = e["t"]
        elif e["type"] in ("node_finished", "node_failed") and e["node"] in open_:
            out.append((e["node"], open_.pop(e["node"]), e["t"], kind_of(e["node"], graph)))
    return out


def timelines():
    """The same $640 refund in three structures, on one time axis."""
    runs = [("Stage 1 · agent loop", "run-01-loop.json"),
            ("Stage 2 · explicit graph", "run-02-graph.json"),
            ("Stage 3 · supersteps", "run-03-supersteps.json")]
    data = [(title, spans(json.loads((HERE / "data" / f).read_text()))) for title, f in runs]
    end = max(e for _, s in data for _, _, e, _ in s)
    fig, axes = plt.subplots(3, 1, figsize=(11, 8.4), sharex=True,
                             gridspec_kw={"height_ratios": [len({n for n, *_ in s}) for _, s in data]})
    for ax, (title, s) in zip(axes, data):
        order = list(dict.fromkeys(n for n, *_ in s))
        for node, a, b, kind in s:
            ax.barh(order.index(node), max(b - a, 0.4), left=a, height=0.62,
                    color=KIND_COLOR[kind], edgecolor=SURFACE, linewidth=2)
        ax.set_yticks(range(len(order)), [n.replace("request_manager_", "") for n in order], fontsize=10)
        ax.invert_yaxis()
        ax.set_title(title, loc="left", fontsize=12, fontweight="bold", color=INK, pad=6)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", length=0)
    axes[-1].set_xlabel("seconds")
    axes[-1].set_xlim(0, end * 1.02)
    fig.legend(handles=[Patch(color=BLUE, label="model call"), Patch(color=AQUA, label="HTTP call"),
                        Patch(color=ORANGE, label="person (manager approval)")],
               loc="upper right", ncol=3, frameon=False, bbox_to_anchor=(0.98, 1.0))
    fig.suptitle("The same $640 refund, three structures", x=0.01, ha="left", fontsize=15,
                 fontweight="bold")
    fig.text(0.01, 0.94, "Real runs on gemini-3.5-flash. In the loop and the explicit graph the three "
             "lookups run one after another; in supersteps they share one 2 s round.",
             fontsize=10, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.subplots_adjust(top=0.86)
    fig.savefig(HERE / "timelines.png", dpi=200)
    plt.close(fig)


def context():
    """Tokens each model call reads: the loop re-reads its transcript, nodes read their fields."""
    loop = [528, 1093, 1440, 1748]            # stage 1, real run (01-agent-loop/README.md)
    graph = [("classify", 114), ("decide", 558)]   # real: decide alone read 558 (stage 4 resume), 672 in total
    fig, ax = plt.subplots(figsize=(9, 4.6))
    xs = range(1, len(loop) + 1)
    ax.bar([x - 0.2 for x in xs], loop, width=0.38, color=BLUE, label="agent loop: call n re-reads everything")
    ax.bar([1 + 0.2, 2 + 0.2], [t for _, t in graph], width=0.38, color=ORANGE,
           label="graph: each node reads only its fields")
    for x, t in zip(xs, loop):
        ax.text(x - 0.2, t + 25, f"{t:,}", ha="center", fontsize=10, color=INK2)
    for i, (name, t) in enumerate(graph):
        ax.text(i + 1.2, t + 25, f"{t:,}\n{name}", ha="center", fontsize=10, color=INK2)
    ax.set_xticks(list(xs), [f"call {x}" for x in xs])
    ax.set_ylabel("input tokens")
    ax.grid(axis="y", color=GRID)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", length=0)
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("Tokens read per model call, same refund", loc="left", fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(HERE / "context.png", dpi=200)
    plt.close(fig)


def reliability():
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ns = list(range(1, 101))
    ax.plot(ns, [0.99 ** n for n in ns], color=BLUE, linewidth=2)
    for n, label, offset in [(2, "graph: 2 judgments", (6, -95)), (7, "loop: ~7 choices", (40, -12)),
                             (60, "a 60-step job", (8, 8))]:
        p = 0.99 ** n
        ax.plot([n], [p], "o", color=BLUE, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2)
        ax.annotate(f"{label}: {p:.0%}", (n, p), xytext=offset, textcoords="offset points", fontsize=10,
                    color=INK2, arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8})
    ax.set_ylim(0, 1.05)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.set_xlabel("uncertain decisions in a row, each right 99% of the time")
    ax.set_ylabel("runs that get through")
    ax.grid(color=GRID)
    ax.set_axisbelow(True)
    ax.set_title("Reliability decays exponentially with the number of uncertain decisions",
                 loc="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(HERE / "reliability.png", dpi=200)
    plt.close(fig)


def evaluation():
    files = sorted((ROOT / "06-reliability" / "results").glob("eval-*.json"))
    if not files:
        return
    rows = json.loads(files[-1].read_text())
    designs = [("loop", "agent loop", BLUE), ("graph", "graph", ORANGE)]
    metrics = [("correct", "correct outcomes", lambda d: sum(x["correct"] for x in d), "{:.0f}/" + str(len(rows))),
               ("calls", "model calls", lambda d: sum(x["usage"].get("calls", 0) for x in d), "{:.0f}"),
               ("tokens", "input tokens", lambda d: sum(x["usage"].get("input_tokens", 0) for x in d), "{:,.0f}"),
               ("time", "minutes", lambda d: sum(x["seconds"] for x in d) / 60, "{:.0f}")]
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.8))
    for ax, (_, title, fn, fmt) in zip(axes, metrics):
        vals = [fn([r[k] for r in rows]) for k, _, _ in designs]
        ax.bar([0, 1], vals, color=[c for *_, c in designs], width=0.6)
        for i, v in enumerate(vals):
            ax.text(i, v, fmt.format(v), ha="center", va="bottom", fontsize=11, color=INK)
        ax.set_xticks([0, 1], [n for _, n, _ in designs])
        ax.set_yticks([])
        ax.set_title(title, fontsize=11, color=INK2)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(axis="x", length=0)
        ax.set_ylim(0, max(vals) * 1.2 or 1)
    fig.suptitle(f"Agent loop vs graph on {len(rows)} refund cases (real model, same platform)",
                 x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(HERE / "evaluation.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    timelines()
    context()
    reliability()
    evaluation()
    print("wrote", ", ".join(p.name for p in sorted(HERE.glob("*.png"))))
