from __future__ import annotations

import logging
import os
import re
import stat
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..utils import RepositoryMetadata, inspect_repository

# Bounded fan-out for concurrent git clone/pull operations.
_MAX_PARALLEL = 8
_CLONE_DEPTH = "1"
_PROGRESS_RE = re.compile(r"(?P<stage>[A-Za-z][A-Za-z ]+):\s+(?P<percent>\d{1,3})%")

logger = logging.getLogger(__name__)

GitProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class RepositoryImportRequest:
    name: str
    url: str
    role: str = "unknown"
    branch: str | None = None
    github_token: str | None = None


@dataclass(frozen=True)
class RepositoryImportResult:
    name: str
    url: str
    role: str
    path: str
    status: str
    git: RepositoryMetadata
    branch: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stderr: str


class RepositoryImporter:
    def __init__(
        self,
        scratch_path: Path,
        github_token: str | None = None,
        progress_callback: GitProgressCallback | None = None,
    ) -> None:
        self.scratch_path = scratch_path
        self.github_token = github_token or None
        self.progress_callback = progress_callback

    def import_repositories(
        self,
        project_path: Path,
        repositories: list[RepositoryImportRequest],
    ) -> list[RepositoryImportResult]:
        base = project_path.resolve()
        environment = self._git_environment()
        jobs: list[Callable[[], RepositoryImportResult]] = []
        for repository in repositories:
            target_path = _repo_target(base, repository.role, repository.name)
            jobs.append(
                lambda repo=repository, path=target_path: self._import_repository(
                    repo, path, base, environment
                )
            )
        return self._run_parallel(jobs)

    def refresh_repositories(
        self,
        project_path: Path,
        repositories: list[dict[str, Any]],
    ) -> list[RepositoryImportResult]:
        base = project_path.resolve()
        environment = self._git_environment()
        jobs: list[Callable[[], RepositoryImportResult]] = []
        for repository in repositories:
            name, url, role, path, _branch = _repository_fields(repository, base)
            jobs.append(
                lambda n=name, u=url, r=role, p=path: self._refresh_repository(
                    n, u, r, p, base, environment
                )
            )
        return self._run_parallel(jobs)

    def sync_repositories(
        self,
        project_path: Path,
        repositories: list[dict[str, Any]],
    ) -> list[RepositoryImportResult]:
        """Ensure each repository is present and current.

        Clone when the working-tree path is missing, otherwise pull. Repositories
        are processed concurrently since each git invocation is I/O bound.
        """
        base = project_path.resolve()
        environment = self._git_environment()
        jobs: list[Callable[[], RepositoryImportResult]] = []
        for repository in repositories:
            name, url, role, path, branch = _repository_fields(repository, base)
            jobs.append(
                lambda n=name, u=url, r=role, p=path, b=branch: self._sync_repository(
                    n, u, r, p, b, base, environment
                )
            )
        return self._run_parallel(jobs)

    def _run_parallel(
        self,
        jobs: list[Callable[[], RepositoryImportResult]],
    ) -> list[RepositoryImportResult]:
        if not jobs:
            return []
        if len(jobs) == 1:
            return [jobs[0]()]
        with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL, len(jobs))) as pool:
            # pool.map preserves the input ordering of the results.
            return list(pool.map(lambda job: job(), jobs))

    def _import_repository(
        self,
        repository: RepositoryImportRequest,
        target_path: Path,
        base: Path,
        environment: dict[str, str],
    ) -> RepositoryImportResult:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path = _store_path(base, target_path)

        if target_path.exists():
            metadata = inspect_repository(target_path)
            return RepositoryImportResult(
                name=repository.name,
                url=repository.url,
                role=repository.role,
                path=stored_path,
                status="already_present",
                git=metadata,
                branch=repository.branch,
                error=metadata.error,
            )

        command = [
            "git",
            *_git_credential_config(self.github_token),
            "clone",
            "-c",
            "core.longpaths=true",
            "--depth",
            _CLONE_DEPTH,
            "--single-branch",
            "--progress",
            "--",
            repository.url,
            str(target_path),
        ]
        if repository.branch:
            command = [
                "git",
                *_git_credential_config(self.github_token),
                "clone",
                "-c",
                "core.longpaths=true",
                "--depth",
                _CLONE_DEPTH,
                "--single-branch",
                "--progress",
                "--branch",
                repository.branch,
                "--",
                repository.url,
                str(target_path),
            ]

        logger.info(
            "[git:clone] cloning %s into %s%s",
            _safe_remote_for_log(repository.url),
            stored_path,
            f" branch={repository.branch}" if repository.branch else "",
        )
        self._emit_progress(
            "clone_start",
            repository.name,
            remote=_safe_remote_for_log(repository.url),
            path=stored_path,
            branch=repository.branch,
        )
        started = time.monotonic()
        result = _run_git_command(
            command,
            environment=environment,
            operation="clone",
            subject=repository.name,
            token=self.github_token,
            progress_callback=self.progress_callback,
        )
        elapsed = time.monotonic() - started
        metadata = inspect_repository(target_path)

        if result.returncode != 0:
            error = (
                _clean_git_error(result.stderr, token=self.github_token)
                or "Git clone failed."
            )
            logger.warning(
                "[git:clone] failed %s into %s after %.1fs: %s",
                _safe_remote_for_log(repository.url),
                stored_path,
                elapsed,
                error,
            )
            self._emit_progress(
                "clone_failed",
                repository.name,
                remote=_safe_remote_for_log(repository.url),
                path=stored_path,
                elapsedSeconds=round(elapsed, 1),
                error=error,
            )
            return RepositoryImportResult(
                name=repository.name,
                url=repository.url,
                role=repository.role,
                path=stored_path,
                status="failed",
                git=metadata,
                branch=repository.branch,
                error=error,
            )

        logger.info(
            "[git:clone] cloned %s into %s at %s after %.1fs",
            _safe_remote_for_log(repository.url),
            stored_path,
            _short_commit(metadata.commit),
            elapsed,
        )
        self._emit_progress(
            "clone_complete",
            repository.name,
            remote=_safe_remote_for_log(repository.url),
            path=stored_path,
            commit=_short_commit(metadata.commit),
            elapsedSeconds=round(elapsed, 1),
        )
        return RepositoryImportResult(
            name=repository.name,
            url=repository.url,
            role=repository.role,
            path=stored_path,
            status="imported",
            git=metadata,
            branch=repository.branch,
            error=metadata.error,
        )

    def _refresh_repository(
        self,
        name: str,
        url: str,
        role: str,
        path: Path,
        base: Path,
        environment: dict[str, str],
    ) -> RepositoryImportResult:
        stored_path = _store_path(base, path)
        metadata = inspect_repository(path)
        if not metadata.is_git_repository:
            return RepositoryImportResult(
                name=name,
                url=url,
                role=role,
                path=stored_path,
                status="failed",
                git=metadata,
                error=metadata.error or "not a git repository",
            )

        logger.info("[git:pull] pulling %s at %s", name, stored_path)
        self._emit_progress("pull_start", name, path=stored_path)
        started = time.monotonic()
        result = _run_git_command(
            [
                "git",
                *_git_credential_config(self.github_token),
                "-C",
                str(path),
                "pull",
                "--ff-only",
                "--progress",
            ],
            environment=environment,
            operation="pull",
            subject=name,
            token=self.github_token,
            progress_callback=self.progress_callback,
        )
        elapsed = time.monotonic() - started
        refreshed_metadata = inspect_repository(path)
        if result.returncode != 0:
            error = (
                _clean_git_error(result.stderr, token=self.github_token)
                or "Git pull failed."
            )
            logger.warning(
                "[git:pull] failed %s at %s after %.1fs: %s",
                name,
                stored_path,
                elapsed,
                error,
            )
            self._emit_progress(
                "pull_failed",
                name,
                path=stored_path,
                elapsedSeconds=round(elapsed, 1),
                error=error,
            )
            return RepositoryImportResult(
                name=name,
                url=url,
                role=role,
                path=stored_path,
                status="failed",
                git=refreshed_metadata,
                error=error,
            )

        logger.info(
            "[git:pull] refreshed %s at %s commit=%s dirty=%s after %.1fs",
            name,
            stored_path,
            _short_commit(refreshed_metadata.commit),
            refreshed_metadata.dirty,
            elapsed,
        )
        self._emit_progress(
            "pull_complete",
            name,
            path=stored_path,
            commit=_short_commit(refreshed_metadata.commit),
            dirty=refreshed_metadata.dirty,
            elapsedSeconds=round(elapsed, 1),
        )
        return RepositoryImportResult(
            name=name,
            url=url,
            role=role,
            path=stored_path,
            status="refreshed",
            git=refreshed_metadata,
            error=refreshed_metadata.error,
        )

    def _sync_repository(
        self,
        name: str,
        url: str,
        role: str,
        path: Path,
        branch: str | None,
        base: Path,
        environment: dict[str, str],
    ) -> RepositoryImportResult:
        metadata = inspect_repository(path)
        if metadata.is_git_repository:
            return self._refresh_repository(name, url, role, path, base, environment)

        if not url:
            return RepositoryImportResult(
                name=name,
                url=url,
                role=role,
                path=_store_path(base, path),
                status="failed",
                git=metadata,
                error="repository URL is required to clone a missing path",
            )

        if path.exists():
            # Something non-git is occupying the path. Clear it only if it is an
            # empty directory; never clobber a path that has contents.
            try:
                path.rmdir()
            except OSError:
                return RepositoryImportResult(
                    name=name,
                    url=url,
                    role=role,
                    path=_store_path(base, path),
                    status="failed",
                    git=metadata,
                    error="path exists but is not a git repository",
                )

        request = RepositoryImportRequest(name=name, url=url, role=role, branch=branch)
        return self._import_repository(request, path, base, environment)

    def _git_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        if self.github_token:
            askpass_path = self._ensure_askpass_script()
            environment["GIT_ASKPASS"] = str(askpass_path)
            environment["GITHUB_TOKEN"] = self.github_token
        return environment

    def _ensure_askpass_script(self) -> Path:
        self.scratch_path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            askpass_path = self.scratch_path / "git-askpass.cmd"
            askpass_path.write_text(
                "@echo off\n"
                'echo %1 | findstr /I "Username" >nul\n'
                "if %errorlevel%==0 (\n"
                "  echo x-access-token\n"
                ") else (\n"
                "  echo %GITHUB_TOKEN%\n"
                ")\n",
                encoding="utf-8",
            )
            return askpass_path

        askpass_path = self.scratch_path / "git-askpass.sh"
        askpass_path.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  *Username*|*username*) printf '%s\\n' 'x-access-token' ;;\n"
            "  *) printf '%s\\n' \"$GITHUB_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass_path.chmod(askpass_path.stat().st_mode | stat.S_IXUSR)
        return askpass_path

    def _emit_progress(
        self,
        phase: str,
        repository: str,
        **payload: Any,
    ) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(
            {
                "phase": phase,
                "repository": repository,
                **payload,
            }
        )


def import_request_from_json(payload: dict[str, Any]) -> RepositoryImportRequest:
    name = payload.get("name")
    url = payload.get("url")
    role = payload.get("role", "unknown")
    branch = payload.get("branch")
    github_token = payload.get("githubToken") or payload.get("token")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Repository URL is required.")
    if not isinstance(role, str) or not role.strip():
        raise ValueError("Repository role is required.")
    if branch is not None and not isinstance(branch, str):
        raise ValueError("Repository branch must be a string.")
    if github_token is not None and not isinstance(github_token, str):
        raise ValueError("GitHub token must be a string.")
    resolved_url = _github_url(url.strip())
    resolved_name = (
        name.strip()
        if isinstance(name, str) and name.strip()
        else _name_from_url(resolved_url)
    )
    return RepositoryImportRequest(
        name=resolved_name,
        url=resolved_url,
        role=role.strip().lower(),
        branch=branch.strip() if isinstance(branch, str) and branch.strip() else None,
        github_token=github_token.strip()
        if isinstance(github_token, str) and github_token.strip()
        else None,
    )


def import_result_to_json(result: RepositoryImportResult) -> dict[str, Any]:
    payload = {
        "name": result.name,
        "url": result.url,
        "role": result.role,
        "path": result.path,
        "status": result.status,
        "git": asdict(result.git),
        "error": result.error,
    }
    if result.branch:
        payload["branch"] = result.branch
    return payload


def _repository_fields(
    repository: dict[str, Any], base: Path
) -> tuple[str, str, str, Path, str | None]:
    raw_path = Path(str(repository.get("path", "")))
    path = raw_path if raw_path.is_absolute() else (base / raw_path)
    name = str(repository.get("name") or raw_path.name or "repository")
    url = str(repository.get("url") or "")
    role = str(repository.get("role") or "unknown")
    # Only a branch explicitly carried on the repository record should constrain
    # a future clone. Observed Git metadata lives under ``git.branch`` and must
    # not override the remote default branch.
    branch = repository.get("branch")
    branch = branch.strip() if isinstance(branch, str) and branch.strip() else None
    git = repository.get("git", {})
    git = git if isinstance(git, dict) else {}
    observed_branch = git.get("branch")
    observed_branch = (
        observed_branch.strip()
        if isinstance(observed_branch, str) and observed_branch.strip()
        else None
    )
    if branch == observed_branch:
        branch = None
    return name, url, role, path, branch


def _store_path(base: Path, target: Path) -> str:
    """Path stored in project.json: relative to the package root, POSIX-style.

    Paths outside the package root (externally registered repos) are stored
    absolute so resolution stays lossless.
    """
    try:
        return target.resolve().relative_to(base).as_posix()
    except ValueError:
        return str(target.resolve())


def _repo_target(base: Path, role: str, name: str) -> Path:
    """Clone destination under the package's ``repos/`` root.

    All clones live under ``repos/``; ``argo``/``infra``/``docs`` get a role
    subdir, while ``source``/``service``/unknown sit directly under ``repos/``
    (so a source repo is ``repos/<name>``, not ``repos/repos/<name>``).
    """
    target = base / "repos"
    role_dir = _role_directory(role)
    if role_dir:
        target = target / role_dir
    return target / _safe_name(name)


def _role_directory(role: str) -> str:
    if role == "argo":
        return "argo"
    if role in {"infra", "docs"}:
        return role
    return ""


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip(".-")
    return cleaned or "repository"


def _name_from_url(url: str) -> str:
    trimmed_url = url.strip().rstrip("/\\")
    last_segment = re.split(r"[/\\:]", trimmed_url)[-1]
    name = re.sub(r"\.git$", "", last_segment, flags=re.IGNORECASE)
    return _safe_name(name)


def _github_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.hostname == "github.com":
        return value
    if re.match(r"^git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$", value):
        return value
    raise ValueError("Only GitHub repository URLs are supported.")


def _git_credential_config(token: str | None) -> list[str]:
    config = ["-c", "credential.helper="]
    if token:
        config.extend(["-c", "credential.username=x-access-token"])
    return config


def _run_git_command(
    command: list[str],
    *,
    environment: dict[str, str],
    operation: str,
    subject: str,
    token: str | None,
    progress_callback: GitProgressCallback | None,
) -> GitCommandResult:
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        bufsize=1,
    )
    stderr_lines: list[str] = []
    progress_by_stage: dict[str, int] = {}
    buffer: list[str] = []

    if process.stderr is not None:
        while True:
            char = process.stderr.read(1)
            if not char:
                break
            if char in {"\r", "\n"}:
                _consume_git_stderr_line(
                    "".join(buffer),
                    operation=operation,
                    subject=subject,
                    token=token,
                    stderr_lines=stderr_lines,
                    progress_by_stage=progress_by_stage,
                    progress_callback=progress_callback,
                )
                buffer.clear()
            else:
                buffer.append(char)

    if buffer:
        _consume_git_stderr_line(
            "".join(buffer),
            operation=operation,
            subject=subject,
            token=token,
            stderr_lines=stderr_lines,
            progress_by_stage=progress_by_stage,
            progress_callback=progress_callback,
        )

    return GitCommandResult(
        returncode=process.wait(),
        stderr="\n".join(stderr_lines),
    )


def _consume_git_stderr_line(
    line: str,
    *,
    operation: str,
    subject: str,
    token: str | None,
    stderr_lines: list[str],
    progress_by_stage: dict[str, int],
    progress_callback: GitProgressCallback | None,
) -> None:
    clean_line = _clean_log_line(line, token=token)
    if not clean_line:
        return
    is_progress = _log_git_progress(
        clean_line,
        operation=operation,
        subject=subject,
        progress_by_stage=progress_by_stage,
        progress_callback=progress_callback,
    )
    if not is_progress and not _is_git_noise_line(clean_line):
        stderr_lines.append(clean_line)


def _log_git_progress(
    line: str,
    *,
    operation: str,
    subject: str,
    progress_by_stage: dict[str, int],
    progress_callback: GitProgressCallback | None,
) -> bool:
    match = _PROGRESS_RE.search(line)
    if match is None:
        return False
    stage = " ".join(match.group("stage").split())
    percent = min(100, int(match.group("percent")))
    previous = progress_by_stage.get(stage)
    if previous is not None and percent < 100 and percent - previous < 10:
        return True
    progress_by_stage[stage] = percent
    logger.info("[git:%s] %s %s %d%%", operation, subject, stage, percent)
    if progress_callback is not None:
        progress_callback(
            {
                "phase": f"{operation}_progress",
                "repository": subject,
                "stage": stage,
                "percent": percent,
            }
        )
    return True


def _safe_remote_for_log(url: str) -> str:
    if not url:
        return "<missing remote>"
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        path = parsed.path or ""
        return f"{parsed.scheme}://{host}{path}"
    return url


def _short_commit(commit: str | None) -> str:
    return commit[:12] if commit else "unknown"


def _clean_log_line(value: str, *, token: str | None = None) -> str:
    cleaned = value.strip()
    if token:
        cleaned = cleaned.replace(token, "<redacted>")
    return cleaned


def _is_git_noise_line(value: str) -> bool:
    line = value.strip()
    if not line:
        return True
    progress_prefixes = (
        "remote: Enumerating objects:",
        "remote: Counting objects:",
        "remote: Compressing objects:",
        "remote: Total ",
        "Receiving objects:",
        "Resolving deltas:",
        "Updating files:",
        "Filtering content:",
    )
    if line.startswith(progress_prefixes):
        return True
    if line.startswith("From ") or line.startswith(" * branch "):
        return True
    if line in {"Already up to date.", "Already up-to-date."}:
        return True
    return False


def _clean_git_error(stderr: str, *, token: str | None = None) -> str | None:
    value = _clean_log_line(stderr, token=token)
    if not value:
        return None
    if _is_missing_username_error(value):
        return "\n".join(
            [
                value,
                "Git could not obtain HTTPS credentials. Enter a GitHub token before importing/syncing this private repository.",
            ]
        )
    if _is_github_auth_error(value):
        value = "\n".join(
            [
                value,
                "GitHub rejected the supplied token. For private repositories, verify the token has repository contents read access and is authorized for the organization/SSO.",
            ]
        )
    return value


def _is_github_auth_error(value: str) -> bool:
    lowered = value.lower()
    return "github.com" in lowered and (
        "authentication failed" in lowered
        or "invalid username or token" in lowered
        or "password authentication is not supported" in lowered
    )


def _is_missing_username_error(value: str) -> bool:
    lowered = value.lower()
    return "could not read username" in lowered and "github.com" in lowered
