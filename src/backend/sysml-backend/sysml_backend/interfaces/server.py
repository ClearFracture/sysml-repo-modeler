from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from http.server import ThreadingHTTPServer

from ..services import (
    AnalysisStore,
    MonitoringService,
    OpenCodeClient,
    OpenCodeConfig,
    PackageRegistry,
    ProjectWorkspaceService,
    ProjectRegistry,
    RunEventStore,
    WorkspaceManager,
    create_document_store,
)
from ..utils import BackendConfig, load_config
from .http_handler import SysmlBackendHandler

logger = logging.getLogger(__name__)


class _HealthAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/health" not in record.getMessage()


@dataclass(frozen=True)
class Services:
    """The backend's service layer, constructed once and shared by every server adapter."""

    package_registry: PackageRegistry
    project_registry: ProjectRegistry
    project_workspace: ProjectWorkspaceService
    analysis_store: AnalysisStore
    event_store: RunEventStore
    opencode_client: OpenCodeClient
    monitoring_service: MonitoringService
    workspaces: WorkspaceManager
    config: BackendConfig


def build_services(config: BackendConfig) -> Services:
    global_store = create_document_store(config.database_url)
    workspaces = WorkspaceManager(config.projects_path, global_store)
    analysis_store = AnalysisStore(config.database_url)
    package_registry = PackageRegistry(workspaces)
    project_registry = ProjectRegistry(workspaces, analysis_store)
    project_workspace = ProjectWorkspaceService(project_registry, config.scratch_path)
    event_store = RunEventStore(config.database_url)
    opencode_client = OpenCodeClient(
        OpenCodeConfig(
            base_url=config.opencode_base_url,
            health_path=config.opencode_health_path,
            username=config.opencode_username,
            password=config.opencode_password,
            timeout_seconds=config.opencode_timeout_seconds,
            model_id=config.opencode_model_id,
            provider_id=config.opencode_provider_id,
            agent=config.opencode_agent,
        )
    )
    monitoring_service = MonitoringService(
        workspaces,
        package_registry,
        event_store,
        opencode_client,
        config.opencode_workspace_root,
        analysis_store,
    )
    return Services(
        package_registry=package_registry,
        project_registry=project_registry,
        project_workspace=project_workspace,
        analysis_store=analysis_store,
        event_store=event_store,
        opencode_client=opencode_client,
        monitoring_service=monitoring_service,
        workspaces=workspaces,
        config=config,
    )


class SysmlBackendServer(ThreadingHTTPServer):
    """Threaded HTTP server that carries the backend's services as typed attributes.

    The request handler reads these directly (``self.server.<service>``), which
    replaces the previous set of ``isinstance``-guarded accessor properties.
    """

    package_registry: PackageRegistry
    project_registry: ProjectRegistry
    project_workspace: ProjectWorkspaceService
    analysis_store: AnalysisStore
    event_store: RunEventStore
    opencode_client: OpenCodeClient
    monitoring_service: MonitoringService
    workspaces: WorkspaceManager
    services: Services


def create_server(config: BackendConfig | None = None) -> SysmlBackendServer:
    resolved_config = config or load_config()
    services = build_services(resolved_config)
    server = SysmlBackendServer(
        (resolved_config.host, resolved_config.port), SysmlBackendHandler
    )

    server.package_registry = services.package_registry
    server.project_registry = services.project_registry
    server.project_workspace = services.project_workspace
    server.analysis_store = services.analysis_store
    server.event_store = services.event_store
    server.opencode_client = services.opencode_client
    server.monitoring_service = services.monitoring_service
    server.workspaces = services.workspaces
    server.services = services
    return server


class _ColorFormatter(logging.Formatter):
    _LEVEL_COLORS: dict[int, str] = {
        logging.DEBUG: "\033[36m",  # cyan
        logging.INFO: "\033[32m",  # green
        logging.WARNING: "\033[33m",  # yellow
        logging.ERROR: "\033[31m",  # red
        logging.CRITICAL: "\033[1;31m",  # bold red
    }
    # Highlight lines that contain opencode thinking/output
    _MSG_COLORS: dict[str, str] = {
        "[opencode:thinking]": "\033[35m",  # magenta
        "[opencode:text]": "\033[32m",  # green
        "[opencode:pass]": "\033[1;36m",  # bold cyan
    }
    _RESET = "\033[0m"
    _DIM = "\033[2m"

    def format(self, record: logging.LogRecord) -> str:
        level_color = self._LEVEL_COLORS.get(record.levelno, "")
        msg = super().format(record)
        for prefix, color in self._MSG_COLORS.items():
            if prefix in msg:
                return f"{color}{msg}{self._RESET}"
        # Dim the logger name portion to reduce noise
        ts, rest = msg[:23], msg[23:]
        return f"{self._DIM}{ts}{self._RESET} {level_color}{rest}{self._RESET}"


def _configure_logging() -> None:
    import sys

    level_name = os.environ.get("SYSML_BACKEND_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    health_filter = _HealthAccessLogFilter()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(health_filter)
    fmt = "%(asctime)s %(levelname)-8s %(message)s"
    if sys.stdout.isatty():
        handler.setFormatter(_ColorFormatter(fmt))
    else:
        handler.setFormatter(logging.Formatter(fmt))
    logging.root.setLevel(level)
    logging.root.handlers = [handler]
    logging.getLogger("uvicorn.access").addFilter(health_filter)


def main() -> None:
    _configure_logging()
    config = load_config()
    server = create_server(config)
    logger.info("[sysml-backend] listening on http://%s:%s", config.host, config.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("[sysml-backend] shutdown requested")
    finally:
        server.server_close()
