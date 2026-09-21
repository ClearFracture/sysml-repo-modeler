from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace import TracerProvider

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importer.config_store import ImporterConfig
from importer.langfuse_import import (
    IngestionScoreBody,
    LangfuseImportError,
    _enqueue_trace_score,
    map_token_usage,
    scan_session_id_for_manifest,
    import_bundle,
    trace_seed_for_import,
)
from importer.timed_observation import _start_span_with_id, emit_timed_item
from importer.timeline import TimelineItem, parse_timestamp

TEST_RUN_ID = "testrun001"


class _RecordingIdGenerator:
    def __init__(self) -> None:
        self._next_span_id = 1

    def generate_span_id(self) -> int:
        span_id = self._next_span_id
        self._next_span_id += 1
        return span_id

    def generate_trace_id(self) -> int:
        return 1

    def is_trace_id_random(self) -> bool:
        return False


class _RecordingTracer:
    def __init__(self) -> None:
        self.id_generator = _RecordingIdGenerator()
        self.span_ids: list[int] = []

    def start_span(self, name: str, *, start_time: int) -> MagicMock:
        span = MagicMock()
        span_id = self.id_generator.generate_span_id()
        span.get_span_context.return_value.span_id = span_id
        self.span_ids.append(span_id)
        return span


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


def test_trace_seed_includes_scan_version():
    assert (
        trace_seed_for_import("run-1", "2026-08-25T00:00:00+00:00")
        == "run-1:2026-08-25T00:00:00+00:00"
    )


def test_start_span_with_id_uses_requested_id_and_restores_generator():
    tracer = TracerProvider().get_tracer("langfuse-importer-idempotency-test")
    original_generator = tracer.id_generator

    first = _start_span_with_id(
        tracer,
        name="same-observation",
        start_time=1,
        observation_id="0123456789abcdef",
    )
    second = _start_span_with_id(
        tracer,
        name="same-observation",
        start_time=1,
        observation_id="0123456789abcdef",
    )

    assert first.get_span_context().span_id == int("0123456789abcdef", 16)
    assert second.get_span_context().span_id == first.get_span_context().span_id
    assert tracer.id_generator is original_generator
    first.end()
    second.end()


def test_enqueue_trace_score_sets_trace_id_on_body():
    langfuse = MagicMock()
    langfuse.create_trace_id.return_value = "event-id"
    langfuse._resources = MagicMock()

    _enqueue_trace_score(
        langfuse,
        trace_id="abcd1234abcd1234abcd1234abcd1234",
        name="opencode_cost",
        value=1.25,
        score_id="score-id",
    )

    event = langfuse._resources.add_score_task.call_args.args[0]
    body = event["body"]
    assert isinstance(body, IngestionScoreBody)
    assert body.trace_id == "abcd1234abcd1234abcd1234abcd1234"
    assert body.model_dump(exclude_none=True)["traceId"] == (
        "abcd1234abcd1234abcd1234abcd1234"
    )
    assert "trace_id" not in body.model_dump(exclude_none=True)
    assert langfuse._resources.add_score_task.call_args.kwargs["force_sample"] is True


def test_ingestion_score_body_serializes_api_field_names():
    body = IngestionScoreBody(
        id="score-id",
        trace_id="abcd1234abcd1234abcd1234abcd1234",
        name="status",
        value="completed",
        data_type="CATEGORICAL",
    )
    payload = body.model_dump(exclude_none=True)
    assert payload == {
        "id": "score-id",
        "traceId": "abcd1234abcd1234abcd1234abcd1234",
        "name": "status",
        "value": "completed",
        "dataType": "CATEGORICAL",
    }


def test_enqueue_trace_score_marks_categorical_values():
    langfuse = MagicMock()
    langfuse.create_trace_id.return_value = "event-id"
    langfuse._resources = MagicMock()

    _enqueue_trace_score(
        langfuse,
        trace_id="abcd1234abcd1234abcd1234abcd1234",
        name="status",
        value="completed",
        score_id="score-id",
    )

    body = langfuse._resources.add_score_task.call_args.args[0]["body"]
    assert body.data_type == "CATEGORICAL"
    assert body.model_dump(exclude_none=True)["dataType"] == "CATEGORICAL"


@patch("importer.langfuse_import.Langfuse")
def test_import_bundle_creates_scan_session_and_trace(mock_langfuse_cls, tmp_path: Path):
    mock_langfuse = MagicMock()
    mock_langfuse.create_trace_id.return_value = "trace-123"
    mock_langfuse.auth_check.return_value = True
    mock_langfuse._resources = MagicMock()
    mock_langfuse._create_observation_id.return_value = "score-obs-1"
    mock_langfuse.api.trace.delete.return_value = None
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
    mock_langfuse.create_trace_id.assert_any_call(
        seed=f"{TEST_RUN_ID}:2026-08-25T00:00:00+00:00"
    )
    mock_langfuse.auth_check.assert_called()
    mock_langfuse.api.trace.delete.assert_not_called()
    mock_langfuse._resources.add_score_task.assert_called_once()
    score_event = mock_langfuse._resources.add_score_task.call_args.args[0]
    score_body = score_event["body"]
    assert score_body.trace_id == "trace-123"
    assert score_body.model_dump(exclude_none=True)["traceId"] == "trace-123"
    assert mock_langfuse._resources.add_score_task.call_args.kwargs["force_sample"] is True
    mock_langfuse.flush.assert_called_once()
    mock_langfuse.shutdown.assert_not_called()


@patch("importer.langfuse_import.Langfuse")
def test_import_bundle_raises_when_langfuse_unreachable(mock_langfuse_cls, tmp_path: Path):
    mock_langfuse = MagicMock()
    mock_langfuse.auth_check.side_effect = ConnectionError("offline")
    mock_langfuse_cls.return_value = mock_langfuse

    bundle_dir = tmp_path / "bundle"
    _write_bundle(bundle_dir)
    config = ImporterConfig(
        modeler_base_url="http://localhost:8080",
        langfuse_host="http://localhost:3000",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",  # pragma: allowlist secret
        langfuse_project_name="demo",
    )

    with pytest.raises(LangfuseImportError, match="Langfuse connection or authentication failed"):
        import_bundle(bundle_dir, config=config, scan_version="2026-08-25T00:00:00+00:00")


@patch("importer.langfuse_import.Langfuse")
def test_import_bundle_reuses_observation_ids_on_reimport(
    mock_langfuse_cls, tmp_path: Path
):
    mock_langfuse = MagicMock()
    mock_langfuse.create_trace_id.return_value = "trace-123"
    mock_langfuse.auth_check.return_value = True
    mock_langfuse._resources = MagicMock()
    mock_langfuse._create_observation_id.return_value = "score-obs-1"
    tracer = _RecordingTracer()
    mock_langfuse._otel_tracer = tracer
    mock_langfuse._create_observation_from_otel_span.return_value = MagicMock()
    mock_langfuse_cls.return_value = mock_langfuse

    bundle_dir = tmp_path / "bundle"
    _write_bundle(
        bundle_dir,
        events=[
            {
                "phase": "validation",
                "timestamp": "2026-08-25T00:01:00+00:00",
            },
            {
                "phase": "validation",
                "timestamp": "2026-08-25T00:02:00+00:00",
            },
        ],
    )
    config = ImporterConfig(
        modeler_base_url="http://localhost:8080",
        langfuse_host="http://localhost:3000",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",  # pragma: allowlist secret
        langfuse_project_name="demo",
    )

    first = import_bundle(
        bundle_dir,
        config=config,
        scan_version="2026-08-25T00:00:00+00:00",
    )
    import_bundle(
        bundle_dir,
        config=config,
        scan_version="2026-08-25T00:00:00+00:00",
    )

    observations_per_import = first["timelineCount"] + 1
    first_ids = tracer.span_ids[:observations_per_import]
    second_ids = tracer.span_ids[observations_per_import:]
    assert first_ids == second_ids
    assert len(first_ids) == len(set(first_ids))
    mock_langfuse.api.trace.delete.assert_not_called()


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
