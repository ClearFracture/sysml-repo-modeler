from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importer.timeline import build_timeline, datetime_to_ns, parse_timestamp


def test_parse_timestamp_handles_iso_and_unix_ms():
    iso = parse_timestamp("2026-08-25T00:01:30+00:00")
    assert iso is not None
    assert iso.isoformat().startswith("2026-08-25T00:01:30")

    unix_ms = parse_timestamp(1724544000000)
    assert unix_ms is not None
    assert unix_ms.year == 2024


def test_build_timeline_interleaves_pipeline_and_opencode():
    manifest = {
        "startedAt": "2026-08-25T00:00:00+00:00",
        "completedAt": "2026-08-25T00:05:00+00:00",
    }
    events = [
        {
            "phase": "cycle_start",
            "timestamp": "2026-08-25T00:00:00+00:00",
            "message": "Started",
        },
        {
            "phase": "opencode_path",
            "timestamp": "2026-08-25T00:01:00+00:00",
            "message": "Running OpenCode",
        },
        {
            "phase": "validation",
            "timestamp": "2026-08-25T00:04:00+00:00",
            "message": "Validation complete",
        },
    ]
    messages = [
        {
            "role": "user",
            "messageId": "user-1",
            "time": {"created": "2026-08-25T00:01:30+00:00"},
            "parts": [{"type": "text", "text": "Analyze repo"}],
        },
        {
            "role": "assistant",
            "messageId": "assistant-1",
            "time": {
                "created": "2026-08-25T00:02:00+00:00",
                "completed": "2026-08-25T00:03:30+00:00",
            },
            "parts": [
                {
                    "type": "tool",
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"path": "README.md"},
                        "output": "hello",
                        "time": {
                            "start": "2026-08-25T00:02:05+00:00",
                            "end": "2026-08-25T00:02:15+00:00",
                        },
                    },
                },
                {
                    "type": "step-finish",
                    "tokens": {"input": 10, "output": 5, "total": 15},
                    "cost": 0.01,
                },
            ],
        },
    ]

    timeline = build_timeline(events, messages, manifest, default_model="gpt-test")
    names = [item.name for item in timeline]

    assert names.index("step-cycle_start") < names.index("prompt-turn-1")
    assert names.index("step-opencode_path") < names.index("prompt-turn-1")
    assert names.index("prompt-turn-1") < names.index("step-validation")
    assert names.index("turn-1-assistant") < names.index("step-validation")

    cycle_start = next(item for item in timeline if item.name == "step-cycle_start")
    opencode_path = next(item for item in timeline if item.name == "step-opencode_path")
    prompt = next(item for item in timeline if item.name == "prompt-turn-1")
    assistant = next(item for item in timeline if item.name == "turn-1-assistant")
    validation = next(item for item in timeline if item.name == "step-validation")

    assert cycle_start.end == opencode_path.start
    assert opencode_path.start < prompt.start
    assert prompt.start < assistant.start
    assert assistant.end <= validation.start

    tool = next(child for child in assistant.children if child.name == "read")
    duration_ms = (tool.end - tool.start).total_seconds() * 1000
    assert duration_ms >= 9000


def test_datetime_to_ns_produces_realistic_durations():
    start = parse_timestamp("2026-08-25T00:00:00+00:00")
    end = parse_timestamp("2026-08-25T00:02:30+00:00")
    assert start is not None and end is not None
    duration_ms = (datetime_to_ns(end) - datetime_to_ns(start)) / 1_000_000
    assert duration_ms == timedelta(minutes=2, seconds=30).total_seconds() * 1000
