from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..utils import RepositoryMetadata, inspect_repository
from .workspace import WorkspaceManager, slugify

PACKAGE_DOC = "package"


@dataclass(frozen=True)
class PackageRegistration:
    name: str
    path: str
    repositories: list[RepositoryMetadata]


class PackageRegistry:
    """External package registrations persisted per-package in ``<slug>/data/package.json``.

    Unlike projects, a registered package points at an existing folder outside
    the workspace, so its repository paths stay absolute; only the registration
    metadata, runs, and events live under the package's workspace folder.
    """

    def __init__(self, workspaces: WorkspaceManager) -> None:
        self.workspaces = workspaces

    def list_packages(self) -> list[dict[str, Any]]:
        packages: list[dict[str, Any]] = []
        for slug in self.workspaces.list_slugs():
            package = self.workspaces.store(slug).read(PACKAGE_DOC, {})
            if isinstance(package, dict) and package.get("name"):
                packages.append(package)
        return packages

    def get_package(self, name: str) -> dict[str, Any] | None:
        slug = slugify(name)
        if not slug:
            return None
        package = self.workspaces.store(slug).read(PACKAGE_DOC, {})
        if isinstance(package, dict) and package.get("name"):
            return package
        return None

    def register_package(self, name: str, path: str) -> PackageRegistration:
        slug = slugify(name)
        if not slug:
            raise ValueError("Package name is required.")
        self.workspaces.workspace(slug)  # ensure the workspace folders exist
        package_path = Path(path).resolve()
        repositories = self.inspect_package_path(package_path)
        registration = PackageRegistration(
            name=name, path=str(package_path), repositories=repositories
        )
        self.workspaces.store(slug).write(
            PACKAGE_DOC, _registration_to_json(registration)
        )
        return registration

    def inspect_package_path(self, package_path: Path) -> list[RepositoryMetadata]:
        if not package_path.exists():
            return [inspect_repository(package_path)]

        repositories: list[RepositoryMetadata] = []
        for child in sorted(package_path.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            repositories.extend(self._inspect_repository_container(child))

        if not repositories:
            repositories.append(inspect_repository(package_path))

        return repositories

    def _inspect_repository_container(self, path: Path) -> list[RepositoryMetadata]:
        if (path / ".git").exists():
            return [inspect_repository(path)]

        repositories: list[RepositoryMetadata] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir() and (child / ".git").exists():
                repositories.append(inspect_repository(child))
        return repositories


def _registration_to_json(registration: PackageRegistration) -> dict[str, Any]:
    return {
        "name": registration.name,
        "path": registration.path,
        "repositories": [
            asdict(repository) for repository in registration.repositories
        ],
    }
