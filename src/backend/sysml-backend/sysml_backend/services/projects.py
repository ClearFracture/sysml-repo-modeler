from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .repository_importer import RepositoryImportResult, import_result_to_json
from .workspace import (
    PackageWorkspace,
    WorkspaceManager,
    slugify,
    to_abs,
    workspace_for,
)

PROJECT_DOC = "project"

if TYPE_CHECKING:
    from .analysis_store import AnalysisStore


@dataclass(frozen=True)
class Project:
    name: str
    slug: str
    path: str
    repositories: list[dict[str, Any]]
    created_at: str
    updated_at: str


class ProjectRegistry:
    """Project metadata read from Postgres and annotated for this runtime."""

    def __init__(
        self,
        workspaces: WorkspaceManager,
        analysis_store: "AnalysisStore | None" = None,
    ) -> None:
        self.workspaces = workspaces
        self.analysis_store = analysis_store

    def list_projects(self) -> list[dict[str, Any]]:
        if self.analysis_store is not None:
            return [
                self._with_workspace_status(project)
                for project in self.analysis_store.list_projects()
            ]
        projects: list[dict[str, Any]] = []
        for slug in self.workspaces.list_slugs():
            project = self.workspaces.store(slug).read(PROJECT_DOC, {})
            if isinstance(project, dict) and project.get("name"):
                projects.append(self._with_workspace_status(project))
        return projects

    def get_project(self, name_or_slug: str) -> dict[str, Any] | None:
        slug = slugify(name_or_slug)
        if not slug:
            return None
        if self.analysis_store is not None:
            project = self.analysis_store.get_project(slug)
            return self._with_workspace_status(project) if project is not None else None
        project = self.workspaces.store(slug).read(PROJECT_DOC, {})
        if isinstance(project, dict) and project.get("name"):
            return self._with_workspace_status(project)
        return None

    def create_project(self, name: str, workspace_path: str | None = None) -> Project:
        slug = slugify(name)
        if not slug:
            raise ValueError("Project name is required.")
        if self.analysis_store is not None:
            existing = self.analysis_store.get_project(slug)
            if existing is not None:
                return _project_from_json(self._with_workspace_status(existing))
        workspace = self.workspaces.workspace(slug)
        now = _now()

        result: dict[str, Any] = {}

        def _upsert(document: dict[str, Any]) -> dict[str, Any]:
            if isinstance(document, dict) and document.get("name"):
                result["project"] = document
                return document
            project_json = _project_to_json(
                Project(
                    name=name,
                    slug=slug,
                    path=workspace_path or str(workspace.root),
                    repositories=[],
                    created_at=now,
                    updated_at=now,
                )
            )
            result["project"] = project_json
            return project_json

        self.workspaces.store(slug).mutate(PROJECT_DOC, {}, _upsert)
        return _project_from_json(result["project"])

    def record_import_results(
        self,
        name_or_slug: str,
        results: list[RepositoryImportResult],
    ) -> Project:
        def _update(project: dict[str, Any]) -> dict[str, Any]:
            result_names = {result.name for result in results}
            repositories = [
                repository
                for repository in _repositories(project)
                if repository.get("name") not in result_names
            ]
            repositories.extend(import_result_to_json(result) for result in results)
            return {**project, "repositories": repositories, "updatedAt": _now()}

        return self._update_project(name_or_slug, _update)

    def rename_project(self, name_or_slug: str, name: str) -> Project:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Project name is required.")

        def _update(project: dict[str, Any]) -> dict[str, Any]:
            return {**project, "name": clean_name, "updatedAt": _now()}

        return self._update_project(name_or_slug, _update)

    def remove_repository(self, name_or_slug: str, repo_name: str) -> Project:
        def _update(project: dict[str, Any]) -> dict[str, Any]:
            repositories = [
                repository
                for repository in _repositories(project)
                if repository.get("name") != repo_name
            ]
            return {**project, "repositories": repositories, "updatedAt": _now()}

        return self._update_project(name_or_slug, _update)

    def workspace_for_project(self, name_or_slug: str) -> PackageWorkspace:
        return self.workspaces.workspace(slugify(name_or_slug))

    def _update_project(
        self,
        name_or_slug: str,
        update: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> Project:
        slug = slugify(name_or_slug)
        result: dict[str, Any] = {}
        existing = (
            self.analysis_store.get_project(slug)
            if self.analysis_store is not None
            else None
        )

        def _mutate(document: dict[str, Any]) -> dict[str, Any]:
            current = (
                document
                if isinstance(document, dict) and document.get("name")
                else existing
            )
            if not isinstance(current, dict) or not current.get("name"):
                raise ValueError(f"Project '{name_or_slug}' was not found.")
            updated_project = update(current)
            result["project"] = updated_project
            return updated_project

        self.workspaces.store(slug).mutate(PROJECT_DOC, {}, _mutate)
        return _project_from_json(result["project"])

    def _with_workspace_status(self, project: dict[str, Any]) -> dict[str, Any]:
        slug = str(project.get("slug") or "")
        workspace = workspace_for(self.workspaces.projects_root, slug) if slug else None
        workspace_exists = workspace.root.exists() if workspace else False
        repositories = [
            _repository_with_workspace_status(workspace, repository)
            for repository in _repositories(project)
        ]
        return {
            **project,
            "path": str(workspace.root)
            if workspace
            else str(project.get("path") or ""),
            "workspace": {
                "available": workspace_exists,
                "path": str(workspace.root) if workspace else None,
            },
            "repositories": repositories,
        }


def _repositories(project: dict[str, Any]) -> list[dict[str, Any]]:
    repositories = project.get("repositories", [])
    return repositories if isinstance(repositories, list) else []


def _repository_with_workspace_status(
    workspace: PackageWorkspace | None, repository: dict[str, Any]
) -> dict[str, Any]:
    if workspace is None:
        return {**repository, "workspace": {"available": False, "path": None}}
    raw_path = repository.get("path")
    repo_path = to_abs(workspace, raw_path) if isinstance(raw_path, str) else None
    exists = repo_path.exists() if repo_path is not None else False
    readable = _is_readable_dir(repo_path) if exists else False
    return {
        **repository,
        "workspace": {
            "available": exists and readable,
            "path": str(repo_path) if repo_path is not None else None,
        },
    }


def _is_readable_dir(path: Path | None) -> bool:
    if path is None or not path.is_dir():
        return False
    try:
        next(path.iterdir(), None)
    except OSError:
        return False
    return True


def _project_from_json(project: dict[str, Any]) -> Project:
    return Project(
        name=str(project.get("name", "")),
        slug=str(project.get("slug", "")),
        path=str(project.get("path", "")),
        repositories=_repositories(project),
        created_at=str(project.get("createdAt", "")),
        updated_at=str(project.get("updatedAt", "")),
    )


def _project_to_json(project: Project) -> dict[str, Any]:
    return {
        "name": project.name,
        "slug": project.slug,
        "path": project.path,
        "repositories": project.repositories,
        "createdAt": project.created_at,
        "updatedAt": project.updated_at,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
