from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

if TYPE_CHECKING:
    from .monitoring import MonitoringCycle


class AnalysisStore:
    def __init__(self, database_url: str) -> None:
        import psycopg

        self.database_url = database_url
        self._psycopg = psycopg
        self._lock = threading.RLock()
        self._connection: Any | None = None

    def save_project(self, project: dict[str, Any]) -> None:
        slug = str(project.get("slug") or "").strip()
        name = str(project.get("name") or slug).strip()
        if not slug or not name:
            raise ValueError("Project slug and name are required.")
        repositories = project.get("repositories", [])
        repositories = repositories if isinstance(repositories, list) else []

        with self._lock:
            connection = self._connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        insert into projects (slug, name, workspace_path, updated_at)
                        values (%s, %s, %s, now())
                        on conflict (slug) do update set
                          name = excluded.name,
                          workspace_path = excluded.workspace_path,
                          updated_at = now()
                        """,
                        (slug, name, str(project.get("path") or slug)),
                    )
                    cursor.execute(
                        "delete from project_repositories where project_slug = %s",
                        (slug,),
                    )
                    for repository in repositories:
                        if not isinstance(repository, dict):
                            continue
                        git = repository.get("git", {})
                        git = git if isinstance(git, dict) else {}
                        cursor.execute(
                            """
                            insert into project_repositories (
                              project_slug, name, url, role, branch, path, status,
                              git, error, updated_at
                            )
                            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                            """,
                            (
                                slug,
                                str(repository.get("name") or ""),
                                str(
                                    repository.get("url")
                                    or git.get("remote_url")
                                    or git.get("remoteUrl")
                                    or ""
                                ),
                                str(repository.get("role") or "unknown"),
                                repository.get("branch"),
                                str(repository.get("path") or git.get("path") or ""),
                                str(repository.get("status") or "unknown"),
                                Jsonb(git),
                                repository.get("error") or git.get("error"),
                            ),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select
                      p.slug, p.name, p.workspace_path, p.created_at, p.updated_at,
                      coalesce(
                        jsonb_agg(
                          jsonb_build_object(
                            'name', r.name,
                            'url', r.url,
                            'role', r.role,
                            'branch', r.branch,
                            'path', r.path,
                            'status', r.status,
                            'git', r.git,
                            'error', r.error
                          )
                          order by r.name
                        ) filter (where r.id is not null),
                        '[]'::jsonb
                      ) as repositories
                    from projects p
                    left join project_repositories r on r.project_slug = p.slug
                    group by p.slug
                    order by lower(p.name)
                    """
                )
                rows = cursor.fetchall()
            connection.commit()
        return [_project_row_to_json(row) for row in rows]

    def get_project(self, slug: str) -> dict[str, Any] | None:
        return next(
            (
                project
                for project in self.list_projects()
                if project.get("slug") == slug
            ),
            None,
        )

    def save_cycle(self, project_slug: str, cycle: "MonitoringCycle") -> None:
        with self._lock:
            connection = self._connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        insert into projects (slug, name, workspace_path, updated_at)
                        values (%s, %s, %s, now())
                        on conflict (slug) do update set
                          name = excluded.name,
                          updated_at = now()
                        """,
                        (project_slug, cycle.package_name, project_slug),
                    )
                    cursor.execute(
                        """
                        insert into analysis_runs (
                          run_id, project_slug, project_name, trigger, status,
                          started_at, completed_at, repository_count, changed_count,
                          unchanged_count, repositories, opencode_session_id,
                          opencode_cost, input_tokens, output_tokens, total_tokens,
                          opencode_usage
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (run_id) do update set
                          project_slug = excluded.project_slug,
                          project_name = excluded.project_name,
                          trigger = excluded.trigger,
                          status = excluded.status,
                          started_at = excluded.started_at,
                          completed_at = excluded.completed_at,
                          repository_count = excluded.repository_count,
                          changed_count = excluded.changed_count,
                          unchanged_count = excluded.unchanged_count,
                          repositories = excluded.repositories,
                          opencode_session_id = excluded.opencode_session_id,
                          opencode_cost = excluded.opencode_cost,
                          input_tokens = excluded.input_tokens,
                          output_tokens = excluded.output_tokens,
                          total_tokens = excluded.total_tokens,
                          opencode_usage = excluded.opencode_usage
                        """,
                        (
                            cycle.run_id,
                            project_slug,
                            cycle.package_name,
                            cycle.trigger,
                            cycle.status,
                            _parse_datetime(cycle.started_at),
                            _parse_datetime(cycle.completed_at),
                            cycle.repository_count,
                            cycle.changed_count,
                            cycle.unchanged_count,
                            Jsonb(cycle.repositories),
                            cycle.opencode_session_id,
                            _usage_float(cycle.opencode_usage, "cost"),
                            _usage_int(cycle.opencode_usage, "inputTokens"),
                            _usage_int(cycle.opencode_usage, "outputTokens"),
                            _usage_int(cycle.opencode_usage, "totalTokens"),
                            Jsonb(cycle.opencode_usage or {}),
                        ),
                    )
                    for artifact in cycle.artifacts:
                        cursor.execute(
                            """
                            insert into run_artifacts (
                              run_id, pass_id, suite_model, suite_evidence,
                              unresolved_services
                            )
                            values (%s, %s, %s, %s, %s)
                            on conflict (run_id, pass_id) do update set
                              suite_model = excluded.suite_model,
                              suite_evidence = excluded.suite_evidence,
                              unresolved_services = excluded.unresolved_services
                            """,
                            (
                                cycle.run_id,
                                artifact.pass_id,
                                _read_text(artifact.suite_model_path),
                                Jsonb(_read_json(artifact.suite_evidence_path)),
                                _read_text(artifact.unresolved_services_path),
                            ),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def list_runs(self, project_slug: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._connect()
            where = "where r.project_slug = %s" if project_slug else ""
            params = (project_slug,) if project_slug else ()
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    select
                      r.run_id, r.project_slug, r.project_name, r.trigger, r.status,
                      r.started_at, r.completed_at, r.repository_count,
                      r.changed_count, r.unchanged_count, r.repositories,
                      r.opencode_session_id, r.opencode_cost, r.input_tokens,
                      r.output_tokens, r.total_tokens, r.opencode_usage,
                      coalesce(
                        jsonb_agg(
                          jsonb_build_object('passId', a.pass_id)
                          order by a.created_at
                        ) filter (where a.id is not null),
                        '[]'::jsonb
                      ) as artifacts
                    from analysis_runs r
                    left join run_artifacts a on a.run_id = r.run_id
                    {where}
                    group by r.run_id
                    order by r.started_at desc
                    """,
                    params,
                )
                rows = cursor.fetchall()
            connection.commit()
        return [_run_row_to_json(row) for row in rows]

    def changes_for_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        repositories = run.get("repositories", [])
        repositories = repositories if isinstance(repositories, list) else []
        return {
            "runId": run_id,
            "packageName": run.get("packageName"),
            "projectName": run.get("projectName"),
            "changedCount": run.get("changedCount", 0),
            "unchangedCount": run.get("unchangedCount", 0),
            "repositories": repositories,
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        runs = self.list_runs()
        return next((run for run in runs if run.get("runId") == run_id), None)

    def read_artifact(
        self, run_id: str, pass_id: str, artifact_name: str
    ) -> tuple[bytes, str] | None:
        column = {
            "suite-model.sysml": ("suite_model", "text/plain; charset=utf-8"),
            "suite-evidence.json": ("suite_evidence", "application/json"),
            "unresolved-services.md": (
                "unresolved_services",
                "text/markdown; charset=utf-8",
            ),
        }.get(artifact_name)
        if column is None:
            return None
        column_name, content_type = column
        with self._lock:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    select {column_name}
                    from run_artifacts
                    where run_id = %s and pass_id = %s
                    """,
                    (run_id, pass_id),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            return None
        value = row[0]
        if artifact_name == "suite-evidence.json":
            body = json.dumps(value, indent=2).encode("utf-8")
        else:
            body = str(value).encode("utf-8")
        return body, content_type

    def rename_project(self, slug: str, name: str) -> dict[str, Any]:
        with self._lock:
            connection = self._connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update projects
                        set name = %s, updated_at = now()
                        where slug = %s
                        """,
                        (name, slug),
                    )
                    if cursor.rowcount == 0:
                        raise ValueError(f"Project '{slug}' was not found.")
                    cursor.execute(
                        """
                        update analysis_runs
                        set project_name = %s
                        where project_slug = %s
                        """,
                        (name, slug),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        project = self.get_project(slug)
        if project is None:
            raise ValueError(f"Project '{slug}' was not found.")
        return project

    def delete_project(self, slug: str) -> None:
        with self._lock:
            connection = self._connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "select run_id from analysis_runs where project_slug = %s",
                        (slug,),
                    )
                    run_ids = [row[0] for row in cursor.fetchall()]
                    if run_ids:
                        cursor.execute(
                            "delete from run_events where run_id = any(%s)",
                            (run_ids,),
                        )
                    cursor.execute(
                        "delete from analysis_runs where project_slug = %s",
                        (slug,),
                    )
                    cursor.execute("delete from projects where slug = %s", (slug,))
                    if cursor.rowcount == 0:
                        raise ValueError(f"Project '{slug}' was not found.")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _connect(self) -> Any:
        if self._connection is None or self._connection.closed:
            self._connection = self._psycopg.connect(self.database_url)
        return self._connection


def _run_row_to_json(row: tuple[Any, ...]) -> dict[str, Any]:
    repositories = row[10]
    repositories = repositories if isinstance(repositories, list) else []
    usage = row[16]
    usage = usage if isinstance(usage, dict) else {}
    artifacts = row[17]
    artifacts = artifacts if isinstance(artifacts, list) else []
    return {
        "runId": row[0],
        "run_id": row[0],
        "projectSlug": row[1],
        "projectName": row[2],
        "packageName": row[2],
        "trigger": row[3],
        "status": row[4],
        "startedAt": _iso(row[5]),
        "started_at": _iso(row[5]),
        "completedAt": _iso(row[6]),
        "completed_at": _iso(row[6]),
        "repositoryCount": row[7],
        "changedCount": row[8],
        "changed_count": row[8],
        "unchangedCount": row[9],
        "unchanged_count": row[9],
        "repositories": repositories,
        "artifacts": artifacts,
        "opencodeSessionId": row[11],
        "opencode_session_id": row[11],
        "opencodeCost": row[12],
        "opencode_cost": row[12],
        "inputTokens": row[13],
        "input_tokens": row[13],
        "outputTokens": row[14],
        "output_tokens": row[14],
        "totalTokens": row[15],
        "total_tokens": row[15],
        "opencodeUsage": usage,
        "opencode_usage": usage,
    }


def _project_row_to_json(row: tuple[Any, ...]) -> dict[str, Any]:
    repositories = row[5]
    repositories = repositories if isinstance(repositories, list) else []
    return {
        "slug": row[0],
        "name": row[1],
        "path": row[2],
        "createdAt": _iso(row[3]),
        "updatedAt": _iso(row[4]),
        "repositories": repositories,
    }


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _read_json(path: str) -> dict[str, Any]:
    value = json.loads(_read_text(path))
    return value if isinstance(value, dict) else {}


def _usage_int(usage: dict[str, Any] | None, key: str) -> int | None:
    if not usage:
        return None
    value = usage.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _usage_float(usage: dict[str, Any] | None, key: str) -> float | None:
    if not usage:
        return None
    value = usage.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
