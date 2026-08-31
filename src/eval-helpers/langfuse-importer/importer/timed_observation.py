from __future__ import annotations

import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from opentelemetry import trace as otel_trace_api

from .timeline import TimelineItem, datetime_to_ns

_requested_span_id: ContextVar[int | None] = ContextVar(
    "langfuse_importer_requested_span_id",
    default=None,
)
_span_id_generator_lock = threading.RLock()


def observation_seed(
    *,
    trace_id: str,
    parent_span_id: str | None,
    item: TimelineItem,
    sibling_index: int,
) -> str:
    parent = parent_span_id or "root"
    return f"{trace_id}:{parent}:{sibling_index}:{item.kind}:{item.name}"


def observation_id_for_seed(seed: str) -> str:
    return sha256(seed.encode("utf-8")).digest()[:8].hex()


def emit_timeline(
    langfuse: Any,
    *,
    trace_id: str,
    parent_span_id: str | None,
    items: list[TimelineItem],
) -> None:
    for sibling_index, item in enumerate(items):
        span_id = emit_timed_item(
            langfuse,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            item=item,
            sibling_index=sibling_index,
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
    sibling_index: int = 0,
) -> str | None:
    start_ns = datetime_to_ns(item.start)
    end_ns = datetime_to_ns(item.end)
    if end_ns <= start_ns:
        end_ns = start_ns + 1_000_000

    seed = observation_seed(
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        item=item,
        sibling_index=sibling_index,
    )
    observation_id = observation_id_for_seed(seed)
    remote_parent = langfuse._create_remote_parent_span(
        trace_id=trace_id,
        parent_span_id=parent_span_id,
    )
    with otel_trace_api.use_span(remote_parent):
        otel_span = _start_span_with_id(
            langfuse._otel_tracer,
            name=item.name,
            start_time=start_ns,
            observation_id=observation_id,
        )

    observation = langfuse._create_observation_from_otel_span(
        otel_span=otel_span,
        as_type=item.as_type,
        input=item.input,
        output=item.output,
        metadata={
            **item.metadata,
            "startTime": _iso(item.start),
            "endTime": _iso(item.end),
            "observationSeed": seed,
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
        sibling_index=0,
    )


def _start_span_with_id(
    tracer: Any,
    *,
    name: str,
    start_time: int,
    observation_id: str,
) -> Any:
    original_generator = getattr(tracer, "id_generator", None)
    if original_generator is None:
        raise RuntimeError(
            "Langfuse tracer does not expose an OpenTelemetry ID generator."
        )

    with _span_id_generator_lock:
        tracer.id_generator = _ScopedSpanIdGenerator(original_generator)
        token = _requested_span_id.set(int(observation_id, 16))
        try:
            return tracer.start_span(name, start_time=start_time)
        finally:
            _requested_span_id.reset(token)
            tracer.id_generator = original_generator


class _ScopedSpanIdGenerator:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def generate_span_id(self) -> int:
        requested = _requested_span_id.get()
        if requested is not None:
            return requested
        return self._delegate.generate_span_id()

    def generate_trace_id(self) -> int:
        return self._delegate.generate_trace_id()

    def is_trace_id_random(self) -> bool:
        checker = getattr(self._delegate, "is_trace_id_random", None)
        return bool(checker()) if checker is not None else False


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()
