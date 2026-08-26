from __future__ import annotations

import io
import json
import logging
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .opencode_client import OpenCodeClient
from .sysml_validation import SysmlValidationResult

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"
ARTIFACT_FILES = (
    ("suite_model.sysml", "suite_model_path"),
    ("suite_evidence.json", "suite_evidence_path"),
    ("unresolved_services.md", "unresolved_services_path"),
)


@dataclass(frozen=True)
class TelemetryExportResult:
    status: str
    export_path: str | None = None
    exported_at: str | None = None
    message: str | None = None


class ScanTelemetryExporter:
    """Write vendor-neutral scan telemetry bundles to the local filesystem."""

    def __init__(
        self,
        export_root: Path | None,
        opencode_client: OpenCodeClient,
        *,
        opencode_provider_id: str | None = None,
        opencode_model_id: str | None = None,
        opencode_agent: str | None = None,
    ) -> None:
        self.export_root = export_root
        self.opencode_client = opencode_client
        self.opencode_provider_id = opencode_provider_id
        self.opencode_model_id = opencode_model_id
        self.opencode_agent = opencode_agent

    def is_configured(self) -> bool:
        return self.export_root is not None

    def export_scan(
        self,
        *,
        run_id: str,
        project_slug: str,
        project_name: str,
        trigger: str,
        status: str,
        started_at: str,
        completed_at: str,
        repository_count: int,
        repositories: list[dict[str, Any]],
        opencode_session_id: str | None,
        opencode_usage: dict[str, Any] | None,
        validation: SysmlValidationResult,
        tool_errors: list[dict[str, str]],
        events: list[dict[str, Any]],
        artifacts: list[Any],
    ) -> TelemetryExportResult:
        if self.export_root is None:
            return TelemetryExportResult(
                status="unconfigured",
                message="TELEMETRY_EXPORT_ROOT is not configured.",
            )

        exported_at = _now()
        bundle_dir = self.export_root / run_id
        try:
            bundle_dir.mkdir(parents=True, exist_ok=True)
            _write_events(bundle_dir / "events.jsonl", events)
            _write_json(bundle_dir / "validation.json", validation.to_json())
            _write_json(
                bundle_dir / "scores.json",
                _build_scores(
                    run_id=run_id,
                    validation=validation,
                    tool_errors=tool_errors,
                    opencode_usage=opencode_usage,
                ),
            )
            _write_opencode_session(
                bundle_dir / "opencode" / "session.json",
                opencode_session_id,
                self.opencode_client,
            )
            _write_artifacts(bundle_dir / "artifacts", artifacts)
            manifest = _build_manifest(
                runId=run_id,
                projectSlug=project_slug,
                projectName=project_name,
                trigger=trigger,
                status=status,
                startedAt=started_at,
                completedAt=completed_at,
                repositoryCount=repository_count,
                repositories=repositories,
                telemetryEnabled=True,
                opencodeSessionId=opencode_session_id,
                opencodeUsage=opencode_usage,
                exportedAt=exported_at,
                exportPath=str(bundle_dir),
                model={
                    "providerId": self.opencode_provider_id,
                    "modelId": self.opencode_model_id,
                    "agent": self.opencode_agent,
                },
                files=_bundle_files(bundle_dir),
            )
            _write_json(bundle_dir / "manifest.json", manifest)
            logger.info(
                "[telemetry] exported scan bundle for run %s to %s",
                run_id[:8],
                bundle_dir,
            )
            return TelemetryExportResult(
                status="completed",
                export_path=str(bundle_dir),
                exported_at=exported_at,
            )
        except OSError as error:
            logger.warning(
                "[telemetry] failed to export scan bundle for run %s: %s",
                run_id[:8],
                error,
            )
            return TelemetryExportResult(
                status="failed",
                message=str(error),
            )


class ScanTelemetryStore:
    """Read exported scan telemetry bundles from the local filesystem."""

    def __init__(self, export_root: Path | None) -> None:
        self.export_root = export_root

    def is_configured(self) -> bool:
        return self.export_root is not None

    def list_runs(self, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        exported: list[dict[str, Any]] = []
        for run in runs:
            export_path = run.get("telemetryExportPath") or run.get(
                "telemetry_export_path"
            )
            export_status = run.get("telemetryExportStatus") or run.get(
                "telemetry_export_status"
            )
            if not export_path and not export_status:
                continue
            manifest = self.read_manifest(str(run.get("runId") or run.get("run_id")))
            exported.append(
                {
                    "runId": run.get("runId") or run.get("run_id"),
                    "projectSlug": run.get("projectSlug") or run.get("project_slug"),
                    "projectName": run.get("projectName") or run.get("project_name"),
                    "status": run.get("status"),
                    "startedAt": run.get("startedAt") or run.get("started_at"),
                    "completedAt": run.get("completedAt") or run.get("completed_at"),
                    "telemetryEnabled": run.get("telemetryEnabled")
                    if run.get("telemetryEnabled") is not None
                    else run.get("telemetry_enabled"),
                    "telemetryExportPath": export_path,
                    "telemetryExportStatus": export_status,
                    "telemetryExportedAt": run.get("telemetryExportedAt")
                    or run.get("telemetry_exported_at"),
                    "manifest": manifest,
                }
            )
        return exported

    def bundle_dir(self, run_id: str) -> Path | None:
        if self.export_root is None or not _safe_run_id(run_id):
            return None
        return self.export_root / run_id

    def read_manifest(self, run_id: str) -> dict[str, Any] | None:
        bundle_dir = self.bundle_dir(run_id)
        if bundle_dir is None:
            return None
        manifest_path = bundle_dir / "manifest.json"
        if not manifest_path.is_file():
            return None
        return _read_json_file(manifest_path)

    def read_file(self, run_id: str, relative_path: str) -> tuple[bytes, str] | None:
        bundle_dir = self.bundle_dir(run_id)
        if bundle_dir is None:
            return None
        target = _resolve_bundle_path(bundle_dir, relative_path)
        if target is None or not target.is_file():
            return None
        content_type = _content_type_for_path(target)
        return target.read_bytes(), content_type

    def create_bundle_archive(self, run_id: str) -> bytes | None:
        bundle_dir = self.bundle_dir(run_id)
        if bundle_dir is None or not bundle_dir.is_dir():
            return None
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in sorted(bundle_dir.rglob("*")):
                if not path.is_file():
                    continue
                archive.add(path, arcname=str(path.relative_to(bundle_dir)))
        return buffer.getvalue()


def _build_manifest(**fields: Any) -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, **fields}


def _build_scores(
    *,
    run_id: str,
    validation: SysmlValidationResult,
    tool_errors: list[dict[str, str]],
    opencode_usage: dict[str, Any] | None,
) -> dict[str, Any]:
    usage = opencode_usage or {}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "scores": {
            "renderable": {
                "value": 1 if validation.status == "completed" else 0,
                "comment": validation.summary,
            },
            "status": {"value": validation.status, "comment": validation.summary},
            "tool_error_count": {"value": len(tool_errors)},
            "opencode_cost": {"value": usage.get("cost")},
            "input_tokens": {"value": usage.get("inputTokens")},
            "output_tokens": {"value": usage.get("outputTokens")},
            "total_tokens": {"value": usage.get("totalTokens")},
            "reasoning_tokens": {"value": usage.get("reasoningTokens")},
            "request_count": {"value": usage.get("requestCount")},
        },
        "metrics": validation.metrics,
        "warnings": validation.warnings,
    }


def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False))
            handle.write("\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_opencode_session(
    path: Path,
    session_id: str | None,
    opencode_client: OpenCodeClient,
) -> None:
    messages: list[dict[str, Any]] = []
    if session_id:
        messages = opencode_client.get_session_messages(session_id)
    _write_json(
        path,
        {
            "schemaVersion": SCHEMA_VERSION,
            "sessionId": session_id,
            "messageCount": len(messages),
            "messages": messages,
        },
    )


def _write_artifacts(artifacts_dir: Path, artifacts: list[Any]) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        pass_id = getattr(artifact, "pass_id", None) or "artifact"
        target_dir = artifacts_dir / pass_id
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename, attr in ARTIFACT_FILES:
            source = getattr(artifact, attr, None)
            if not isinstance(source, str) or not source:
                continue
            source_path = Path(source)
            if not source_path.is_file():
                continue
            (target_dir / filename).write_bytes(source_path.read_bytes())


def _bundle_files(bundle_dir: Path) -> list[str]:
    files: list[str] = []
    for path in sorted(bundle_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append(str(path.relative_to(bundle_dir)).replace("\\", "/"))
    return files


def _resolve_bundle_path(bundle_dir: Path, relative_path: str) -> Path | None:
    relative = relative_path.strip().replace("\\", "/")
    if not relative or relative.startswith("/") or ".." in relative.split("/"):
        return None
    candidate = (bundle_dir / relative).resolve()
    root = bundle_dir.resolve()
    if root not in candidate.parents and candidate != root:
        return None
    return candidate


def _content_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".jsonl":
        return "application/x-ndjson"
    if suffix == ".sysml":
        return "text/plain; charset=utf-8"
    if suffix == ".md":
        return "text/markdown; charset=utf-8"
    return "application/octet-stream"


def _read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _safe_run_id(run_id: str) -> bool:
    return bool(run_id) and run_id.isalnum()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
