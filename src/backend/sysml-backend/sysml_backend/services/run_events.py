from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    phase: str
    level: str
    message: str
    timestamp: str
    entity: str | None = None
    reasoning_summary: str | None = None
    evidence_refs: list[str] | None = None
    confidence: float | None = None


class RunEventStore:
    """Run events persisted directly in Postgres."""

    def __init__(self, database_url: str) -> None:
        import psycopg

        self.database_url = database_url
        self._psycopg = psycopg
        self._lock = threading.RLock()
        self._connection: Any | None = None

    def append(
        self,
        slug: str,
        run_id: str,
        phase: str,
        level: str,
        message: str,
        *,
        entity: str | None = None,
        reasoning_summary: str | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            phase=phase,
            level=level,
            message=message,
            timestamp=datetime.now(timezone.utc).isoformat(),
            entity=entity,
            reasoning_summary=reasoning_summary,
            evidence_refs=evidence_refs,
            confidence=confidence,
        )

        del slug
        with self._lock:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into run_events (
                      run_id, phase, level, message, timestamp, entity,
                      reasoning_summary, evidence_refs, confidence
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.run_id,
                        event.phase,
                        event.level,
                        event.message,
                        datetime.fromisoformat(event.timestamp),
                        event.entity,
                        event.reasoning_summary,
                        Jsonb(event.evidence_refs) if event.evidence_refs else None,
                        event.confidence,
                    ),
                )
            connection.commit()
        logger.info("%s", _format_console_event(event))
        return event

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select
                      run_id, phase, level, message, timestamp, entity,
                      reasoning_summary, evidence_refs, confidence
                    from run_events
                    where run_id = %s
                    order by id
                    """,
                    (run_id,),
                )
                rows = cursor.fetchall()
            connection.commit()
        return [_event_row_to_json(row) for row in rows]

    def _connect(self) -> Any:
        if self._connection is None or self._connection.closed:
            self._connection = self._psycopg.connect(self.database_url)
        return self._connection


def _format_console_event(event: RunEvent) -> str:
    return (
        f"[run:{event.run_id} phase:{event.phase} level:{event.level}] {event.message}"
    )


def _event_row_to_json(row: tuple[Any, ...]) -> dict[str, Any]:
    event = RunEvent(
        run_id=row[0],
        phase=row[1],
        level=row[2],
        message=row[3],
        timestamp=row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
        entity=row[5],
        reasoning_summary=row[6],
        evidence_refs=row[7] if isinstance(row[7], list) else None,
        confidence=row[8],
    )
    return asdict(event)
