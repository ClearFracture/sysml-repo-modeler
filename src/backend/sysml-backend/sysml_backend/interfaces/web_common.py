from __future__ import annotations

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
