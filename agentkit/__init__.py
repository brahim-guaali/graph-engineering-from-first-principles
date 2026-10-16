"""What every stage shares: the platform client, the Gemini client and the cases."""
import os
from pathlib import Path


def load_env(path=Path(__file__).resolve().parents[1] / ".env"):
    """Read KEY=value lines from .env into the environment (without overriding it)."""
    if path.exists():
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            value = value.split("#")[0].strip()
            if key.strip() and not key.startswith("#") and value:
                os.environ.setdefault(key.strip(), value)


load_env()
