"""Checkpoints in SQLite: the whole state after every superstep, plus what runs next.

Two tables:
  checkpoints  one row per finished round: state, the nodes due next, join progress, status
  writes       results of nodes that finished in a round that then failed or paused, so a
               resume reuses them instead of running those nodes (and paying for them) again
"""
import importlib
import json
import sqlite3
import time
from pathlib import Path

from pydantic import BaseModel

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "var" / "checkpoints.db"


def encode(value):
    """JSON that remembers which pydantic model a value was, so it comes back typed."""
    if isinstance(value, BaseModel):
        cls = type(value)
        return {"__model__": f"{cls.__module__}.{cls.__qualname__}", "data": value.model_dump()}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    return value


def decode(value):
    if isinstance(value, dict) and "__model__" in value:
        module, _, name = value["__model__"].rpartition(".")
        return getattr(importlib.import_module(module), name).model_validate(value["data"])
    if isinstance(value, dict):
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


class Checkpointer:
    def __init__(self, path=DEFAULT_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
            create table if not exists checkpoints (
                thread text, step integer, created real, state text, next text, waiting text,
                status text, note text, primary key (thread, step));
            create table if not exists writes (
                thread text, step integer, node text, update_ text, primary key (thread, step, node));
        """)

    def save(self, thread, step, state, next_nodes, waiting, status="running", note=""):
        with self.db:
            self.db.execute("insert or replace into checkpoints values (?,?,?,?,?,?,?,?)", (
                thread, step, time.time(), json.dumps(encode(state)), json.dumps(next_nodes),
                json.dumps({k: sorted(v) for k, v in waiting.items()}), status, note))

    def latest(self, thread):
        row = self.db.execute("select * from checkpoints where thread = ? order by step desc limit 1",
                              (thread,)).fetchone()
        return self._row(row) if row else None

    def at(self, thread, step):
        row = self.db.execute("select * from checkpoints where thread = ? and step = ?",
                              (thread, step)).fetchone()
        return self._row(row) if row else None

    def history(self, thread):
        rows = self.db.execute("select * from checkpoints where thread = ? order by step", (thread,))
        return [self._row(r) for r in rows]

    def started(self, thread):
        row = self.db.execute("select min(created) from checkpoints where thread = ?", (thread,)).fetchone()
        return row[0]

    def save_write(self, thread, step, node, update):
        with self.db:
            self.db.execute("insert or replace into writes values (?,?,?,?)",
                            (thread, step, node, json.dumps(encode(update))))

    def saved_writes(self, thread, step):
        rows = self.db.execute("select node, update_ from writes where thread = ? and step = ?",
                               (thread, step))
        return {node: decode(json.loads(u)) for node, u in rows}

    def threads(self):
        return [r[0] for r in self.db.execute(
            "select thread from checkpoints group by thread order by max(created) desc")]

    @staticmethod
    def _row(row):
        thread, step, created, state, next_nodes, waiting, status, note = row
        return {"thread": thread, "step": step, "created": created, "state": decode(json.loads(state)),
                "next": json.loads(next_nodes), "waiting": {k: set(v) for k, v in json.loads(waiting).items()},
                "status": status, "note": note}
