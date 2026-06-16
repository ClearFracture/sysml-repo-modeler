from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .env import load_env_file


@dataclass(frozen=True)
class BackendConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    scratch_path: Path = Path("backend-scratch")
    projects_path: Path = Path("packages")
    static_assets_path: Path = Path("static")
    database_url: str | None = None
    opencode_base_url: str | None = None
    opencode_health_path: str = "/global/health"
    opencode_username: str = "opencode"
    opencode_password: str | None = None
    opencode_timeout_seconds: float = 600.0
    opencode_model_id: str | None = None
    opencode_provider_id: str | None = None
    opencode_agent: str | None = None
    opencode_workspace_root: str = "packages"


def load_config() -> BackendConfig:
    load_env_file()
    base_dir = os.environ.get("SYSML_DATA_DIR")
    base = Path(base_dir) if base_dir else None
    package_root = Path(__file__).resolve().parents[1]

    def _dir(env_key: str, name: str) -> Path:
        # An explicit per-directory override always wins. Otherwise, nest the
        # directory under SYSML_DATA_DIR when set, falling back to a CWD-relative
        # default so existing single-directory layouts keep working.
        override = os.environ.get(env_key)
        if override:
            return Path(override)
        if base is not None:
            return base / name
        return Path(name)

    return BackendConfig(
        host=os.environ.get("BACKEND_LISTEN_HOST", "127.0.0.1"),
        port=int(os.environ.get("BACKEND_LISTEN_PORT", "8765")),
        scratch_path=_dir("SYSML_BACKEND_SCRATCH_PATH", "backend-scratch"),
        projects_path=_dir("PROJECT_WORKSPACE_ROOT", "packages"),
        static_assets_path=_static_assets_path(package_root),
        database_url=os.environ.get("DATABASE_URL") or None,
        opencode_base_url=os.environ.get("OPENCODE_BASE_URL") or None,
        opencode_health_path=os.environ.get("OPENCODE_HEALTH_PATH", "/global/health"),
        opencode_username=os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"),
        opencode_password=os.environ.get("OPENCODE_SERVER_PASSWORD") or None,
        opencode_timeout_seconds=float(
            os.environ.get("OPENCODE_TIMEOUT_SECONDS", "600")
        ),
        opencode_model_id=os.environ.get("OPENCODE_MODEL_ID") or None,
        opencode_provider_id=os.environ.get("OPENCODE_PROVIDER_ID") or None,
        opencode_agent=os.environ.get("OPENCODE_AGENT") or None,
        # The path the OpenCode container sees as the project workspace root.
        opencode_workspace_root=os.environ.get("OPENCODE_WORKSPACE_ROOT")
        or "/workspace/projects",
    )


def _static_assets_path(package_root: Path) -> Path:
    override = os.environ.get("SYSML_STATIC_ASSETS_PATH")
    if override:
        return Path(override)

    packaged = package_root / "static"
    if (packaged / "index.html").exists():
        return packaged

    for parent in package_root.parents:
        candidate = parent / "src" / "ui" / "dist"
        if (candidate / "index.html").exists():
            return candidate

    return packaged
