from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importer.config_store import ConfigStore, ImporterConfig
from importer.import_state import ImportStateStore, scan_version_from_manifest


def test_config_store_roundtrip(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    config = ImporterConfig(
        modeler_base_url="http://localhost:8080",
        langfuse_host="http://localhost:3000",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",  # pragma: allowlist secret
        langfuse_project_name="demo",
    )
    store.save(config)
    loaded = store.load()
    assert loaded.modeler_base_url == "http://localhost:8080"
    assert loaded.langfuse_public_key == "pk-test"
    public = store.to_public_json(loaded)
    assert public["hasSecretKey"] is True
    assert "langfuseSecretKey" not in public


def test_scan_version_from_manifest_uses_exported_at():
    manifest = {
        "schemaVersion": "1",
        "exportedAt": "2026-08-25T00:00:00+00:00",
        "runId": "abc123",
    }
    assert scan_version_from_manifest(manifest) == "2026-08-25T00:00:00+00:00"


def test_import_state_tracks_push_and_stale(tmp_path: Path):
    store = ImportStateStore(tmp_path / "imports.db")
    manifest_v1 = {
        "schemaVersion": "1",
        "exportedAt": "2026-08-25T00:00:00+00:00",
        "status": "completed",
        "opencodeSessionId": "session-1",
        "files": ["manifest.json"],
    }
    store.upsert_from_modeler(
        [
            {
                "runId": "abc123",
                "projectSlug": "demo",
                "projectName": "Demo",
                "status": "completed",
                "telemetryExportStatus": "completed",
                "manifest": manifest_v1,
            }
        ]
    )
    record = store.get_scan("abc123")
    assert record is not None
    assert record.scan_version == "2026-08-25T00:00:00+00:00"
    assert record.needs_push() is True

    store.mark_push_result(
        "abc123",
        success=True,
        bundle_fingerprint=record.bundle_fingerprint,
    )
    record = store.get_scan("abc123")
    assert record is not None
    assert record.pushed_bundle_fingerprint == record.bundle_fingerprint
    assert record.needs_push() is False

    manifest_v2 = dict(manifest_v1)
    manifest_v2["files"] = ["manifest.json", "scores.json"]
    store.upsert_from_modeler(
        [
            {
                "runId": "abc123",
                "projectSlug": "demo",
                "projectName": "Demo",
                "status": "completed",
                "telemetryExportStatus": "completed",
                "manifest": manifest_v2,
            }
        ]
    )
    record = store.get_scan("abc123")
    assert record is not None
    assert record.push_status == "stale"
    assert record.needs_push() is True


def test_sync_from_modeler_removes_unavailable_scans(tmp_path: Path):
    store = ImportStateStore(tmp_path / "imports.db")
    manifest = {
        "schemaVersion": "1",
        "exportedAt": "2026-08-25T00:00:00+00:00",
        "status": "completed",
        "files": ["manifest.json"],
    }
    store.sync_from_modeler(
        [
            {
                "runId": "keep-me",
                "projectSlug": "demo",
                "projectName": "Demo",
                "status": "completed",
                "telemetryExportStatus": "completed",
                "manifest": manifest,
            },
            {
                "runId": "also-keep",
                "projectSlug": "demo",
                "projectName": "Demo",
                "status": "completed",
                "telemetryExportStatus": "completed",
                "manifest": manifest,
            },
        ]
    )
    assert {scan.run_id for scan in store.list_scans()} == {"keep-me", "also-keep"}

    updated, removed = store.sync_from_modeler(
        [
            {
                "runId": "keep-me",
                "projectSlug": "demo",
                "projectName": "Demo",
                "status": "completed",
                "telemetryExportStatus": "completed",
                "manifest": manifest,
            }
        ]
    )
    assert updated == 1
    assert removed == ["also-keep"]
    assert {scan.run_id for scan in store.list_scans()} == {"keep-me"}

    updated, removed = store.sync_from_modeler([])
    assert updated == 0
    assert removed == ["keep-me"]
    assert store.list_scans() == []
