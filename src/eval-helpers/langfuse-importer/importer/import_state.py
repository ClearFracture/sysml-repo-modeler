from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScanRecord:
    run_id: str
    project_slug: str | None
    project_name: str | None
    scan_status: str | None
    telemetry_export_status: str | None
    started_at: str | None
    completed_at: str | None
    exported_at: str | None
    bundle_schema_version: str | None
    bundle_fingerprint: str | None
    scan_version: str | None
    opencode_session_id: str | None
    last_refreshed_at: str | None
    last_pushed_at: str | None
    pushed_bundle_fingerprint: str | None
    push_status: str | None
    push_error: str | None
    langfuse_session_id: str | None

    def needs_push(self) -> bool:
        if self.telemetry_export_status != "completed":
            return False
        return self.push_status in {None, "failed", "stale"}

    def to_json(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "projectSlug": self.project_slug,
            "projectName": self.project_name,
            "scanStatus": self.scan_status,
            "telemetryExportStatus": self.telemetry_export_status,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "exportedAt": self.exported_at,
            "bundleSchemaVersion": self.bundle_schema_version,
            "bundleFingerprint": self.bundle_fingerprint,
            "scanVersion": self.scan_version,
            "opencodeSessionId": self.opencode_session_id,
            "lastRefreshedAt": self.last_refreshed_at,
            "lastPushedAt": self.last_pushed_at,
            "pushedBundleFingerprint": self.pushed_bundle_fingerprint,
            "pushStatus": self.push_status,
            "pushError": self.push_error,
            "langfuseSessionId": self.langfuse_session_id,
            "needsPush": self.needs_push(),
        }


class ImportStateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.executescript(
                    """
                    create table if not exists scans (
                      run_id text primary key,
                      project_slug text,
                      project_name text,
                      scan_status text,
                      telemetry_export_status text,
                      started_at text,
                      completed_at text,
                      exported_at text,
                      bundle_schema_version text,
                      bundle_fingerprint text,
                      scan_version text,
                      opencode_session_id text,
                      last_refreshed_at text,
                      last_pushed_at text,
                      pushed_bundle_fingerprint text,
                      push_status text,
                      push_error text,
                      langfuse_session_id text
                    );
                    """
                )
                self._ensure_column(connection, "scans", "scan_version", "text")
                self._ensure_column(connection, "scans", "pushed_bundle_fingerprint", "text")
                connection.commit()

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row[1]
            for row in connection.execute(f"pragma table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(
                f"alter table {table} add column {column} {definition}"
            )

    def sync_from_modeler(self, runs: list[dict[str, Any]]) -> tuple[int, list[str]]:
        """Upsert modeler runs and drop local scans no longer exported by the modeler."""
        refreshed_at = _now()
        seen_run_ids: list[str] = []
        updated = 0
        with self._lock:
            with self._connect() as connection:
                for run in runs:
                    run_id = str(run.get("runId") or run.get("run_id") or "").strip()
                    if not run_id:
                        continue
                    seen_run_ids.append(run_id)
                    manifest = run.get("manifest")
                    manifest = manifest if isinstance(manifest, dict) else {}
                    fingerprint = _fingerprint(manifest)
                    exported_at = manifest.get("exportedAt") or run.get("telemetryExportedAt")
                    schema_version = manifest.get("schemaVersion")
                    scan_version = scan_version_from_manifest(manifest)
                    row = connection.execute(
                        """
                        select bundle_fingerprint, pushed_bundle_fingerprint, push_status
                        from scans where run_id = ?
                        """,
                        (run_id,),
                    ).fetchone()
                    push_status = row["push_status"] if row else None
                    push_error = None
                    pushed_fingerprint = (
                        row["pushed_bundle_fingerprint"] if row else None
                    )
                    if pushed_fingerprint and pushed_fingerprint != fingerprint:
                        push_status = "stale"
                        push_error = (
                            "Scan bundle changed in SysML Repo Modeler; "
                            "push again to update Langfuse."
                        )
                    connection.execute(
                        """
                        insert into scans (
                          run_id, project_slug, project_name, scan_status,
                          telemetry_export_status, started_at, completed_at,
                          exported_at, bundle_schema_version, bundle_fingerprint,
                          scan_version, opencode_session_id, last_refreshed_at,
                          pushed_bundle_fingerprint, push_status, push_error
                        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        on conflict(run_id) do update set
                          project_slug = excluded.project_slug,
                          project_name = excluded.project_name,
                          scan_status = excluded.scan_status,
                          telemetry_export_status = excluded.telemetry_export_status,
                          started_at = excluded.started_at,
                          completed_at = excluded.completed_at,
                          exported_at = excluded.exported_at,
                          bundle_schema_version = excluded.bundle_schema_version,
                          bundle_fingerprint = excluded.bundle_fingerprint,
                          scan_version = excluded.scan_version,
                          opencode_session_id = excluded.opencode_session_id,
                          last_refreshed_at = excluded.last_refreshed_at,
                          push_status = excluded.push_status,
                          push_error = excluded.push_error
                        """,
                        (
                            run_id,
                            run.get("projectSlug") or run.get("project_slug"),
                            run.get("projectName") or run.get("project_name"),
                            run.get("status"),
                            run.get("telemetryExportStatus") or run.get("telemetry_export_status"),
                            run.get("startedAt") or run.get("started_at"),
                            run.get("completedAt") or run.get("completed_at"),
                            exported_at,
                            schema_version,
                            fingerprint,
                            scan_version,
                            manifest.get("opencodeSessionId"),
                            refreshed_at,
                            pushed_fingerprint,
                            push_status,
                            push_error,
                        ),
                    )
                    updated += 1

                removed_run_ids: list[str] = []
                if seen_run_ids:
                    placeholders = ",".join("?" * len(seen_run_ids))
                    stale_rows = connection.execute(
                        f"""
                        select run_id from scans
                        where run_id not in ({placeholders})
                        """,
                        seen_run_ids,
                    ).fetchall()
                    removed_run_ids = [str(row["run_id"]) for row in stale_rows]
                    if removed_run_ids:
                        delete_placeholders = ",".join("?" * len(removed_run_ids))
                        connection.execute(
                            f"delete from scans where run_id in ({delete_placeholders})",
                            removed_run_ids,
                        )
                else:
                    stale_rows = connection.execute("select run_id from scans").fetchall()
                    removed_run_ids = [str(row["run_id"]) for row in stale_rows]
                    if removed_run_ids:
                        connection.execute("delete from scans")

                connection.commit()
        return updated, removed_run_ids

    def upsert_from_modeler(self, runs: list[dict[str, Any]]) -> int:
        updated, _removed = self.sync_from_modeler(runs)
        return updated

    def list_scans(self) -> list[ScanRecord]:
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    select *
                    from scans
                    order by coalesce(completed_at, started_at, last_refreshed_at) desc
                    """
                ).fetchall()
        return [_row_to_record(row) for row in rows]

    def get_scan(self, run_id: str) -> ScanRecord | None:
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "select * from scans where run_id = ?", (run_id,)
                ).fetchone()
        return _row_to_record(row) if row else None

    def mark_push_result(
        self,
        run_id: str,
        *,
        success: bool,
        bundle_fingerprint: str | None = None,
        error: str | None = None,
        langfuse_session_id: str | None = None,
    ) -> None:
        with self._lock:
            with self._connect() as connection:
                if success:
                    connection.execute(
                        """
                        update scans set
                          push_status = ?,
                          push_error = ?,
                          last_pushed_at = ?,
                          langfuse_session_id = ?,
                          pushed_bundle_fingerprint = ?
                        where run_id = ?
                        """,
                        (
                            "success",
                            None,
                            _now(),
                            langfuse_session_id,
                            bundle_fingerprint,
                            run_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        update scans set
                          push_status = ?,
                          push_error = ?
                        where run_id = ?
                        """,
                        ("failed", error, run_id),
                    )
                connection.commit()


def scan_version_from_manifest(manifest: dict[str, Any]) -> str:
    exported_at = manifest.get("exportedAt")
    if isinstance(exported_at, str) and exported_at.strip():
        return exported_at.strip()
    schema_version = manifest.get("schemaVersion")
    run_id = manifest.get("runId")
    if isinstance(run_id, str) and run_id.strip():
        digest = hashlib.sha256(_fingerprint(manifest).encode("utf-8")).hexdigest()[:12]
        schema = str(schema_version or "1")
        return f"{schema}:{run_id.strip()}:{digest}"
    return "unknown"


def _row_to_record(row: sqlite3.Row) -> ScanRecord:
    keys = set(row.keys())
    return ScanRecord(
        run_id=row["run_id"],
        project_slug=row["project_slug"],
        project_name=row["project_name"],
        scan_status=row["scan_status"],
        telemetry_export_status=row["telemetry_export_status"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        exported_at=row["exported_at"],
        bundle_schema_version=row["bundle_schema_version"],
        bundle_fingerprint=row["bundle_fingerprint"],
        scan_version=row["scan_version"] if "scan_version" in keys else None,
        opencode_session_id=row["opencode_session_id"],
        last_refreshed_at=row["last_refreshed_at"],
        last_pushed_at=row["last_pushed_at"],
        pushed_bundle_fingerprint=row["pushed_bundle_fingerprint"]
        if "pushed_bundle_fingerprint" in keys
        else None,
        push_status=row["push_status"],
        push_error=row["push_error"],
        langfuse_session_id=row["langfuse_session_id"],
    )


def _fingerprint(manifest: dict[str, Any]) -> str:
    payload = {
        "schemaVersion": manifest.get("schemaVersion"),
        "exportedAt": manifest.get("exportedAt"),
        "status": manifest.get("status"),
        "opencodeSessionId": manifest.get("opencodeSessionId"),
        "files": manifest.get("files"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
