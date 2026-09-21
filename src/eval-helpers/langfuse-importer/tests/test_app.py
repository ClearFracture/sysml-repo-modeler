from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importer.app import create_app


def test_spa_fallback_does_not_expose_data_directory(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    secret_path = data_dir / "config.json"
    secret_path.write_text(
        '{"langfuseSecretKey":"super-secret"}',  # pragma: allowlist secret
        encoding="utf-8",
    )

    ui_dist = tmp_path / "ui" / "dist"
    ui_dist.mkdir(parents=True)
    (ui_dist / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    with patch("importer.app.UI_DIST", ui_dist):
        app = create_app(data_dir=data_dir)
        client = TestClient(app)

        response = client.get("/..%2f..%2fdata%2fconfig.json")
        assert response.status_code == 200
        assert "super-secret" not in response.text
        assert "ok" in response.text


def test_reset_push_status_endpoint(tmp_path: Path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    store = app.state.service.state_store
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
    record = store.get_scan("run-1")
    assert record is not None
    store.mark_push_result(
        "run-1",
        success=True,
        bundle_fingerprint=record.bundle_fingerprint,
        langfuse_session_id="sysml-project:Demo:2026-08-25T00:00:00+00:00",
    )

    response = client.post("/api/scans/run-1/reset-push")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scan"]["pushStatus"] is None
    assert payload["scan"]["needsPush"] is True

    missing = client.post("/api/scans/missing/reset-push")
    assert missing.status_code == 404
