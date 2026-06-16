from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .projects import Project, ProjectRegistry
from .repository_importer import (
    GitProgressCallback,
    RepositoryImporter,
    RepositoryImportRequest,
    RepositoryImportResult,
)


class ProjectWorkspaceService:
    """Backend-owned project/repository filesystem operations."""

    def __init__(self, project_registry: ProjectRegistry, scratch_path: Path) -> None:
        self.project_registry = project_registry
        self.scratch_path = scratch_path

    def create_project(self, name: str) -> Project:
        return self.project_registry.create_project(name)

    def rename_project(self, name_or_slug: str, name: str) -> Project:
        return self.project_registry.rename_project(name_or_slug, name)

    def import_repositories(
        self,
        name_or_slug: str,
        repositories: list[RepositoryImportRequest],
    ) -> list[RepositoryImportResult]:
        workspace = self.project_registry.workspace_for_project(name_or_slug).ensure()
        results: list[RepositoryImportResult] = []
        for repository in repositories:
            importer = RepositoryImporter(
                self.scratch_path,
                github_token=repository.github_token,
            )
            results.extend(importer.import_repositories(workspace.root, [repository]))
        return results

    def refresh_repositories(
        self,
        name_or_slug: str,
        repositories: list[dict[str, Any]],
        github_token: str | None = None,
    ) -> list[RepositoryImportResult]:
        workspace = self.project_registry.workspace_for_project(name_or_slug).ensure()
        importer = RepositoryImporter(self.scratch_path, github_token=github_token)
        return importer.refresh_repositories(workspace.root, repositories)

    def sync_repositories(
        self,
        name_or_slug: str,
        repositories: list[dict[str, Any]],
        github_token: str | None = None,
        progress_callback: GitProgressCallback | None = None,
    ) -> list[RepositoryImportResult]:
        workspace = self.project_registry.workspace_for_project(name_or_slug).ensure()
        importer = RepositoryImporter(
            self.scratch_path,
            github_token=github_token,
            progress_callback=progress_callback,
        )
        return importer.sync_repositories(workspace.root, repositories)

    def delete_repository(self, name_or_slug: str, repo_name: str) -> None:
        project = self.project_registry.get_project(name_or_slug)
        if project is None:
            raise ValueError(f"Project '{name_or_slug}' was not found.")

        workspace = self.project_registry.workspace_for_project(name_or_slug).ensure()
        repositories = project.get("repositories", [])
        repositories = repositories if isinstance(repositories, list) else []
        repository = next(
            (item for item in repositories if item.get("name") == repo_name),
            None,
        )
        if repository is None:
            raise ValueError(f"Repository '{repo_name}' was not found.")

        path = Path(str(repository.get("path") or ""))
        target = path if path.is_absolute() else (workspace.root / path)
        target = target.resolve()
        try:
            target.relative_to(workspace.repos_dir.resolve())
        except ValueError as error:
            raise ValueError(
                "Repository path is outside this project's repos folder."
            ) from error

        if target.exists():
            shutil.rmtree(target)

    def delete_project(self, name_or_slug: str) -> str:
        project = self.project_registry.get_project(name_or_slug)
        if project is None:
            raise ValueError(f"Project '{name_or_slug}' was not found.")

        workspace = self.project_registry.workspace_for_project(name_or_slug)
        target = workspace.root.resolve()
        root = self.project_registry.workspaces.projects_root.resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError("Project path is outside the workspace root.") from error

        if target.exists():
            shutil.rmtree(target)
        return str(project.get("slug") or name_or_slug)
