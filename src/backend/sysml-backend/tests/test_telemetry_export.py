from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sysml_backend.interfaces.web_common import telemetry_enabled_from_payload
from sysml_backend.services.opencode_client import OpenCodeClient, OpenCodeConfig
from sysml_backend.services.sysml_validation import SysmlValidationResult
from sysml_backend.services.telemetry_export import (
    ScanTelemetryExporter,
    ScanTelemetryStore,
)


class _StubOpenCodeClient(OpenCodeClient):
    def __init__(self) -> None:
        super().__init__(OpenCodeConfig(base_url=None))

    def get_session_messages(self, session_id: str) -> list[dict]:
        del session_id
        return [{"type": "assistant", "parts": [{"type": "text", "text": "package Demo {}"}]}]


def test_telemetry_enabled_from_payload():
    assert telemetry_enabled_from_payload(None) is False
    assert telemetry_enabled_from_payload({}) is False
    assert telemetry_enabled_from_payload({"telemetryEnabled": True}) is True
    assert telemetry_enabled_from_payload({"telemetry_enabled": "yes"}) is True


def test_export_scan_writes_bundle(tmp_path):
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    model_path = artifact_dir / "suite_model.sysml"
    model_path.write_text("package Demo {}", encoding="utf-8")
    evidence_path = artifact_dir / "suite_evidence.json"
    evidence_path.write_text("{}", encoding="utf-8")
    unresolved_path = artifact_dir / "unresolved_services.md"
    unresolved_path.write_text("none", encoding="utf-8")

    class Artifact:
        pass_id = "synthesis"
        suite_model_path = str(model_path)
        suite_evidence_path = str(evidence_path)
        unresolved_services_path = str(unresolved_path)

    exporter = ScanTelemetryExporter(
        tmp_path / "telemetry",
        _StubOpenCodeClient(),
        opencode_provider_id="openai",
        opencode_model_id="gpt-test",
    )
    validation = SysmlValidationResult(
        status="completed",
        summary="Generated SysML is renderable.",
        metrics={"partDefs": 1},
        warnings=[],
    )
    result = exporter.export_scan(
        run_id="abc123def4567890abc123def4567890",
        project_slug="demo",
        project_name="Demo",
        trigger="manual",
        status="completed",
        started_at="2026-08-25T00:00:00+00:00",
        completed_at="2026-08-25T00:05:00+00:00",
        repository_count=1,
        repositories=[{"path": "repos/demo", "changeStatus": "unchanged"}],
        opencode_session_id="session-1",
        opencode_usage={"cost": 0.12, "inputTokens": 10, "outputTokens": 20},
        validation=validation,
        tool_errors=[],
        events=[{"phase": "cycle_start", "message": "Started"}],
        artifacts=[Artifact()],
    )

    assert result.status == "completed"
    bundle_dir = Path(result.export_path or "")
    assert (bundle_dir / "manifest.json").is_file()
    assert (bundle_dir / "events.jsonl").is_file()
    assert (bundle_dir / "validation.json").is_file()
    assert (bundle_dir / "scores.json").is_file()
    assert (bundle_dir / "opencode" / "session.json").is_file()
    assert (bundle_dir / "artifacts" / "synthesis" / "suite_model.sysml").is_file()

    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == "1"
    assert manifest["runId"] == "abc123def4567890abc123def4567890"
    assert manifest["telemetryEnabled"] is True


def test_telemetry_store_reads_bundle(tmp_path):
    run_id = "abc123def4567890abc123def4567890"
    bundle_dir = tmp_path / run_id
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "manifest.json").write_text(
        json.dumps({"schemaVersion": "1", "runId": run_id}),
        encoding="utf-8",
    )
    (bundle_dir / "scores.json").write_text("{}", encoding="utf-8")

    store = ScanTelemetryStore(tmp_path)
    manifest = store.read_manifest(run_id)
    assert manifest is not None
    assert manifest["runId"] == run_id

    payload = store.read_file(run_id, "scores.json")
    assert payload is not None
    assert payload[1] == "application/json"

    archive = store.create_bundle_archive(run_id)
    assert archive is not None
    assert len(archive) > 0

    listed = store.list_runs(
        [
            {
                "runId": run_id,
                "telemetryEnabled": True,
                "telemetryExportPath": str(bundle_dir),
                "telemetryExportStatus": "completed",
            }
        ]
    )
    assert len(listed) == 1
    assert listed[0]["manifest"]["runId"] == run_id
