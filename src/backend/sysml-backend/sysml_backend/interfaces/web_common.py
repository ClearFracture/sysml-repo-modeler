from __future__ import annotations

from typing import Any

# Artifact filenames the HTTP layer is allowed to serve from the artifact table.
ALLOWED_ARTIFACTS = frozenset(
    {
        "suite-model.sysml",
        "suite-evidence.json",
        "unresolved-services.md",
    }
)


def is_unsafe_segment(segment: str) -> bool:
    """Reject path segments that would escape the output directory.

    Route patterns permit dots, so ``.`` / ``..`` (and any embedded separator)
    must be rejected before a segment is joined into a filesystem path.
    """
    return segment in {".", ".."} or "/" in segment or "\\" in segment


def telemetry_enabled_from_payload(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    value = payload.get("telemetryEnabled", payload.get("telemetry_enabled"))
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
