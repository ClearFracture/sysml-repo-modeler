from __future__ import annotations

from typing import Any


def normalize_opencode_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize OpenCode v2 `{info, parts}` messages for telemetry export."""
    info = message.get("info")
    info = info if isinstance(info, dict) else {}

    role = info.get("role") or message.get("type") or message.get("role")
    if not isinstance(role, str) or not role.strip():
        return None

    parts = message.get("parts")
    parts = [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []

    model = info.get("model")
    model = model if isinstance(model, dict) else {}

    return {
        "role": role.strip(),
        "messageId": info.get("id"),
        "providerId": info.get("providerID") or model.get("providerID"),
        "modelId": info.get("modelID") or model.get("modelID") or model.get("id"),
        "parentId": info.get("parentID"),
        "agent": info.get("agent"),
        "time": info.get("time"),
        "cost": info.get("cost"),
        "tokens": info.get("tokens"),
        "finish": info.get("finish"),
        "error": info.get("error"),
        "parts": parts,
    }


def message_role(message: dict[str, Any]) -> str:
    if isinstance(message.get("role"), str) and message["role"].strip():
        return message["role"].strip()
    info = message.get("info")
    info = info if isinstance(info, dict) else {}
    for candidate in (info.get("role"), message.get("type"), message.get("role")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "unknown"


def message_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    parts = message.get("parts")
    if not isinstance(parts, list):
        return []
    return [part for part in parts if isinstance(part, dict)]


def part_text(part: dict[str, Any]) -> str | None:
    part_type = part.get("type")
    if part_type not in {"text", "reasoning", None}:
        return None
    for key in ("text", "content", "delta"):
        value = part.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def tool_part_payload(part: dict[str, Any]) -> dict[str, Any]:
    state = part.get("state")
    state = state if isinstance(state, dict) else {}
    tool_name = str(part.get("tool") or state.get("tool") or "unknown")
    return {
        "tool": tool_name,
        "callId": part.get("callID") or part.get("callId"),
        "status": state.get("status"),
        "title": state.get("title"),
        "input": state.get("input"),
        "output": _tool_output_value(state.get("output")),
        "error": state.get("error"),
        "metadata": state.get("metadata"),
        "attachments": state.get("attachments"),
        "time": state.get("time"),
    }


def step_finish_payload(part: dict[str, Any]) -> dict[str, Any]:
    tokens = part.get("tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    cache = tokens.get("cache")
    cache = cache if isinstance(cache, dict) else {}
    return {
        "reason": part.get("reason"),
        "snapshot": part.get("snapshot"),
        "cost": part.get("cost"),
        "tokens": {
            "input": tokens.get("input"),
            "output": tokens.get("output"),
            "reasoning": tokens.get("reasoning"),
            "total": tokens.get("total"),
            "cacheRead": cache.get("read"),
            "cacheWrite": cache.get("write"),
        },
    }


def message_prompt_text(message: dict[str, Any]) -> str:
    return "\n".join(
        text
        for part in message_parts(message)
        if (text := part_text(part)) is not None
    )


def message_assistant_text(message: dict[str, Any]) -> str:
    lines: list[str] = []
    for part in message_parts(message):
        if part.get("type") != "text":
            continue
        text = part_text(part)
        if text:
            lines.append(text)
    return "\n".join(lines)


def _tool_output_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]") and len(stripped) <= 120:
            return stripped
        return value
    return value
