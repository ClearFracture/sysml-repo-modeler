from __future__ import annotations

from typing import Any


def pick(document: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present, non-None value among ``keys``.

    Persisted documents mix snake_case (``dataclasses.asdict``) and camelCase
    (hand-built API payloads). Rather than scatter ``d.get(a) or d.get(b)`` reads,
    callers funnel alias lookups through this one helper.
    """
    for key in keys:
        if key in document and document[key] is not None:
            return document[key]
    return default
