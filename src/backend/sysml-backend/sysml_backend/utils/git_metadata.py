from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepositoryMetadata:
    path: str
    is_git_repository: bool
    commit: str | None
    branch: str | None
    remote_url: str | None
    dirty: bool
    error: str | None = None


def inspect_repository(path: Path) -> RepositoryMetadata:
    resolved_path = path.resolve()
    if not resolved_path.exists():
        return RepositoryMetadata(
            path=str(resolved_path),
            is_git_repository=False,
            commit=None,
            branch=None,
            remote_url=None,
            dirty=False,
            error="path does not exist",
        )

    is_git = _git(resolved_path, "rev-parse", "--is-inside-work-tree")
    if is_git.returncode != 0 or is_git.stdout.strip() != "true":
        return RepositoryMetadata(
            path=str(resolved_path),
            is_git_repository=False,
            commit=None,
            branch=None,
            remote_url=None,
            dirty=False,
            error="not a git repository",
        )

    commit = _optional_git(resolved_path, "rev-parse", "HEAD")
    branch = _optional_git(resolved_path, "branch", "--show-current")
    remote_url = _optional_git(resolved_path, "remote", "get-url", "origin")
    status = _git(resolved_path, "status", "--porcelain")

    return RepositoryMetadata(
        path=str(resolved_path),
        is_git_repository=True,
        commit=commit,
        branch=branch,
        remote_url=remote_url,
        dirty=bool(status.stdout.strip()) if status.returncode == 0 else False,
        error=None if status.returncode == 0 else _clean_error(status.stderr),
    )


def _optional_git(path: Path, *args: str) -> str | None:
    result = _git(path, *args)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _clean_error(stderr: str) -> str | None:
    value = stderr.strip()
    return value or None
