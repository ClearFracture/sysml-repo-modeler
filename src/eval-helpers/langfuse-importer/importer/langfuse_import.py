from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langfuse import Langfuse, propagate_attributes

from .config_store import ImporterConfig
from .import_state import scan_version_from_manifest
from .opencode_transcript import coerce_messages
from .timeline import TimelineItem, build_timeline, parse_timestamp
from .timed_observation import emit_timeline, emit_timed_root

IMPORTER_NAME = "sysml-repo-modeler-langfuse-importer"
IMPORTER_VERSION = "0.4.0"


class LangfuseImportError(Exception):
    pass


def import_bundle(
    bundle_dir: Path,
    *,
    config: ImporterConfig,
    scan_version: str | None = None,
) -> dict[str, Any]:
    if not config.langfuse_public_key or not config.langfuse_secret_key:
        raise LangfuseImportError("Langfuse API keys are not configured.")

    manifest = _load_json(bundle_dir / "manifest.json")
    session = _load_json(bundle_dir / "opencode" / "session.json")
    events = _load_events(bundle_dir / "events.jsonl")
    scores_path = bundle_dir / "scores.json"
    scores = _load_json(scores_path) if scores_path.is_file() else {}
    validation_path = bundle_dir / "validation.json"
    validation = _load_json(validation_path) if validation_path.is_file() else {}

    run_id = str(manifest.get("runId") or "")
    if not run_id:
        raise LangfuseImportError("Bundle manifest is missing runId.")

    model = manifest.get("model")
    model = model if isinstance(model, dict) else {}
    default_model = str(model.get("modelId") or "unknown")
    default_provider = str(model.get("providerId") or "")

    project_session_id = project_session_id_for_manifest(manifest)
    resolved_scan_version = scan_version or scan_version_from_manifest(manifest)
    metadata = _build_metadata(manifest, config, resolved_scan_version)
    tags = _build_tags(manifest, config, resolved_scan_version)
    messages = coerce_messages(session)

    langfuse = Langfuse(
        public_key=config.langfuse_public_key,
        secret_key=config.langfuse_secret_key,
        host=config.langfuse_host.rstrip("/"),
    )
    trace_id = langfuse.create_trace_id(seed=run_id)

    try:
        with propagate_attributes(
            session_id=project_session_id,
            trace_name=f"scan/{run_id[:8]}",
            metadata=metadata,
            tags=tags,
        ):
            scan_start = parse_timestamp(manifest.get("startedAt"))
            scan_end = parse_timestamp(manifest.get("completedAt"))
            scan_root_span_id = emit_timed_root(
                langfuse,
                trace_id=trace_id,
                name=f"scan-{run_id[:8]}",
                start=scan_start,
                end=scan_end,
                input={
                    "project": manifest.get("projectName"),
                    "projectSlug": manifest.get("projectSlug"),
                    "trigger": manifest.get("trigger"),
                    "repositories": manifest.get("repositories"),
                    "model": model,
                    "scanVersion": resolved_scan_version,
                },
                output={
                    "status": manifest.get("status"),
                    "validation": validation or None,
                    "opencodeUsage": manifest.get("opencodeUsage"),
                },
                metadata={
                    **metadata,
                    "startedAt": manifest.get("startedAt"),
                    "completedAt": manifest.get("completedAt"),
                },
            )

            timeline = build_timeline(
                events,
                messages,
                manifest,
                default_model=default_model,
            )
            cost_item = _scan_cost_summary_item(manifest, session, default_model, scan_end)
            if cost_item is not None:
                timeline.append(cost_item)
                timeline.sort(key=lambda item: (item.start, item.name))

            emit_timeline(
                langfuse,
                trace_id=trace_id,
                parent_span_id=scan_root_span_id,
                items=timeline,
            )

        for score_name, score in (scores.get("scores") or {}).items():
            if not isinstance(score, dict):
                continue
            value = score.get("value")
            if value is None:
                continue
            langfuse.create_score(
                name=str(score_name),
                value=value,
                comment=score.get("comment"),
                session_id=project_session_id,
                trace_id=trace_id,
                metadata={
                    "scanVersion": resolved_scan_version,
                    "sysmlRunId": run_id,
                    "sysmlProjectSlug": manifest.get("projectSlug"),
                },
            )

        langfuse.flush()
    except Exception as error:
        raise LangfuseImportError(str(error)) from error

    return {
        "runId": run_id,
        "langfuseSessionId": project_session_id,
        "langfuseTraceId": trace_id,
        "scanVersion": resolved_scan_version,
        "tags": tags,
        "timelineCount": len(timeline),
        "defaultProvider": default_provider or None,
    }


def project_session_id_for_manifest(manifest: dict[str, Any]) -> str:
    project_slug = manifest.get("projectSlug")
    if isinstance(project_slug, str) and project_slug.strip():
        return f"sysml-project:{project_slug.strip()}"
    run_id = manifest.get("runId")
    if isinstance(run_id, str) and run_id.strip():
        return f"sysml-run:{run_id.strip()}"
    return "sysml-unknown"


def map_token_usage(
    tokens: dict[str, Any] | None,
    *,
    cost: Any = None,
) -> tuple[dict[str, int] | None, dict[str, float] | None]:
    if not isinstance(tokens, dict):
        tokens = {}
    cache = tokens.get("cache")
    cache = cache if isinstance(cache, dict) else {}

    usage_details: dict[str, int] = {}
    mapping = (
        ("input", "input"),
        ("output", "output"),
        ("reasoning", "reasoning"),
        ("total", "total"),
        ("cacheRead", "cache_read_input_tokens"),
        ("cacheWrite", "cache_creation_input_tokens"),
    )
    for source_key, target_key in mapping:
        value = _int_or_none(tokens.get(source_key))
        if value is not None:
            usage_details[target_key] = value

    if "cache_read_input_tokens" not in usage_details:
        cache_read = _int_or_none(cache.get("read"))
        if cache_read is not None:
            usage_details["cache_read_input_tokens"] = cache_read
    if "cache_creation_input_tokens" not in usage_details:
        cache_write = _int_or_none(cache.get("write"))
        if cache_write is not None:
            usage_details["cache_creation_input_tokens"] = cache_write

    cost_details: dict[str, float] | None = None
    parsed_cost = _float_or_none(cost)
    if parsed_cost is not None:
        cost_details = {"total": parsed_cost}

    return usage_details or None, cost_details


def _scan_cost_summary_item(
    manifest: dict[str, Any],
    session: dict[str, Any],
    default_model: str,
    scan_end: Any,
) -> TimelineItem | None:
    usage = session.get("usage")
    usage = usage if isinstance(usage, dict) else manifest.get("opencodeUsage")
    usage = usage if isinstance(usage, dict) else {}
    if not usage:
        return None

    usage_details: dict[str, int] = {}
    for source_key, target_key in (
        ("inputTokens", "input"),
        ("outputTokens", "output"),
        ("reasoningTokens", "reasoning"),
        ("cacheReadTokens", "cache_read_input_tokens"),
        ("cacheWriteTokens", "cache_creation_input_tokens"),
        ("totalTokens", "total"),
    ):
        value = _int_or_none(usage.get(source_key))
        if value is not None:
            usage_details[target_key] = value

    cost = _float_or_none(usage.get("cost")) or _float_or_none(
        usage.get("calculatedCost")
    )
    cost_details = {"total": cost} if cost is not None else None
    if not usage_details and not cost_details:
        return None

    end = parse_timestamp(scan_end) or parse_timestamp(manifest.get("completedAt"))
    if end is None:
        from datetime import datetime, timezone

        end = datetime.now(timezone.utc)

    from datetime import timedelta

    start = end - timedelta(milliseconds=1)
    return TimelineItem(
        kind="cost-summary",
        name="scan-cost-summary",
        start=start,
        end=end,
        as_type="generation",
        model=default_model,
        output={
            "requestCount": usage.get("requestCount"),
            "cost": cost,
        },
        usage_details=usage_details or None,
        cost_details=cost_details,
        metadata={"scope": "scan-total"},
    )


def _build_metadata(
    manifest: dict[str, Any],
    config: ImporterConfig,
    scan_version: str,
) -> dict[str, Any]:
    return {
        "source": IMPORTER_NAME,
        "sourceVersion": IMPORTER_VERSION,
        "sysmlRunId": manifest.get("runId"),
        "sysmlProjectSlug": manifest.get("projectSlug"),
        "sysmlProjectName": manifest.get("projectName"),
        "sysmlScanStatus": manifest.get("status"),
        "sysmlTrigger": manifest.get("trigger"),
        "sysmlExportedAt": manifest.get("exportedAt"),
        "sysmlBundleSchemaVersion": manifest.get("schemaVersion"),
        "sysmlOpenCodeSessionId": manifest.get("opencodeSessionId"),
        "langfuseProjectName": config.langfuse_project_name or None,
        "scanVersion": scan_version,
    }


def _build_tags(
    manifest: dict[str, Any],
    config: ImporterConfig,
    scan_version: str,
) -> list[str]:
    tags = [
        "sysml-repo-modeler",
        f"scan-version:{scan_version}",
    ]
    project_slug = manifest.get("projectSlug")
    if isinstance(project_slug, str) and project_slug.strip():
        tags.append(f"project:{project_slug.strip()}")
    if config.langfuse_project_name.strip():
        tags.append(f"langfuse-project:{config.langfuse_project_name.strip()}")
    scan_status = manifest.get("status")
    if isinstance(scan_status, str) and scan_status.strip():
        tags.append(f"scan-status:{scan_status.strip()}")
    return tags


def _load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


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
