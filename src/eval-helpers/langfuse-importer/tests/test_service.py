from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importer.config_store import ConfigStore, ImporterConfig
from importer.import_state import ImportStateStore
from importer.langfuse_import import LangfuseImportError
from importer.modeler_client import ModelerClientError
from importer.service import ImporterService


def _seed_scan(store: ImportStateStore, *, push_status: str | None = None) -> None:
    store.sync_from_modeler(
        [
            {
                "runId": "run-1",
                "projectSlug": "demo",
                "projectName": "Demo",
                "status": "completed",
                "telemetryExportStatus": "completed",
                "manifest": {
                    "schemaVersion": "1",
                    "runId": "run-1",
                    "exportedAt": "2026-08-25T00:00:00+00:00",
                },
            }
        ]
    )
    if push_status == "success":
        record = store.get_scan("run-1")
        assert record is not None
        store.mark_push_result(
            "run-1",
            success=True,
            bundle_fingerprint=record.bundle_fingerprint,
            langfuse_session_id="sysml-project:Demo:2026-08-25T00:00:00+00:00",
        )


def test_push_scan_skips_already_pushed_bundle(tmp_path: Path):
    service = ImporterService(tmp_path)
    _seed_scan(service.state_store, push_status="success")

    with patch.object(service, "_push_bundle") as mock_push:
        result = service.push_scan("run-1")

    mock_push.assert_not_called()
    assert result["result"]["skipped"] is True
    assert result["scan"]["pushStatus"] == "success"


@patch("importer.service.import_bundle")
@patch("importer.service.ModelerClient")
def test_push_scan_marks_failed_langfuse_import(
    mock_client_cls,
    mock_import_bundle,
    tmp_path: Path,
):
    service = ImporterService(tmp_path)
    _seed_scan(service.state_store)

    mock_client = MagicMock()
    mock_client.download_bundle.return_value = tmp_path / "bundle"
    mock_client_cls.return_value = mock_client
    mock_import_bundle.side_effect = LangfuseImportError("Langfuse connection failed")

    ConfigStore(tmp_path / "config.json").save(
        ImporterConfig(
            modeler_base_url="http://localhost:8080",
            langfuse_host="http://localhost:3000",
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",  # pragma: allowlist secret
            langfuse_project_name="demo",
        )
    )

    try:
        service.push_scan("run-1")
        raised = False
    except RuntimeError as error:
        raised = True
        assert "Langfuse connection failed" in str(error)

    assert raised
    record = service.state_store.get_scan("run-1")
    assert record is not None
    assert record.push_status == "failed"
    assert "Langfuse connection failed" in (record.push_error or "")


@patch("importer.service.ModelerClient")
def test_refresh_scans_wraps_modeler_client_errors(mock_client_cls, tmp_path: Path):
    service = ImporterService(tmp_path)
    mock_client = MagicMock()
    mock_client.list_telemetry_runs.side_effect = ModelerClientError("offline")
    mock_client_cls.return_value = mock_client

    try:
        service.refresh_scans()
        raised = False
    except RuntimeError as error:
        raised = True
        assert "offline" in str(error)

    assert raised


def test_reset_push_status_allows_repush(tmp_path: Path):
    service = ImporterService(tmp_path)
    _seed_scan(service.state_store, push_status="success")

    result = service.reset_push_status("run-1")
    assert result["scan"]["pushStatus"] is None
    assert result["scan"]["needsPush"] is True

    with patch.object(service, "_push_bundle") as mock_push:
        mock_push.return_value = {"langfuseSessionId": "sysml-project:Demo:2026-08-25T00:00:00+00:00"}
        push_result = service.push_scan("run-1")

    mock_push.assert_called_once()
    assert push_result["result"]["langfuseSessionId"] == "sysml-project:Demo:2026-08-25T00:00:00+00:00"


def test_reset_push_status_unknown_scan(tmp_path: Path):
    service = ImporterService(tmp_path)
    try:
        service.reset_push_status("missing")
        raised = False
    except RuntimeError as error:
        raised = True
        assert "not tracked" in str(error)

    assert raised
