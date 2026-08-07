"""Business-logic layer: registries, stores, and orchestration services."""

from __future__ import annotations

from .analysis_store import AnalysisStore
from .artifacts import ArtifactSet, ArtifactWriter
from .monitoring import MonitoringService, MonitorInProgressError
from .opencode_client import OpenCodeClient, OpenCodeConfig, health_to_json
from .packages import PackageRegistration, PackageRegistry
from .project_workspace import ProjectWorkspaceService
from .projects import ProjectRegistry
from .repository_importer import (
    RepositoryImporter,
    RepositoryImportResult,
    import_request_from_json,
    import_result_to_json,
)
from .run_events import RunEventStore
from .storage import (
    DocumentStore,
    PostgresDocumentStore,
    ScopedDocumentStore,
    create_document_store,
)
from .sysml_prompts import (
    analysis_prompt,
    analysis_system_prompt,
    analysis_user_prompt,
    coverage_repair_prompt,
    enrichment_prompt,
    repair_prompt,
    repo_lines,
)
from .workspace import (
    PackageWorkspace,
    WorkspaceManager,
    slugify,
    to_abs,
    to_rel,
    workspace_for,
)

__all__ = [
    "ArtifactSet",
    "ArtifactWriter",
    "AnalysisStore",
    "MonitoringService",
    "MonitorInProgressError",
    "OpenCodeClient",
    "OpenCodeConfig",
    "health_to_json",
    "PackageRegistration",
    "PackageRegistry",
    "ProjectWorkspaceService",
    "ProjectRegistry",
    "RepositoryImporter",
    "RepositoryImportResult",
    "import_request_from_json",
    "import_result_to_json",
    "RunEventStore",
    "analysis_prompt",
    "analysis_system_prompt",
    "analysis_user_prompt",
    "coverage_repair_prompt",
    "enrichment_prompt",
    "repair_prompt",
    "repo_lines",
    "DocumentStore",
    "PostgresDocumentStore",
    "ScopedDocumentStore",
    "create_document_store",
    "PackageWorkspace",
    "WorkspaceManager",
    "slugify",
    "to_abs",
    "to_rel",
    "workspace_for",
]
