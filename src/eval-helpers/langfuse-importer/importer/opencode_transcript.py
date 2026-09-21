from __future__ import annotations

from typing import Any


def coerce_messages(session: dict[str, Any]) -> list[dict[str, Any]]:
    raw_messages = session.get("messages")
    if not isinstance(raw_messages, list):
        return []
    messages: list[dict[str, Any]] = []
    for raw in raw_messages:
        if not isinstance(raw, dict):
            continue
        normalized = normalize_message(raw)
        if normalized is not None:
            messages.append(normalized)
    return messages


def normalize_message(message: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(message.get("role"), str) and message.get("parts") is not None:
        parts = message.get("parts")
        return {
            **message,
            "role": str(message["role"]).strip(),
            "parts": [part for part in parts if isinstance(part, dict)]
            if isinstance(parts, list)
            else [],
        }

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
        "messageId": info.get("id") or message.get("messageId"),
        "providerId": info.get("providerID") or model.get("providerID") or message.get("providerId"),
        "modelId": info.get("modelID") or model.get("modelID") or model.get("id") or message.get("modelId"),
        "parentId": info.get("parentID") or message.get("parentId"),
        "agent": info.get("agent") or message.get("agent"),
        "time": info.get("time") or message.get("time"),
        "cost": info.get("cost") if info.get("cost") is not None else message.get("cost"),
        "tokens": info.get("tokens") or message.get("tokens"),
        "finish": info.get("finish") or message.get("finish"),
        "error": info.get("error") or message.get("error"),
        "parts": parts,
    }


def message_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    parts = message.get("parts")
    if not isinstance(parts, list):
        return []
    return [part for part in parts if isinstance(part, dict)]


def part_text(part: dict[str, Any], *, include_reasoning: bool = True) -> str | None:
    part_type = part.get("type")
    allowed = {"text", None}
    if include_reasoning:
        allowed = allowed | {"reasoning"}
    if part_type not in allowed:
        return None
    for key in ("text", "content", "delta"):
        value = part.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def tool_part_payload(part: dict[str, Any]) -> dict[str, Any]:
    state = part.get("state")
    state = state if isinstance(state, dict) else {}
    return {
        "tool": str(part.get("tool") or state.get("tool") or "unknown"),
        "callId": part.get("callID") or part.get("callId"),
        "status": state.get("status"),
        "title": state.get("title"),
        "input": state.get("input"),
        "output": state.get("output"),
        "error": state.get("error"),
        "metadata": state.get("metadata"),
        "attachments": state.get("attachments"),
        "time": state.get("time"),
    }


def message_prompt_text(message: dict[str, Any]) -> str:
    return "\n".join(
        text
        for part in message_parts(message)
        if (text := part_text(part, include_reasoning=False)) is not None
    )
