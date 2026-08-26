from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importer.config_store import ImporterConfig
from importer.langfuse_import import (
    LangfuseImportError,
    map_token_usage,
    scan_session_id_for_manifest,
    import_bundle,
)
from importer.timed_observation import emit_timed_item
from importer.timeline import TimelineItem, parse_timestamp

TEST_RUN_ID = "testrun001"


def _write_bundle(
    bundle_dir: Path,
    *,
    events: list[dict] | None = None,
    messages: list[dict] | None = None,
) -> None:
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
        "opencodeUsage": {
            "cost": 0.42,
            "inputTokens": 100,
            "outputTokens": 50,
            "totalTokens": 150,
            "requestCount": 2,
        },
        "model": {"providerId": "openai", "modelId": "gpt-test"},
    }
    (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (bundle_dir / "validation.json").write_text(
        json.dumps({"status": "completed", "summary": "ok"}),
        encoding="utf-8",
    )
    (bundle_dir / "scores.json").write_text(
        json.dumps({"scores": {"opencode_cost": {"value": 0.42}}}),
        encoding="utf-8",
    )
    (bundle_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in (events or [])) + "\n",
        encoding="utf-8",
    )
    session = {
        "schemaVersion": "1",
        "sessionId": "session-1",
        "messageCount": len(messages or []),
        "usage": manifest["opencodeUsage"],
        "messages": messages or [],
    }
    opencode_dir = bundle_dir / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    (opencode_dir / "session.json").write_text(
        json.dumps(session),
        encoding="utf-8",
    )


def test_scan_session_id_for_manifest():
    run_id = TEST_RUN_ID
    scan_version = "2026-08-25T00:00:00+00:00"
    assert (
        scan_session_id_for_manifest(
            {"projectName": "Demo Project", "runId": run_id},
            scan_version=scan_version,
        )
        == "sysml-project:Demo Project:2026-08-25T00:00:00+00:00"
    )
    assert (
        scan_session_id_for_manifest(
            {"projectName": "Demo:Project", "runId": run_id},
            scan_version=scan_version,
        )
        == "sysml-project:Demo-Project:2026-08-25T00:00:00+00:00"
    )
    assert (
        scan_session_id_for_manifest(
            {"projectSlug": "demo", "runId": run_id},
            scan_version=scan_version,
        )
        == "sysml-project:demo:2026-08-25T00:00:00+00:00"
    )


def test_map_token_usage_includes_cost_and_cache():
    usage, cost = map_token_usage(
        {
            "input": 10,
            "output": 5,
            "reasoning": 2,
            "total": 17,
            "cache": {"read": 3, "write": 1},
        },
        cost=0.015,
    )
    assert usage == {
        "input": 10,
        "output": 5,
        "reasoning": 2,
        "total": 17,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 1,
    }
    assert cost == {"total": 0.015}


def test_import_bundle_requires_keys(tmp_path: Path):
    bundle_dir = tmp_path / "bundle"
    _write_bundle(bundle_dir)
    config = ImporterConfig(
        modeler_base_url="http://localhost:8080",
        langfuse_host="http://localhost:3000",
        langfuse_public_key="",
        langfuse_secret_key="",
        langfuse_project_name="demo",
    )
    try:
        import_bundle(bundle_dir, config=config, scan_version="2026-08-25T00:00:00+00:00")
        raised = False
    except LangfuseImportError:
        raised = True
    assert raised


@patch("importer.langfuse_import.Langfuse")
def test_import_bundle_creates_scan_session_and_trace(mock_langfuse_cls, tmp_path: Path):
    mock_langfuse = MagicMock()
    mock_langfuse.create_trace_id.return_value = "trace-123"
    mock_span = MagicMock()
    mock_span.get_span_context.return_value.span_id = 0x1234
    mock_langfuse._otel_tracer.start_span.return_value = mock_span
    mock_langfuse._create_observation_from_otel_span.return_value = MagicMock()
    mock_langfuse_cls.return_value = mock_langfuse

    messages = [
        {
            "type": "user",
            "parts": [{"type": "text", "text": "Analyze repositories"}],
        },
        {
            "info": {
                "id": "assistant-1",
                "role": "assistant",
                "providerID": "openai",
                "modelID": "gpt-test",
            },
            "parts": [
                {
                    "type": "tool",
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"path": "README.md"},
                        "output": "hello",
                    },
                },
                {"type": "text", "text": "package Demo {}"},
                {
                    "type": "step-finish",
                    "tokens": {
                        "input": 100,
                        "output": 50,
                        "total": 150,
                    },
                    "cost": 0.12,
                },
            ],
        },
    ]
    events = [
        {
            "phase": "cycle_start",
            "level": "info",
            "message": "Started",
            "timestamp": "2026-08-25T00:00:00+00:00",
        },
        {
            "phase": "validation",
            "level": "info",
            "message": "Validation passed",
            "timestamp": "2026-08-25T00:04:00+00:00",
        },
    ]

    bundle_dir = tmp_path / "bundle"
    _write_bundle(bundle_dir, events=events, messages=messages)

    config = ImporterConfig(
        modeler_base_url="http://localhost:8080",
        langfuse_host="http://localhost:3000",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",  # pragma: allowlist secret
        langfuse_project_name="demo",
    )
    result = import_bundle(bundle_dir, config=config, scan_version="2026-08-25T00:00:00+00:00")

    assert result["langfuseSessionId"] == "sysml-project:Demo Project:2026-08-25T00:00:00+00:00"
    assert result["runId"] == TEST_RUN_ID
    assert result["langfuseTraceId"] == "trace-123"
    assert result["timelineCount"] >= 2
    mock_langfuse.create_trace_id.assert_called_once_with(
        seed=TEST_RUN_ID
    )
    mock_langfuse.create_score.assert_called()
    mock_langfuse.flush.assert_called_once()


def test_emit_timed_item_uses_historical_start_and_end():
    mock_langfuse = MagicMock()
    mock_observation = MagicMock()
    mock_span = MagicMock()
    mock_span.get_span_context.return_value.span_id = 0x1234
    mock_langfuse._create_remote_parent_span.return_value = MagicMock()
    mock_langfuse._otel_tracer.start_span.return_value = mock_span
    mock_langfuse._create_observation_from_otel_span.return_value = mock_observation

    start = parse_timestamp("2026-08-25T00:01:00+00:00")
    end = parse_timestamp("2026-08-25T00:03:00+00:00")
    item = TimelineItem(
        kind="pipeline",
        name="step-opencode",
        start=start,
        end=end,
        output="Running OpenCode",
    )

    emit_timed_item(
        mock_langfuse,
        trace_id="trace-123",
        parent_span_id="parent-span",
        item=item,
    )

    start_ns = int(start.timestamp() * 1_000_000_000)
    end_ns = int(end.timestamp() * 1_000_000_000)
    mock_langfuse._otel_tracer.start_span.assert_called_once_with(
        "step-opencode",
        start_time=start_ns,
    )
    mock_observation.end.assert_called_once_with(end_time=end_ns)
