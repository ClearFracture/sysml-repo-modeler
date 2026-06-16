from __future__ import annotations

import json
import logging
import mimetypes
import queue
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from re import Match
from typing import Any
from urllib.parse import urlparse

from ..services import (
    MonitorInProgressError,
    health_to_json,
    import_request_from_json,
)
from .serializers import (
    package_registration_to_response,
    project_to_response,
)
from .status import project_workspace_status, runtime_status
from .web_common import ALLOWED_ARTIFACTS, is_unsafe_segment

logger = logging.getLogger(__name__)

_SAFE_SEGMENT = r"[A-Za-z0-9_.-]+"
_NAME_SEGMENT = r"[^/]+"

_SSE_POLL_INTERVAL = 0.5  # seconds between event-store polls
_SSE_HEARTBEAT_INTERVAL = 15.0  # seconds between SSE keepalive comments
_SSE_MAX_WAIT = 600.0  # seconds before the stream times out


class SysmlBackendHandler(BaseHTTPRequestHandler):
    server_version = "SysmlBackend/0.1"

    _GET_ROUTES: list[tuple[re.Pattern[str], str]] = []
    _POST_ROUTES: list[tuple[re.Pattern[str], str]] = []
    _PATCH_ROUTES: list[tuple[re.Pattern[str], str]] = []
    _DELETE_ROUTES: list[tuple[re.Pattern[str], str]] = []

    _responded: bool = False

    # ---- dispatch -----------------------------------------------------------

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        self._dispatch(self._GET_ROUTES)

    def do_POST(self) -> None:
        self._dispatch(self._POST_ROUTES)

    def do_PATCH(self) -> None:
        self._dispatch(self._PATCH_ROUTES)

    def do_DELETE(self) -> None:
        self._dispatch(self._DELETE_ROUTES)

    def _dispatch(self, routes: list[tuple[re.Pattern[str], str]]) -> None:
        path = urlparse(self.path).path
        try:
            for pattern, handler_name in routes:
                match = pattern.match(path)
                if match is not None:
                    getattr(self, handler_name)(match)
                    return
            if self.command == "GET" and not path.startswith("/api"):
                self.serve_static(path)
                return
            self.respond_json(
                {"error": "not_found", "message": f"No route registered for {path}"},
                status=HTTPStatus.NOT_FOUND,
            )
        except Exception:
            logger.exception(
                "[http] unhandled error handling %s %s", self.command, path
            )
            if not self._responded:
                self.respond_json(
                    {
                        "error": "internal_error",
                        "message": "The backend encountered an unexpected error.",
                    },
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

    # ---- GET handlers -------------------------------------------------------

    def get_health(self, match: Match[str]) -> None:
        self.respond_json(
            {"status": "ok", "service": "sysml-backend", "version": "0.1.0"}
        )

    def get_status(self, match: Match[str]) -> None:
        self.respond_json(runtime_status(self.server.services))

    def get_projects(self, match: Match[str]) -> None:
        self.respond_json({"projects": self.server.project_registry.list_projects()})

    def get_project_repositories(self, match: Match[str]) -> None:
        project = self._require_project(match.group("name"))
        if project is None:
            return
        repositories = project.get("repositories", [])
        self.respond_json(
            {
                "project": project,
                "repositories": repositories if isinstance(repositories, list) else [],
            }
        )

    def get_project_runs(self, match: Match[str]) -> None:
        project = self._require_project(match.group("name"))
        if project is None:
            return
        slug = str(project.get("slug") or match.group("name"))
        self.respond_json(
            {"runs": self.server.monitoring_service.list_project_cycles(slug)}
        )

    def get_opencode_health(self, match: Match[str]) -> None:
        self.respond_json(
            {"opencode": health_to_json(self.server.opencode_client.health())}
        )

    def get_project_workspace_health(self, match: Match[str]) -> None:
        self.respond_json(
            {"projectWorkspace": project_workspace_status(self.server.services)}
        )

    def get_opencode_config(self, match: Match[str]) -> None:
        cfg = self.server.opencode_client.config
        self.respond_json(
            {
                "configured": bool(cfg.base_url),
                "baseUrl": cfg.base_url,
                "healthPath": cfg.health_path,
                "agent": cfg.agent,
                "timeoutSeconds": cfg.timeout_seconds,
            }
        )

    def get_opencode_sessions(self, match: Match[str]) -> None:
        self.respond_json({"sessions": self.server.opencode_client.list_sessions()})

    def get_opencode_session_messages(self, match: Match[str]) -> None:
        session_id = match.group("session_id")
        self.respond_json(
            {"messages": self.server.opencode_client.get_session_messages(session_id)}
        )

    def get_packages(self, match: Match[str]) -> None:
        self.respond_json({"packages": self.server.package_registry.list_packages()})

    def get_runs(self, match: Match[str]) -> None:
        self.respond_json({"runs": self.server.monitoring_service.list_cycles()})

    def get_runs_inflight(self, match: Match[str]) -> None:
        self.respond_json(
            {"runIds": self.server.monitoring_service.list_inflight_run_ids()}
        )

    def get_run_events(self, match: Match[str]) -> None:
        run_id = match.group("run_id")
        self.respond_json({"events": self.server.event_store.list_events(run_id)})

    def get_run_events_stream(self, match: Match[str]) -> None:
        """SSE endpoint — streams RunEvents for a run as they are appended."""
        run_id = match.group("run_id")
        self._responded = True
        self.send_response(HTTPStatus.OK)
        self.send_cors_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        last_index = 0
        elapsed = 0.0
        last_heartbeat = 0.0

        try:
            while elapsed < _SSE_MAX_WAIT:
                events = self.server.event_store.list_events(run_id)
                if len(events) > last_index:
                    for event in events[last_index:]:
                        data = json.dumps(event).encode("utf-8")
                        self.wfile.write(b"data: " + data + b"\n\n")
                    self.wfile.flush()
                    last_index = len(events)

                status = self.server.monitoring_service.get_run_status(run_id)
                if status in (
                    "completed",
                    "failed",
                    "needs_attention",
                ) and last_index >= len(events):
                    self.wfile.write(b"event: done\ndata: {}\n\n")
                    self.wfile.flush()
                    break

                if elapsed - last_heartbeat >= _SSE_HEARTBEAT_INTERVAL:
                    self.wfile.write(b":\n\n")
                    self.wfile.flush()
                    last_heartbeat = elapsed

                time.sleep(_SSE_POLL_INTERVAL)
                elapsed += _SSE_POLL_INTERVAL
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # client disconnected

    def get_run_changes(self, match: Match[str]) -> None:
        run_id = match.group("run_id")
        changes = self.server.monitoring_service.changes_for_run(run_id)
        if changes is None:
            self.respond_json(
                {"error": "not_found", "message": f"Run {run_id} was not found."},
                status=HTTPStatus.NOT_FOUND,
            )
            return
        self.respond_json(changes)

    def get_artifact(self, match: Match[str]) -> None:
        run_id = match.group("run_id")
        pass_id = match.group("pass_id")
        artifact_name = match.group("artifact_name")
        if artifact_name not in ALLOWED_ARTIFACTS:
            self.respond_json(
                {
                    "error": "invalid_request",
                    "message": f"Unsupported artifact {artifact_name}.",
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if is_unsafe_segment(run_id) or is_unsafe_segment(pass_id):
            self.respond_json(
                {
                    "error": "invalid_request",
                    "message": "Invalid run or pass identifier.",
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        artifact_payload = self.server.analysis_store.read_artifact(
            run_id, pass_id, artifact_name
        )
        if artifact_payload is None:
            self.respond_json(
                {
                    "error": "not_found",
                    "message": f"Artifact {artifact_name} was not found.",
                },
                status=HTTPStatus.NOT_FOUND,
            )
            return
        body, content_type = artifact_payload
        self.respond_bytes(body, content_type=content_type)

    # ---- POST handlers ------------------------------------------------------

    def post_opencode_ping(self, match: Match[str]) -> None:
        self.respond_json(self.server.opencode_client.ping())

    def post_projects(self, match: Match[str]) -> None:
        payload = self.read_json_body()
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            self.respond_json(
                {"error": "invalid_request", "message": "Project name is required."},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            project = self.server.project_workspace.create_project(name.strip())
        except ValueError as error:
            self.respond_json(
                {"error": "invalid_request", "message": str(error)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        project_payload = project_to_response(project)
        self.server.analysis_store.save_project(project_payload)
        self.respond_json({"project": project_payload}, status=HTTPStatus.CREATED)

    def post_import_repositories(self, match: Match[str]) -> None:
        project = self._require_project(match.group("name"))
        if project is None:
            return

        payload = self.read_json_body()
        raw_repositories = payload.get("repositories")
        if not isinstance(raw_repositories, list) or not raw_repositories:
            self.respond_json(
                {
                    "error": "invalid_request",
                    "message": "At least one repository is required.",
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if not all(isinstance(repository, dict) for repository in raw_repositories):
            self.respond_json(
                {
                    "error": "invalid_request",
                    "message": "Repository entries must be objects.",
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        github_token = _github_token_from_payload(payload)

        try:
            repositories = [
                import_request_from_json(_with_github_token(repository, github_token))
                for repository in raw_repositories
            ]
        except ValueError as error:
            self.respond_json(
                {"error": "invalid_request", "message": str(error)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            results = self.server.project_workspace.import_repositories(
                project["name"], repositories
            )
        except ValueError as error:
            self.respond_json(
                {"error": "invalid_request", "message": str(error)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        updated_project = self.server.project_registry.record_import_results(
            project["name"], results
        )
        project_payload = project_to_response(updated_project)
        self.server.analysis_store.save_project(project_payload)
        self.respond_json(
            {
                "project": project_payload,
                "repositories": updated_project.repositories,
            },
            status=HTTPStatus.CREATED,
        )

    def post_refresh_repositories(self, match: Match[str]) -> None:
        project = self._require_project(match.group("name"))
        if project is None:
            return
        payload = self.read_json_body()
        raw_repositories = project.get("repositories", [])
        repositories = raw_repositories if isinstance(raw_repositories, list) else []
        try:
            results = self.server.project_workspace.refresh_repositories(
                project["name"], repositories, _github_token_from_payload(payload)
            )
        except ValueError as error:
            self.respond_json(
                {"error": "invalid_request", "message": str(error)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        updated_project = self.server.project_registry.record_import_results(
            project["name"], results
        )
        project_payload = project_to_response(updated_project)
        self.server.analysis_store.save_project(project_payload)
        self.respond_json(
            {
                "project": project_payload,
                "repositories": updated_project.repositories,
            },
            status=HTTPStatus.CREATED,
        )

    def post_sync_repositories(self, match: Match[str]) -> None:
        project = self._require_project(match.group("name"))
        if project is None:
            return
        payload = self.read_json_body()
        raw_repositories = project.get("repositories", [])
        repositories = raw_repositories if isinstance(raw_repositories, list) else []
        try:
            results = self.server.project_workspace.sync_repositories(
                project["name"], repositories, _github_token_from_payload(payload)
            )
        except ValueError as error:
            self.respond_json(
                {"error": "invalid_request", "message": str(error)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        updated_project = self.server.project_registry.record_import_results(
            project["name"], results
        )
        project_payload = project_to_response(updated_project)
        self.server.analysis_store.save_project(project_payload)
        self.respond_json(
            {
                "project": project_payload,
                "repositories": updated_project.repositories,
            },
            status=HTTPStatus.CREATED,
        )

    def post_sync_repositories_stream(self, match: Match[str]) -> None:
        project = self._require_project(match.group("name"))
        if project is None:
            return
        payload = self.read_json_body()
        raw_repositories = project.get("repositories", [])
        repositories = raw_repositories if isinstance(raw_repositories, list) else []
        github_token = _github_token_from_payload(payload)
        events: "queue.Queue[dict[str, Any] | None]" = queue.Queue()

        def _emit(event: dict[str, Any]) -> None:
            events.put({"type": "progress", **event})

        def _work() -> None:
            try:
                events.put(
                    {
                        "type": "start",
                        "message": "Repository sync started.",
                        "repositoryCount": len(repositories),
                    }
                )
                results = self.server.project_workspace.sync_repositories(
                    project["name"],
                    repositories,
                    github_token,
                    progress_callback=_emit,
                )
                updated_project = self.server.project_registry.record_import_results(
                    project["name"], results
                )
                project_payload = project_to_response(updated_project)
                self.server.analysis_store.save_project(project_payload)
                failures = [
                    repository
                    for repository in updated_project.repositories
                    if repository.get("status") == "failed"
                    or repository.get("error")
                    or (repository.get("git") or {}).get("error")
                ]
                events.put(
                    {
                        "type": "complete",
                        "message": "Repository sync complete.",
                        "project": project_payload,
                        "repositories": updated_project.repositories,
                        "failureCount": len(failures),
                    }
                )
            except Exception as error:
                logger.exception("[git:sync] stream failed")
                events.put({"type": "error", "message": str(error)})
            finally:
                events.put(None)

        self._responded = True
        self.send_response(HTTPStatus.OK)
        self.send_cors_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        worker = threading.Thread(target=_work, daemon=True)
        worker.start()

        try:
            while True:
                event = events.get()
                if event is None:
                    self._write_sse_event({"type": "done"})
                    break
                self._write_sse_event(event)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            logger.info("[git:sync] client disconnected from sync stream")

    def post_monitor_project(self, match: Match[str]) -> None:
        project = self._require_project(match.group("name"))
        if project is None:
            return
        try:
            run_id = self.server.monitoring_service.start_project_cycle_async(project)
        except MonitorInProgressError as error:
            self.respond_json(
                {"error": "conflict", "message": str(error)},
                status=HTTPStatus.CONFLICT,
            )
            return
        except ValueError as error:
            self.respond_json(
                {"error": "invalid_request", "message": str(error)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        status = self.server.monitoring_service.get_run_status(run_id) or "queued"
        self.respond_json(
            {"run": {"runId": run_id, "status": status}}, status=HTTPStatus.ACCEPTED
        )

    def post_packages(self, match: Match[str]) -> None:
        payload = self.read_json_body()
        name = payload.get("name")
        path = payload.get("path")
        if not isinstance(name, str) or not name.strip():
            self.respond_json(
                {"error": "invalid_request", "message": "Package name is required."},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if not isinstance(path, str) or not path.strip():
            self.respond_json(
                {"error": "invalid_request", "message": "Package path is required."},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        registration = self.server.package_registry.register_package(
            name.strip(), path.strip()
        )
        self.respond_json(
            {"package": package_registration_to_response(registration)},
            status=HTTPStatus.CREATED,
        )

    def post_monitor_package(self, match: Match[str]) -> None:
        package_name = match.group("name")
        try:
            run_id = self.server.monitoring_service.start_cycle_async(package_name)
        except MonitorInProgressError as error:
            self.respond_json(
                {"error": "conflict", "message": str(error)},
                status=HTTPStatus.CONFLICT,
            )
            return
        except ValueError as error:
            self.respond_json(
                {"error": "not_found", "message": str(error)},
                status=HTTPStatus.NOT_FOUND,
            )
            return
        status = self.server.monitoring_service.get_run_status(run_id) or "queued"
        self.respond_json(
            {"run": {"runId": run_id, "status": status}}, status=HTTPStatus.ACCEPTED
        )

    # ---- PATCH handlers -----------------------------------------------------

    def patch_project(self, match: Match[str]) -> None:
        slug = match.group("slug")
        project = self._require_project(slug)
        if project is None:
            return
        payload = self.read_json_body()
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            self.respond_json(
                {"error": "invalid_request", "message": "Project name is required."},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            updated_project = self.server.project_workspace.rename_project(
                slug, name.strip()
            )
            project_payload = project_to_response(updated_project)
            self.server.analysis_store.rename_project(
                project_payload["slug"], project_payload["name"]
            )
        except ValueError as error:
            self.respond_json(
                {"error": "invalid_request", "message": str(error)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self.respond_json({"project": project_payload})

    # ---- DELETE handlers ----------------------------------------------------

    def delete_project(self, match: Match[str]) -> None:
        slug = match.group("slug")
        project = self._require_project(slug)
        if project is None:
            return
        try:
            deleted_slug = self.server.project_workspace.delete_project(slug)
            self.server.analysis_store.delete_project(deleted_slug)
        except ValueError as error:
            self.respond_json(
                {"error": "invalid_request", "message": str(error)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def delete_repository_missing_name(self, match: Match[str]) -> None:
        self.respond_json(
            {"error": "not_found", "message": "No repository name provided."},
            status=HTTPStatus.NOT_FOUND,
        )

    def delete_repository(self, match: Match[str]) -> None:
        project_slug = match.group("slug")
        repo_name = match.group("repo_name")
        project = self._require_project(project_slug)
        if project is None:
            return
        try:
            self.server.project_workspace.delete_repository(project_slug, repo_name)
            updated_project = self.server.project_registry.remove_repository(
                project_slug, repo_name
            )
            self.server.analysis_store.save_project(
                project_to_response(updated_project)
            )
        except ValueError as error:
            self.respond_json(
                {"error": "invalid_request", "message": str(error)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    # ---- helpers ------------------------------------------------------------

    def _require_project(self, name_or_slug: str) -> dict[str, Any] | None:
        project = self.server.project_registry.get_project(name_or_slug)
        if project is None:
            self.respond_json(
                {
                    "error": "not_found",
                    "message": f"Project {name_or_slug} was not found.",
                },
                status=HTTPStatus.NOT_FOUND,
            )
            return None
        return project

    def log_message(self, format: str, *args: Any) -> None:
        if urlparse(self.path).path == "/api/health":
            return
        logger.info("[http] %s - %s", self.address_string(), format % args)

    def read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        body = self.rfile.read(content_length).decode("utf-8")
        payload = json.loads(body)
        return payload if isinstance(payload, dict) else {}

    def respond_json(
        self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self._responded = True
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_bytes(
        self,
        body: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self._responded = True
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, request_path: str) -> None:
        static_root = self.server.services.config.static_assets_path.resolve()
        relative = request_path.lstrip("/") or "index.html"
        candidate = (static_root / relative).resolve()
        try:
            candidate.relative_to(static_root)
        except ValueError:
            candidate = static_root / "index.html"
        if not candidate.is_file():
            candidate = static_root / "index.html"
        if not candidate.is_file():
            self.respond_json(
                {"error": "not_found", "message": "UI static assets were not found."},
                status=HTTPStatus.NOT_FOUND,
            )
            return
        content_type = (
            mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        )
        self.respond_bytes(candidate.read_bytes(), content_type=content_type)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _write_sse_event(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.wfile.write(b"data: " + data + b"\n\n")
        self.wfile.flush()


def _compile(routes: list[tuple[str, str]]) -> list[tuple[re.Pattern[str], str]]:
    return [(re.compile(f"^{pattern}$"), handler) for pattern, handler in routes]


def _github_token_from_payload(payload: dict[str, Any]) -> str | None:
    token = payload.get("githubToken") or payload.get("token")
    return token.strip() if isinstance(token, str) and token.strip() else None


def _with_github_token(
    repository: dict[str, Any], github_token: str | None
) -> dict[str, Any]:
    if not github_token or repository.get("githubToken") or repository.get("token"):
        return repository
    return {**repository, "githubToken": github_token}


SysmlBackendHandler._GET_ROUTES = _compile(
    [
        (r"/api/health", "get_health"),
        (r"/api/status", "get_status"),
        (r"/api/projects", "get_projects"),
        (
            rf"/api/projects/(?P<name>{_NAME_SEGMENT})/repositories",
            "get_project_repositories",
        ),
        (
            rf"/api/projects/(?P<name>{_NAME_SEGMENT})/runs",
            "get_project_runs",
        ),
        (r"/api/opencode/health", "get_opencode_health"),
        (r"/api/project-workspace/health", "get_project_workspace_health"),
        (r"/api/opencode/config", "get_opencode_config"),
        (r"/api/opencode/sessions", "get_opencode_sessions"),
        (
            rf"/api/opencode/sessions/(?P<session_id>{_NAME_SEGMENT})/messages",
            "get_opencode_session_messages",
        ),
        (r"/api/packages", "get_packages"),
        (r"/api/runs", "get_runs"),
        (r"/api/runs/inflight", "get_runs_inflight"),
        (
            rf"/api/runs/(?P<run_id>{_SAFE_SEGMENT})/events/stream",
            "get_run_events_stream",
        ),
        (rf"/api/runs/(?P<run_id>{_SAFE_SEGMENT})/events", "get_run_events"),
        (rf"/api/runs/(?P<run_id>{_SAFE_SEGMENT})/changes", "get_run_changes"),
        (
            rf"/api/runs/(?P<run_id>{_SAFE_SEGMENT})/passes/(?P<pass_id>{_SAFE_SEGMENT})"
            rf"/artifacts/(?P<artifact_name>{_SAFE_SEGMENT})",
            "get_artifact",
        ),
    ]
)

SysmlBackendHandler._POST_ROUTES = _compile(
    [
        (r"/api/opencode/ping", "post_opencode_ping"),
        (r"/api/projects", "post_projects"),
        (
            rf"/api/projects/(?P<name>{_NAME_SEGMENT})/repositories/import",
            "post_import_repositories",
        ),
        (
            rf"/api/projects/(?P<name>{_NAME_SEGMENT})/repositories/refresh",
            "post_refresh_repositories",
        ),
        (
            rf"/api/projects/(?P<name>{_NAME_SEGMENT})/repositories/sync/stream",
            "post_sync_repositories_stream",
        ),
        (
            rf"/api/projects/(?P<name>{_NAME_SEGMENT})/repositories/sync",
            "post_sync_repositories",
        ),
        (rf"/api/projects/(?P<name>{_NAME_SEGMENT})/monitor", "post_monitor_project"),
        (r"/api/packages", "post_packages"),
        (rf"/api/packages/(?P<name>{_NAME_SEGMENT})/monitor", "post_monitor_package"),
    ]
)

SysmlBackendHandler._PATCH_ROUTES = _compile(
    [
        (rf"/api/projects/(?P<slug>{_NAME_SEGMENT})", "patch_project"),
    ]
)

SysmlBackendHandler._DELETE_ROUTES = _compile(
    [
        (rf"/api/projects/(?P<slug>{_NAME_SEGMENT})", "delete_project"),
        (
            rf"/api/projects/(?P<slug>{_NAME_SEGMENT})/repositories",
            "delete_repository_missing_name",
        ),
        (
            rf"/api/projects/(?P<slug>{_NAME_SEGMENT})/repositories/(?P<repo_name>{_NAME_SEGMENT})",
            "delete_repository",
        ),
    ]
)
