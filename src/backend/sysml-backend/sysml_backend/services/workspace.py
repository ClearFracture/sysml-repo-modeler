"""Per-project workspace layout and store resolution.

Every project owns a self-contained folder::

    <projects_root>/<slug>/
        repos/   git clones
        runs/    run artifacts (runs/<run_id>/<pass>/...)

Repository paths are persisted *relative* to the package root and resolved to
absolute only when the filesystem needs them (git, inspection, the OpenCode
prompt). ``WorkspaceManager`` resolves a slug to its directories and to a
``DocumentStore`` scoped to that project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .storage import DocumentStore, ScopedDocumentStore


def slugify(value: str) -> str:
    """Lowercase, filesystem-safe slug used to name a package's folder."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "-", value.strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip(".-")
    return cleaned


@dataclass(frozen=True)
class PackageWorkspace:
    slug: str
    root: Path
    repos_dir: Path
    runs_dir: Path
    data_dir: Path

    def ensure(self) -> "PackageWorkspace":
        for directory in (self.root, self.repos_dir, self.runs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def workspace_for(projects_root: Path, slug: str) -> PackageWorkspace:
    root = (projects_root / slug).resolve()
    return PackageWorkspace(
        slug=slug,
        root=root,
        repos_dir=root / "repos",
        runs_dir=root / "runs",
        data_dir=root / "data",
    )


def to_rel(workspace: PackageWorkspace, path: str | Path) -> str:
    """Path relative to the package root as a POSIX string.

    Paths outside the workspace (e.g. an externally registered package) are
    returned unchanged so the round-trip through :func:`to_abs` is lossless.
    """
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(workspace.root).as_posix()
    except ValueError:
        return candidate.as_posix()


def to_abs(workspace: PackageWorkspace, rel: str | Path) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute():
        return candidate
    return (workspace.root / candidate).resolve()


class WorkspaceManager:
    """Resolves slugs to directories and scoped Postgres document stores."""

    def __init__(
        self,
        projects_root: Path,
        global_store: DocumentStore,
    ) -> None:
        self.projects_root = projects_root
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.global_store = global_store
        self._stores: dict[str, DocumentStore] = {}

    def workspace(self, slug: str) -> PackageWorkspace:
        return workspace_for(self.projects_root, slug).ensure()

    def store(self, slug: str) -> DocumentStore:
        if slug not in self._stores:
            self.workspace(slug)
            self._stores[slug] = ScopedDocumentStore(self.global_store, slug)
        return self._stores[slug]

    def list_slugs(self) -> list[str]:
        """Every project folder, sorted."""
        if not self.projects_root.exists():
            return []
        slugs = [
            child.name
            for child in sorted(
                self.projects_root.iterdir(), key=lambda item: item.name.lower()
            )
            if child.is_dir()
        ]
        return slugs
