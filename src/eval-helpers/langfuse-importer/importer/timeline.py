from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .opencode_transcript import (
    message_parts,
    message_prompt_text,
    part_text,
    tool_part_payload,
)


@dataclass
class TimelineItem:
    kind: str
    name: str
    start: datetime
    end: datetime
    as_type: str = "span"
    level: str = "DEFAULT"
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    usage_details: dict[str, int] | None = None
    cost_details: dict[str, float] | None = None
    children: list[TimelineItem] = field(default_factory=list)


SKIP_EVENT_PHASES = frozenset({"opencode_delta", "opencode_reasoning"})


def build_timeline(
    events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    default_model: str,
) -> list[TimelineItem]:
    scan_start = parse_timestamp(manifest.get("startedAt"))
    scan_end = parse_timestamp(manifest.get("completedAt"))
    items: list[TimelineItem] = []

    pipeline_events = [
        event
        for event in events
        if str(event.get("phase") or "") not in SKIP_EVENT_PHASES
    ]
    opencode_pass_times = [
        parse_timestamp(event.get("timestamp"))
        for event in pipeline_events
        if str(event.get("phase") or "") == "opencode_pass"
    ]
    opencode_pass_times = [value for value in opencode_pass_times if value is not None]

    for index, event in enumerate(pipeline_events):
        phase = str(event.get("phase") or "unknown")
        if phase == "opencode_pass":
            continue
        start = parse_timestamp(event.get("timestamp")) or scan_start
        next_start = None
        for later in pipeline_events[index + 1 :]:
            next_start = parse_timestamp(later.get("timestamp"))
            if next_start is not None:
                break
        end = next_start or scan_end or (start + timedelta(milliseconds=1))
        if end <= start:
            end = start + timedelta(milliseconds=1)
        items.append(
            TimelineItem(
                kind="pipeline",
                name=f"step-{phase}",
                start=start,
                end=end,
                level=_event_level(event.get("level")),
                input={"phase": phase, "entity": event.get("entity")},
                output=event.get("message"),
                metadata={
                    "phase": phase,
                    "entity": event.get("entity"),
                    "runId": event.get("run_id") or event.get("runId"),
                    "reasoningSummary": event.get("reasoning_summary"),
                },
            )
        )

    pass_index = 0
    turn = 0
    for message in messages:
        role = str(message.get("role") or "unknown")
        if role == "user":
            turn += 1
            start, end = message_time_range(message)
            if start is None and pass_index < len(opencode_pass_times):
                start = opencode_pass_times[pass_index]
                pass_index += 1
            if start is None:
                start = _infer_message_start(items, scan_start)
            if end is None:
                end = start + timedelta(seconds=1)
            if end <= start:
                end = start + timedelta(milliseconds=1)
            items.append(
                TimelineItem(
                    kind="prompt",
                    name=f"prompt-turn-{turn}",
                    start=start,
                    end=end,
                    as_type="generation",
                    model=default_model,
                    input={"prompt": message_prompt_text(message) or message},
                    output={"role": "user", "messageId": message.get("messageId")},
                    metadata={"turn": turn, "messageId": message.get("messageId")},
                )
            )
            continue

        if role == "assistant":
            if turn == 0:
                turn += 1
            start, end = message_time_range(message)
            if start is None:
                start = _infer_message_start(items, scan_start)
            if end is None:
                end = start + timedelta(seconds=30)
            if end <= start:
                end = start + timedelta(milliseconds=1)
            assistant = TimelineItem(
                kind="assistant",
                name=f"turn-{turn}-assistant",
                start=start,
                end=end,
                metadata={
                    "turn": turn,
                    "messageId": message.get("messageId"),
                    "providerId": message.get("providerId"),
                    "modelId": message.get("modelId") or default_model,
                    "finish": message.get("finish"),
                    "error": message.get("error"),
                },
                children=_assistant_children(
                    message,
                    turn=turn,
                    default_start=start,
                    default_end=end,
                    default_model=str(message.get("modelId") or default_model),
                ),
            )
            assistant_end = max(
                [assistant.end, *[child.end for child in assistant.children]],
                default=assistant.end,
            )
            assistant.end = assistant_end
            items.append(assistant)

    items.sort(key=lambda item: (item.start, item.name))
    return items


def _assistant_children(
    message: dict[str, Any],
    *,
    turn: int,
    default_start: datetime,
    default_end: datetime,
    default_model: str,
) -> list[TimelineItem]:
    children: list[TimelineItem] = []
    agent_step = 0
    model_step = 0
    pending_text: list[str] = []
    pending_reasoning: list[str] = []

    for part in message_parts(message):
        part_type = part.get("type")

        if part_type == "step-start":
            agent_step += 1
            start, end = part_time_range(part, default_start, default_end)
            children.append(
                TimelineItem(
                    kind="agent-step",
                    name=f"agent-step-{agent_step}",
                    start=start,
                    end=end,
                    input={"snapshot": part.get("snapshot")},
                    metadata={"turn": turn, "agentStep": agent_step},
                )
            )
            continue

        if part_type == "reasoning":
            text = part_text(part, include_reasoning=True)
            if not text:
                continue
            pending_reasoning.append(text)
            start, end = part_time_range(part, default_start, default_end)
            children.append(
                TimelineItem(
                    kind="reasoning",
                    name="reasoning",
                    start=start,
                    end=end,
                    output=text,
                )
            )
            continue

        if part_type == "tool":
            payload = tool_part_payload(part)
            start, end = tool_time_range(payload.get("time"), default_start, default_end)
            children.append(
                TimelineItem(
                    kind="tool",
                    name=str(payload["tool"]),
                    start=start,
                    end=end,
                    as_type="tool",
                    level="ERROR" if str(payload.get("status") or "") == "error" else "DEFAULT",
                    input=payload.get("input"),
                    output=payload.get("output"),
                    metadata={
                        "turn": turn,
                        "agentStep": agent_step or None,
                        "callId": payload.get("callId"),
                        "status": payload.get("status"),
                        "title": payload.get("title"),
                        "error": payload.get("error"),
                    },
                )
            )
            continue

        if part_type == "text":
            text = part_text(part, include_reasoning=False)
            if text:
                pending_text.append(text)
            continue

        if part_type == "patch":
            start, end = part_time_range(part, default_start, default_end)
            children.append(
                TimelineItem(
                    kind="patch",
                    name="file-patch",
                    start=start,
                    end=end,
                    output={
                        "hash": part.get("hash"),
                        "files": part.get("files"),
                    },
                )
            )
            continue

        if part_type == "file":
            start, end = part_time_range(part, default_start, default_end)
            children.append(
                TimelineItem(
                    kind="file",
                    name="file",
                    start=start,
                    end=end,
                    output={
                        "mime": part.get("mime"),
                        "filename": part.get("filename"),
                        "url": part.get("url"),
                    },
                )
            )
            continue

        if part_type == "step-finish":
            model_step += 1
            start, end = part_time_range(part, default_start, default_end)
            usage_details, cost_details = _map_usage(part)
            output = _combine_output(pending_text, pending_reasoning) or {
                "reason": part.get("reason"),
                "snapshot": part.get("snapshot"),
                "cost": part.get("cost"),
            }
            children.append(
                TimelineItem(
                    kind="model-step",
                    name=f"model-step-{model_step}",
                    start=start,
                    end=end,
                    as_type="generation",
                    model=default_model,
                    input={
                        "turn": turn,
                        "agentStep": agent_step or None,
                        "reasoning": pending_reasoning or None,
                    },
                    output=output,
                    usage_details=usage_details,
                    cost_details=cost_details,
                    metadata={
                        "turn": turn,
                        "agentStep": agent_step or None,
                        "modelStep": model_step,
                        "providerId": message.get("providerId"),
                        "reason": part.get("reason"),
                        "cost": part.get("cost"),
                    },
                )
            )
            pending_text = []
            pending_reasoning = []

    if pending_text or pending_reasoning:
        children.append(
            TimelineItem(
                kind="assistant-text",
                name="assistant-output",
                start=default_start,
                end=default_end,
                output=_combine_output(pending_text, pending_reasoning),
            )
        )

    children.sort(key=lambda item: (item.start, item.name))
    return children


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 1e14:
            numeric /= 1_000_000
        elif numeric > 1e11:
            numeric /= 1_000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def message_time_range(message: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    time_obj = message.get("time")
    time_obj = time_obj if isinstance(time_obj, dict) else {}
    start = parse_timestamp(
        time_obj.get("created")
        or time_obj.get("start")
        or time_obj.get("started")
    )
    end = parse_timestamp(
        time_obj.get("completed")
        or time_obj.get("end")
        or time_obj.get("finished")
    )
    return start, end


def part_time_range(
    part: dict[str, Any],
    default_start: datetime,
    default_end: datetime,
) -> tuple[datetime, datetime]:
    time_obj = part.get("time")
    time_obj = time_obj if isinstance(time_obj, dict) else {}
    start = parse_timestamp(time_obj.get("start") or time_obj.get("created")) or default_start
    end = parse_timestamp(time_obj.get("end") or time_obj.get("completed")) or default_end
    if end <= start:
        end = start + timedelta(milliseconds=1)
    return start, end


def tool_time_range(
    time_obj: Any,
    default_start: datetime,
    default_end: datetime,
) -> tuple[datetime, datetime]:
    time_obj = time_obj if isinstance(time_obj, dict) else {}
    start = parse_timestamp(time_obj.get("start") or time_obj.get("created")) or default_start
    end = parse_timestamp(time_obj.get("end") or time_obj.get("completed")) or default_end
    if end <= start:
        end = start + timedelta(milliseconds=1)
    return start, end


def datetime_to_ns(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


def _infer_message_start(items: list[TimelineItem], scan_start: datetime | None) -> datetime:
    if items:
        latest = max(item.end for item in items)
        return latest + timedelta(milliseconds=1)
    if scan_start is not None:
        return scan_start
    return datetime.now(timezone.utc)


def _event_level(level: Any) -> str:
    normalized = str(level or "info").lower()
    if normalized == "error":
        return "ERROR"
    if normalized == "warn":
        return "WARNING"
    return "DEFAULT"


def _map_usage(part: dict[str, Any]) -> tuple[dict[str, int] | None, dict[str, float] | None]:
    tokens = part.get("tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    cache = tokens.get("cache")
    cache = cache if isinstance(cache, dict) else {}
    usage: dict[str, int] = {}
    for source_key, target_key in (
        ("input", "input"),
        ("output", "output"),
        ("reasoning", "reasoning"),
        ("total", "total"),
    ):
        value = _int_or_none(tokens.get(source_key))
        if value is not None:
            usage[target_key] = value
    cache_read = _int_or_none(cache.get("read"))
    if cache_read is not None:
        usage["cache_read_input_tokens"] = cache_read
    cache_write = _int_or_none(cache.get("write"))
    if cache_write is not None:
        usage["cache_creation_input_tokens"] = cache_write
    cost_details = None
    cost = _float_or_none(part.get("cost"))
    if cost is not None:
        cost_details = {"total": cost}
    return usage or None, cost_details


def _combine_output(text_parts: list[str], reasoning_parts: list[str]) -> Any:
    text = "\n".join(text_parts).strip()
    reasoning = "\n".join(reasoning_parts).strip()
    if text and reasoning:
        return {"text": text, "reasoning": reasoning}
    if text:
        return text
    if reasoning:
        return {"reasoning": reasoning}
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
