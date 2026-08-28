from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importer.config_store import ImporterConfig
from importer.langfuse_delivery import LangfuseDeliveryError, LangfuseDeliveryTracker
from importer.langfuse_import import LangfuseImportError, import_bundle
from langfuse._utils import parse_error as langfuse_parse_error
from langfuse._utils.request import APIError

TEST_RUN_ID = "testrun001"


def _write_bundle(bundle_dir: Path) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schemaVersion": "1",
        "runId": TEST_RUN_ID,
        "projectSlug": "demo",
        "projectName": "Demo Project",
        "status": "completed",
        "trigger": "manual",
        "startedAt": "2026-08-25T00:00:00+00:00",
        "completedAt": "2026-08-25T00:05:00+00:00",
        "opencodeSessionId": "session-1",
        "model": {"providerId": "openai", "modelId": "gpt-test"},
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (bundle_dir / "validation.json").write_text("{}", encoding="utf-8")
    (bundle_dir / "scores.json").write_text("{}", encoding="utf-8")
    (bundle_dir / "events.jsonl").write_text("", encoding="utf-8")
    opencode_dir = bundle_dir / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    (opencode_dir / "session.json").write_text(
        json.dumps({"schemaVersion": "1", "sessionId": "session-1", "messages": []}),
        encoding="utf-8",
    )


def test_delivery_tracker_records_handle_exception_errors():
    with LangfuseDeliveryTracker() as tracker:
        langfuse_parse_error.handle_exception(APIError(400, "invalid score payload"))

    with pytest.raises(LangfuseDeliveryError, match="Bad request"):
        tracker.raise_if_failed()


def test_delivery_tracker_records_export_log_messages():
    logger = logging.getLogger("langfuse.test.export")
    with LangfuseDeliveryTracker() as tracker:
        logger.error(
            "API errors occurred: Bad request. Please check your request for any missing or incorrect parameters."
        )

    with pytest.raises(LangfuseDeliveryError, match="Bad request"):
        tracker.raise_if_failed()


@patch("importer.langfuse_import.Langfuse")
def test_import_bundle_raises_when_delivery_tracker_records_api_error(
    mock_langfuse_cls,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_langfuse = MagicMock()
    mock_langfuse.create_trace_id.return_value = "trace-123"
    mock_langfuse.auth_check.return_value = True
    mock_span = MagicMock()
    mock_span.get_span_context.return_value.span_id = 0x1234
    mock_langfuse._otel_tracer.start_span.return_value = mock_span
    mock_langfuse._create_observation_from_otel_span.return_value = MagicMock()
    mock_langfuse_cls.return_value = mock_langfuse

    def fail_delivery(langfuse, delivery_tracker):
        delivery_tracker.record(
            "API errors occurred: Bad request. Please check your request for any missing or incorrect parameters."
        )
        delivery_tracker.raise_if_failed()

    monkeypatch.setattr(
        "importer.langfuse_import._ensure_langfuse_delivery",
        fail_delivery,
    )

    bundle_dir = tmp_path / "bundle"
    _write_bundle(bundle_dir)
    config = ImporterConfig(
        modeler_base_url="http://localhost:8080",
        langfuse_host="http://localhost:3000",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",  # pragma: allowlist secret
        langfuse_project_name="demo",
    )

    with pytest.raises(LangfuseImportError, match="Langfuse export failed"):
        import_bundle(bundle_dir, config=config, scan_version="2026-08-25T00:00:00+00:00")
