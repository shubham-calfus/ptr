from __future__ import annotations

import os
import re
from pathlib import Path

_RUNNER_ENV_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$"
)
_RUNNER_ENV_OVERRIDE_PREFIXES = ("OPENAI_", "ACT_AI_")
_RUNNER_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_DOTENV_PATH = _RUNNER_PROJECT_ROOT / ".env"


def parse_runner_dotenv(
    dotenv_path: Path | None = None,
) -> dict[str, str]:
    path = dotenv_path or _RUNNER_DOTENV_PATH
    if not path.exists():
        return {}

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _RUNNER_ENV_LINE_RE.match(line)
        if not match:
            continue
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[match.group("key")] = value
    return values


def load_runner_local_env(
    dotenv_path: Path | None = None,
    *,
    allowed_prefixes: tuple[str, ...] = _RUNNER_ENV_OVERRIDE_PREFIXES,
) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for key, value in parse_runner_dotenv(dotenv_path).items():
        if allowed_prefixes and not any(key.startswith(prefix) for prefix in allowed_prefixes):
            continue
        if not str(value).strip():
            continue
        os.environ[key] = value
        loaded[key] = value
    return loaded


def get_runner_env_value(
    key: str,
    default: str = "",
    dotenv_path: Path | None = None,
) -> str:
    env_value = str(os.getenv(key, "")).strip()
    if env_value:
        return env_value
    dotenv_value = str(parse_runner_dotenv(dotenv_path).get(key, "")).strip()
    if dotenv_value:
        return dotenv_value
    return default
