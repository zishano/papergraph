"""Small dependency-free loader for the project's private .env file."""
from __future__ import annotations

import os
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_project_env(path: str | Path = DEFAULT_ENV_FILE) -> Path | None:
    """Load KEY=VALUE pairs without replacing variables set by the shell."""
    source = Path(path)
    if not source.is_file():
        return None
    for number, raw in enumerate(source.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry on line {number}: expected KEY=VALUE")
        key, value = (part.strip() for part in line.split("=", 1))
        if not _KEY.fullmatch(key):
            raise ValueError(f"Invalid .env key on line {number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return source
