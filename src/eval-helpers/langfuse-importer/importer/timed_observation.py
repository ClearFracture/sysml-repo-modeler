from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from opentelemetry import trace as otel_trace_api

from .timeline import TimelineItem, datetime_to_ns


def observation_seed(
    *,
    trace_id: str,
    parent_span_id: str | None,
    item: TimelineItem,
) -> str:
    parent = parent_span_id or "root"
    return f"{trace_id}:{parent}:{item.kind}:{item.name}"


def emit_timeline(
    langfuse: Any,
    *,
    trace_id: str,
    parent_span_id: str | None,
    items: list[TimelineItem],
) -> None:
    for item in items:
        span_id = emit_timed_item(
            langfuse,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            item=item,
        )
        if item.children and span_id:
            emit_timeline(
                langfuse,
                trace_id=trace_id,
                parent_span_id=span_id,
                items=item.children,
            )


def emit_timed_item(
    langfuse: Any,
    *,
    trace_id: str,
    parent_span_id: str | None,
    item: TimelineItem,
) -> str | None:
    start_ns = datetime_to_ns(item.start)
    end_ns = datetime_to_ns(item.end)
    if end_ns <= start_ns:
        end_ns = start_ns + 1_000_000

    remote_parent = langfuse._create_remote_parent_span(
        trace_id=trace_id,
        parent_span_id=parent_span_id,
    )
    with otel_trace_api.use_span(remote_parent):
        otel_span = langfuse._otel_tracer.start_span(item.name, start_time=start_ns)

    observation = langfuse._create_observation_from_otel_span(
        otel_span=otel_span,
        as_type=item.as_type,
        input=item.input,
        output=item.output,
        metadata={
            **item.metadata,
            "startTime": _iso(item.start),
            "endTime": _iso(item.end),
            "observationSeed": observation_seed(
                trace_id=trace_id,
                parent_span_id=parent_span_id,
                item=item,
            ),
        },
        level=item.level,
        model=item.model,
        usage_details=item.usage_details,
        cost_details=item.cost_details,
    )
    observation.end(end_time=end_ns)

    span_context = otel_span.get_span_context()
    span_id = getattr(span_context, "span_id", 0)
    if isinstance(span_id, int) and span_id:
        return format(span_id, "016x")
    return None


def emit_timed_root(
    langfuse: Any,
    *,
    trace_id: str,
    name: str,
    start: datetime | None,
    end: datetime | None,
    input: Any = None,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    now = datetime.now(timezone.utc)
    item = TimelineItem(
        kind="scan-root",
        name=name,
        start=start or now,
        end=end or (start or now),
        input=input,
        output=output,
        metadata=metadata or {},
    )
    if item.end <= item.start:
        item.end = item.start
    return emit_timed_item(
        langfuse,
        trace_id=trace_id,
        parent_span_id=None,
        item=item,
    )


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()
