"""FastAPI/ASGI adapter for the backend, runnable under uvicorn.

This is an alternative front end to the standard-library server in ``server.py``.
Both share the exact same service layer (``build_services``), serializers, and
validation rules, so the HTTP contract is identical between the two.

Run it with::

    uvicorn sysml_backend.interfaces.asgi:app
    # or
    python -m sysml_backend.interfaces.asgi
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from ..services import MonitorInProgressError, health_to_json, import_request_from_json
from ..utils import BackendConfig, load_config
from .serializers import package_registration_to_response, project_to_response
from .server import Services, build_services
from .status import project_workspace_status, runtime_status
from .web_common import ALLOWED_ARTIFACTS, is_unsafe_segment

logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status_code)


def create_app(config: BackendConfig | None = None) -> FastAPI:
    resolved_config = config or load_config()
    services = build_services(resolved_config)

    app = FastAPI(title="sysml-backend", version="0.1.0")
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    _register_routes(app, services)
    return app


def _register_routes(app: FastAPI, services: Services) -> None:
    def _require_project(name_or_slug: str) -> dict[str, Any] | None:
        return services.project_registry.get_project(name_or_slug)

    # ---- GET ---------------------------------------------------------------

    @app.get("/api/health")
    def health() -> Any:
        return {"status": "ok", "service": "sysml-backend", "version": "0.1.0"}

    @app.get("/api/status")
    def status() -> Any:
        return runtime_status(services)

    @app.get("/api/projects")
    def list_projects() -> Any:
        return {"projects": services.project_registry.list_projects()}

    @app.get("/api/projects/{name}/repositories")
    def project_repositories(name: str) -> Any:
        project = _require_project(name)
        if project is None:
            return _error(404, "not_found", f"Project {name} was not found.")
        repositories = project.get("repositories", [])
        return {
            "project": project,
            "repositories": repositories if isinstance(repositories, list) else [],
        }

    @app.get("/api/projects/{name}/runs")
    def project_runs(name: str) -> Any:
        project = _require_project(name)
        if project is None:
            return _error(404, "not_found", f"Project {name} was not found.")
        slug = str(project.get("slug") or name)
        return {"runs": services.monitoring_service.list_project_cycles(slug)}

    @app.get("/api/opencode/health")
    def opencode_health() -> Any:
        return {"opencode": health_to_json(services.opencode_client.health())}

    @app.get("/api/project-workspace/health")
    def project_workspace_health() -> Any:
        return {"projectWorkspace": project_workspace_status(services)}

    @app.get("/api/opencode/config")
    def opencode_config() -> Any:
        cfg = services.opencode_client.config
        return {
            "configured": bool(cfg.base_url),
            "baseUrl": cfg.base_url,
            "healthPath": cfg.health_path,
            "agent": cfg.agent,
            "timeoutSeconds": cfg.timeout_seconds,
        }

    @app.get("/api/opencode/sessions")
    def opencode_sessions() -> Any:
        return {"sessions": services.opencode_client.list_sessions()}

    @app.get("/api/opencode/sessions/{session_id}/messages")
    def opencode_session_messages(session_id: str) -> Any:
        return {"messages": services.opencode_client.get_session_messages(session_id)}

    @app.get("/api/packages")
    def list_packages() -> Any:
        return {"packages": services.package_registry.list_packages()}

    @app.get("/api/runs")
    def list_runs() -> Any:
        return {"runs": services.monitoring_service.list_cycles()}

    @app.get("/api/runs/inflight")
    def runs_inflight() -> Any:
        return {"runIds": services.monitoring_service.list_inflight_run_ids()}

    @app.get("/api/runs/{run_id}/events/stream")
    async def run_events_stream(run_id: str) -> Any:
        poll = 0.5
        heartbeat = 15.0
        max_wait = 600.0

        async def _generate():
            last_index = 0
            elapsed = 0.0
            last_heartbeat = 0.0
            while elapsed < max_wait:
                events = services.event_store.list_events(run_id)
                if len(events) > last_index:
                    for event in events[last_index:]:
                        yield f"data: {json.dumps(event)}\n\n"
                    last_index = len(events)
                status = services.monitoring_service.get_run_status(run_id)
                if status in (
                    "completed",
                    "failed",
                    "needs_attention",
                ) and last_index >= len(events):
                    yield "event: done\ndata: {}\n\n"
                    break
                if elapsed - last_heartbeat >= heartbeat:
                    yield ":\n\n"
                    last_heartbeat = elapsed
                await asyncio.sleep(poll)
                elapsed += poll

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str) -> Any:
        return {"events": services.event_store.list_events(run_id)}

    @app.get("/api/runs/{run_id}/changes")
    def run_changes(run_id: str) -> Any:
        changes = services.monitoring_service.changes_for_run(run_id)
        if changes is None:
            return _error(404, "not_found", f"Run {run_id} was not found.")
        return changes

    @app.get("/api/runs/{run_id}/passes/{pass_id}/artifacts/{artifact_name}")
    def artifact(run_id: str, pass_id: str, artifact_name: str) -> Any:
        if artifact_name not in ALLOWED_ARTIFACTS:
            return _error(
                400, "invalid_request", f"Unsupported artifact {artifact_name}."
            )
        if is_unsafe_segment(run_id) or is_unsafe_segment(pass_id):
            return _error(400, "invalid_request", "Invalid run or pass identifier.")
        artifact_payload = services.analysis_store.read_artifact(
            run_id, pass_id, artifact_name
        )
        if artifact_payload is None:
            return _error(404, "not_found", f"Artifact {artifact_name} was not found.")
        body, content_type = artifact_payload
        return Response(content=body, media_type=content_type)

    # ---- POST --------------------------------------------------------------

    @app.post("/api/opencode/ping")
    def opencode_ping() -> Any:
        return services.opencode_client.ping()

    @app.post("/api/projects")
    def create_project(payload: dict[str, Any] = Body(default=None)) -> Any:
        payload = payload or {}
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return _error(400, "invalid_request", "Project name is required.")
        try:
            project = services.project_workspace.create_project(name.strip())
        except ValueError as error:
            return _error(400, "invalid_request", str(error))
        project_payload = project_to_response(project)
        services.analysis_store.save_project(project_payload)
        return JSONResponse({"project": project_payload}, status_code=201)

    @app.post("/api/projects/{name}/repositories/import")
    def import_repositories(
        name: str, payload: dict[str, Any] = Body(default=None)
    ) -> Any:
        project = _require_project(name)
        if project is None:
            return _error(404, "not_found", f"Project {name} was not found.")
        payload = payload or {}
        raw_repositories = payload.get("repositories")
        if not isinstance(raw_repositories, list) or not raw_repositories:
            return _error(
                400, "invalid_request", "At least one repository is required."
            )
        if not all(isinstance(repository, dict) for repository in raw_repositories):
            return _error(400, "invalid_request", "Repository entries must be objects.")
        github_token = _github_token_from_payload(payload)
        try:
            repositories = [
                import_request_from_json(_with_github_token(repository, github_token))
                for repository in raw_repositories
            ]
        except ValueError as error:
            return _error(400, "invalid_request", str(error))
        try:
            results = services.project_workspace.import_repositories(
                project["name"], repositories
            )
        except ValueError as error:
            return _error(400, "invalid_request", str(error))
        updated_project = services.project_registry.record_import_results(
            project["name"], results
        )
        project_payload = project_to_response(updated_project)
        services.analysis_store.save_project(project_payload)
        return JSONResponse(
            {
                "project": project_payload,
                "repositories": updated_project.repositories,
            },
            status_code=201,
        )

    @app.post("/api/projects/{name}/repositories/refresh")
    def refresh_repositories(
        name: str, payload: dict[str, Any] = Body(default=None)
    ) -> Any:
        project = _require_project(name)
        if project is None:
            return _error(404, "not_found", f"Project {name} was not found.")
        raw_repositories = project.get("repositories", [])
        repositories = raw_repositories if isinstance(raw_repositories, list) else []
        try:
            results = services.project_workspace.refresh_repositories(
                project["name"], repositories, _github_token_from_payload(payload or {})
            )
        except ValueError as error:
            return _error(400, "invalid_request", str(error))
        updated_project = services.project_registry.record_import_results(
            project["name"], results
        )
        project_payload = project_to_response(updated_project)
        services.analysis_store.save_project(project_payload)
        return JSONResponse(
            {
                "project": project_payload,
                "repositories": updated_project.repositories,
            },
            status_code=201,
        )

    @app.post("/api/projects/{name}/repositories/sync")
    def sync_repositories(
        name: str, payload: dict[str, Any] = Body(default=None)
    ) -> Any:
        project = _require_project(name)
        if project is None:
            return _error(404, "not_found", f"Project {name} was not found.")
        raw_repositories = project.get("repositories", [])
        repositories = raw_repositories if isinstance(raw_repositories, list) else []
        try:
            results = services.project_workspace.sync_repositories(
                project["name"], repositories, _github_token_from_payload(payload or {})
            )
        except ValueError as error:
            return _error(400, "invalid_request", str(error))
        updated_project = services.project_registry.record_import_results(
            project["name"], results
        )
        project_payload = project_to_response(updated_project)
        services.analysis_store.save_project(project_payload)
        return JSONResponse(
            {
                "project": project_payload,
                "repositories": updated_project.repositories,
            },
            status_code=201,
        )

    @app.post("/api/projects/{name}/repositories/sync/stream")
    async def sync_repositories_stream(
        name: str, payload: dict[str, Any] = Body(default=None)
    ) -> Any:
        project = _require_project(name)
        if project is None:
            return _error(404, "not_found", f"Project {name} was not found.")
        payload = payload or {}
        raw_repositories = project.get("repositories", [])
        repositories = raw_repositories if isinstance(raw_repositories, list) else []
        github_token = _github_token_from_payload(payload)

        async def _generate():
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            def _emit(event: dict[str, Any]) -> None:
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"type": "progress", **event}
                )

            def _work() -> None:
                try:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "start",
                            "message": "Repository sync started.",
                            "repositoryCount": len(repositories),
                        },
                    )
                    results = services.project_workspace.sync_repositories(
                        project["name"],
                        repositories,
                        github_token,
                        progress_callback=_emit,
                    )
                    updated_project = services.project_registry.record_import_results(
                        project["name"], results
                    )
                    project_payload = project_to_response(updated_project)
                    services.analysis_store.save_project(project_payload)
                    failures = [
                        repository
                        for repository in updated_project.repositories
                        if repository.get("status") == "failed"
                        or repository.get("error")
                        or (repository.get("git") or {}).get("error")
                    ]
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "complete",
                            "message": "Repository sync complete.",
                            "project": project_payload,
                            "repositories": updated_project.repositories,
                            "failureCount": len(failures),
                        },
                    )
                except Exception as error:
                    logger.exception("[git:sync] stream failed")
                    loop.call_soon_threadsafe(
                        queue.put_nowait, {"type": "error", "message": str(error)}
                    )
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            threading.Thread(target=_work, daemon=True).start()

            while True:
                event = await queue.get()
                if event is None:
                    yield 'data: {"type": "done"}\n\n'
                    break
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/projects/{name}/monitor")
    def monitor_project(name: str) -> Any:
        project = _require_project(name)
        if project is None:
            return _error(404, "not_found", f"Project {name} was not found.")
        try:
            run_id = services.monitoring_service.start_project_cycle_async(project)
        except MonitorInProgressError as error:
            return _error(409, "conflict", str(error))
        except ValueError as error:
            return _error(400, "invalid_request", str(error))
        status = services.monitoring_service.get_run_status(run_id) or "queued"
        return JSONResponse(
            {"run": {"runId": run_id, "status": status}}, status_code=202
        )

    @app.post("/api/packages")
    def register_package(payload: dict[str, Any] = Body(default=None)) -> Any:
        payload = payload or {}
        name = payload.get("name")
        path = payload.get("path")
        if not isinstance(name, str) or not name.strip():
            return _error(400, "invalid_request", "Package name is required.")
        if not isinstance(path, str) or not path.strip():
            return _error(400, "invalid_request", "Package path is required.")
        registration = services.package_registry.register_package(
            name.strip(), path.strip()
        )
        return JSONResponse(
            {"package": package_registration_to_response(registration)}, status_code=201
        )

    @app.post("/api/packages/{name}/monitor")
    def monitor_package(name: str) -> Any:
        try:
            run_id = services.monitoring_service.start_cycle_async(name)
        except MonitorInProgressError as error:
            return _error(409, "conflict", str(error))
        except ValueError as error:
            return _error(404, "not_found", str(error))
        status = services.monitoring_service.get_run_status(run_id) or "queued"
        return JSONResponse(
            {"run": {"runId": run_id, "status": status}}, status_code=202
        )

    # ---- DELETE ------------------------------------------------------------

    @app.delete("/api/projects/{slug}/repositories")
    def delete_repository_missing_name(slug: str) -> Any:
        return _error(404, "not_found", "No repository name provided.")

    @app.delete("/api/projects/{slug}/repositories/{repo_name}")
    def delete_repository(slug: str, repo_name: str) -> Any:
        project = _require_project(slug)
        if project is None:
            return _error(404, "not_found", f"Project {slug} was not found.")
        try:
            services.project_workspace.delete_repository(slug, repo_name)
            updated_project = services.project_registry.remove_repository(
                slug, repo_name
            )
            services.analysis_store.save_project(project_to_response(updated_project))
        except ValueError as error:
            return _error(400, "invalid_request", str(error))
        return Response(status_code=204)

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Any:
        static_root = services.config.static_assets_path
        candidate = (static_root / full_path).resolve()
        try:
            candidate.relative_to(static_root.resolve())
        except ValueError:
            candidate = static_root / "index.html"
        if candidate.is_file():
            return FileResponse(candidate)
        index = static_root / "index.html"
        if index.is_file():
            return FileResponse(index)
        return _error(404, "not_found", "UI static assets were not found.")


app = create_app()


def _github_token_from_payload(payload: dict[str, Any]) -> str | None:
    token = payload.get("githubToken") or payload.get("token")
    return token.strip() if isinstance(token, str) and token.strip() else None


def _with_github_token(
    repository: dict[str, Any], github_token: str | None
) -> dict[str, Any]:
    if not github_token or repository.get("githubToken") or repository.get("token"):
        return repository
    return {**repository, "githubToken": github_token}


def main() -> None:
    import uvicorn

    from .server import _configure_logging

    _configure_logging()
    config = load_config()
    logger.info(
        "[sysml-backend] uvicorn listening on http://%s:%s", config.host, config.port
    )
    # log_config=None keeps the logging configured by _configure_logging instead of
    # letting uvicorn install its own handlers.
    uvicorn.run(app, host=config.host, port=config.port, log_config=None)


if __name__ == "__main__":
    main()
