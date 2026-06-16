from __future__ import annotations

import os
from pathlib import Path


def load_env_file() -> Path | None:
    configured_env_path = _configured_env_path()
    env_path = configured_env_path or _find_env_file(Path.cwd())
    if env_path is None or not env_path.exists():
        return None

    override_existing = configured_env_path is not None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, value = _parse_env_line(line)
        if key is None:
            continue
        if override_existing:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)

    return env_path


def _configured_env_path() -> Path | None:
    configured_path = os.environ.get("SYSML_BACKEND_ENV_FILE")
    if not configured_path:
        return None
    return Path(configured_path)


def _find_env_file(start_path: Path) -> Path | None:
    for path in (start_path, *start_path.parents):
        candidate = path / ".env"
        if candidate.exists():
            return candidate
    return None


def _parse_env_line(line: str) -> tuple[str | None, str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None, ""

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    value = raw_value.strip()
    if not key:
        return None, ""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    return key, value
