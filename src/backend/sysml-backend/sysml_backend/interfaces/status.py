from __future__ import annotations

from typing import TYPE_CHECKING

from ..services import health_to_json

if TYPE_CHECKING:
    from .server import Services


def runtime_status(services: Services) -> dict[str, object]:
    config = services.config
    project_workspace = project_workspace_status(services)
    opencode = health_to_json(services.opencode_client.health())
    dependency_statuses = [
        _dependency_status(project_workspace),
        _dependency_status(opencode),
    ]
    overall = (
        "ok"
        if all(status in {"ok", "unconfigured"} for status in dependency_statuses)
        else "degraded"
    )
    return {
        "status": overall,
        "service": "sysml-backend",
        "version": "0.1.0",
        "repositoryMode": "backend-workspace",
        "storage": "postgres",
        "paths": {
            "scratch": str(config.scratch_path),
            "projects": str(config.projects_path),
            "opencodeWorkspaceRoot": config.opencode_workspace_root,
        },
        "runs": {
            "inflight": services.monitoring_service.list_inflight_run_ids(),
        },
        "projectWorkspace": project_workspace,
        "opencode": opencode,
    }


def project_workspace_status(services: Services) -> dict[str, object]:
    root = services.config.projects_path
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return {
            "configured": True,
            "status": "error",
            "path": str(root),
            "message": f"Project workspace is not writable: {error}",
        }
    return {
        "configured": True,
        "status": "ok",
        "path": str(root),
        "message": "Project workspace is available.",
    }


def _dependency_status(payload: dict[str, object]) -> str:
    value = payload.get("status")
    return value if isinstance(value, str) else "unknown"
