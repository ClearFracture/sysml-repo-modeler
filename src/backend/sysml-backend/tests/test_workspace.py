from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sysml_backend.services.monitoring import (  # noqa: E402
    MonitoringService,
    MonitorInProgressError,
)
from sysml_backend.services.projects import ProjectRegistry  # noqa: E402
from sysml_backend.services.repository_importer import (  # noqa: E402
    RepositoryImportResult,
    _repo_target,
    _store_path,
)
from sysml_backend.services.storage import ScopedDocumentStore  # noqa: E402
from sysml_backend.services.workspace import (  # noqa: E402
    WorkspaceManager,
    to_abs,
    to_rel,
    workspace_for,
)
from sysml_backend.utils.git_metadata import RepositoryMetadata  # noqa: E402


def _manager(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "packages", _DictStore())


# ---- workspace layout ------------------------------------------------------


def test_workspace_for_builds_nested_dirs(tmp_path):
    workspace = workspace_for(tmp_path / "packages", "demo")
    assert workspace.repos_dir == workspace.root / "repos"
    assert workspace.runs_dir == workspace.root / "runs"
    assert workspace.data_dir == workspace.root / "data"


def test_to_rel_and_to_abs_roundtrip(tmp_path):
    workspace = workspace_for(tmp_path / "packages", "demo")
    abs_path = workspace.repos_dir / "source" / "foo"
    rel = to_rel(workspace, abs_path)
    assert rel == "repos/source/foo"
    assert to_abs(workspace, rel) == abs_path.resolve()


def test_to_rel_passes_through_external_absolute_path(tmp_path):
    workspace = workspace_for(tmp_path / "packages", "demo")
    outside = (tmp_path / "elsewhere" / "repo").resolve()
    # A path outside the workspace round-trips unchanged.
    assert to_abs(workspace, to_rel(workspace, outside)) == outside


def test_repo_target_no_double_repos(tmp_path):
    base = (tmp_path / "packages" / "demo").resolve()
    # source/service/unknown sit directly under repos/; only argo/infra/docs nest.
    assert _repo_target(base, "source", "svc") == base / "repos" / "svc"
    assert _repo_target(base, "service", "svc") == base / "repos" / "svc"
    assert _repo_target(base, "argo", "apps") == base / "repos" / "argo" / "apps"
    assert _repo_target(base, "docs", "guide") == base / "repos" / "docs" / "guide"


def test_store_path_relative_inside_absolute_outside(tmp_path):
    base = (tmp_path / "packages" / "demo").resolve()
    inside = base / "repos" / "svc"
    assert _store_path(base, inside) == "repos/svc"
    outside = (tmp_path / "other").resolve()
    assert _store_path(base, outside) == str(outside)


# ---- per-package isolation -------------------------------------------------


def test_stores_are_isolated_per_slug(tmp_path):
    manager = _manager(tmp_path)
    manager.store("alpha").write("doc", {"v": 1})
    manager.store("beta").write("doc", {"v": 2})
    assert manager.store("alpha").read("doc", {}) == {"v": 1}
    assert manager.store("beta").read("doc", {}) == {"v": 2}
    assert set(manager.list_slugs()) == {"alpha", "beta"}


def test_scoped_document_store_namespaces_names():
    inner = _DictStore()
    scoped = ScopedDocumentStore(inner, "alpha")
    scoped.write("doc", {"v": 1})
    assert "alpha/doc" in inner.data
    assert scoped.read("doc", {}) == {"v": 1}


# ---- project registry ------------------------------------------------------


def test_project_registry_per_package(tmp_path):
    manager = _manager(tmp_path)
    registry = ProjectRegistry(manager)
    project = registry.create_project("Example System")
    assert project.slug == "example-system"

    # create is idempotent and findable by name or slug.
    assert registry.create_project("Example System").slug == "example-system"
    assert registry.get_project("example-system") is not None
    assert registry.get_project("Example System") is not None
    assert [p["slug"] for p in registry.list_projects()] == ["example-system"]

    result = RepositoryImportResult(
        name="svc",
        url="https://example/svc.git",
        role="source",
        path="repos/svc",
        status="imported",
        git=RepositoryMetadata(
            path="repos/svc",
            is_git_repository=True,
            commit=None,
            branch=None,
            remote_url=None,
            dirty=False,
        ),
    )
    updated = registry.record_import_results("example-system", [result])
    assert updated.repositories[0]["path"] == "repos/svc"


# ---- concurrency: one active run globally ----------------------------------


def test_one_active_run_global_lock(tmp_path):
    manager = _manager(tmp_path)
    service = MonitoringService(
        manager, None, None, None, "packages", _MissingAnalysisStore()
    )
    assert service._claim("alpha") is True
    assert service._claim("alpha") is False  # already in flight
    assert service._claim("beta") is False  # one active run globally
    service._release("alpha")
    assert service._claim("beta") is True


def test_start_cycle_rejects_when_in_flight(tmp_path):
    manager = _manager(tmp_path)
    service = MonitoringService(
        manager,
        _MissingPackages(),
        None,
        None,
        "packages",
        _MissingAnalysisStore(),
    )
    service._claim("demo")  # simulate an in-flight run for slug "demo"
    try:
        service.start_cycle("demo")
        assert False, "expected MonitorInProgressError"
    except MonitorInProgressError as error:
        assert error.package_name == "demo"


class _DictStore:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    def read(self, name, default):
        return self.data.get(name, default)

    def write(self, name, payload):
        self.data[name] = payload

    def mutate(self, name, default, update):
        updated = update(self.data.get(name, default))
        self.data[name] = updated
        return updated


class _MissingPackages:
    def get_package(self, name):  # noqa: ARG002
        # A package that exists for the lock but never reaches registry lookup,
        # because the in-flight claim short-circuits first.
        return {"repositories": []}


class _MissingAnalysisStore:
    def list_runs(self, project_slug=None):  # noqa: ANN001, ARG002
        return []

    def changes_for_run(self, run_id):  # noqa: ANN001, ARG002
        return None

    def save_cycle(self, project_slug, cycle):  # noqa: ANN001, ARG002
        raise AssertionError("save_cycle should not be reached")
