"""Import a stage's modules by folder. Stages reuse file names (refund.py, engine.py), so
clear the previous stage's copies first."""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_NAMES = ("agent", "graph", "engine", "refund", "run", "checkpoint")


def load(stage, module):
    for name in SHARED_NAMES:
        sys.modules.pop(name, None)
    path = str(ROOT / stage)
    sys.path.insert(0, path)
    try:
        return importlib.import_module(module)
    finally:
        sys.path.remove(path)
