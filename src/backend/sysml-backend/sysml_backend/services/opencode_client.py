from __future__ import annotations

import base64
import json
import logging
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .sysml_prompts import (
    analysis_prompt,
    coverage_repair_prompt,
    enrichment_prompt,
    repair_prompt,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenCodeConfig:
    base_url: str | None = None
    health_path: str = "/global/health"
    username: str = "opencode"
    password: str | None = None
    timeout_seconds: float = 600.0
    model_id: str | None = None
    provider_id: str | None = None
    agent: str | None = None


@dataclass(frozen=True)
class OpenCodeHealth:
    configured: bool
    status: str
    base_url: str | None
    message: str


@dataclass(frozen=True)
class OpenCodeAnalysisResult:
    session_id: str | None
    sysml_content: str | None
    tool_errors: list[dict[str, str]]


class OpenCodeProtocolError(Exception):
    pass


class OpenCodeClient:
    def __init__(self, config: OpenCodeConfig) -> None:
        self.config = config

    def health(self) -> OpenCodeHealth:
        if not self.config.base_url:
            return OpenCodeHealth(
                configured=False,
                status="unconfigured",
                base_url=None,
                message="OPENCODE_BASE_URL is not configured.",
            )

        try:
            body = self._request_text("GET", self.config.health_path, None)
            return OpenCodeHealth(
                configured=True,
                status="ok",
                base_url=self.config.base_url,
                message=_health_message(body),
            )
        except HTTPError as error:
            return OpenCodeHealth(
                configured=True,
                status="error",
                base_url=self.config.base_url,
                message=f"OpenCode health endpoint returned HTTP {error.code}.",
            )
        except URLError as error:
            return OpenCodeHealth(
                configured=True,
                status="unreachable",
                base_url=self.config.base_url,
                message=f"OpenCode health endpoint is unreachable: {error.reason}",
            )
        except TimeoutError:
            return OpenCodeHealth(
                configured=True,
                status="timeout",
                base_url=self.config.base_url,
                message="OpenCode health endpoint timed out.",
            )

    def ping(self) -> dict[str, Any]:
        """Create a test session, send a one-word prompt, and return the result."""
        if not self.config.base_url:
            return {
                "status": "unconfigured",
                "message": "OPENCODE_BASE_URL is not set.",
            }
        try:
            session = self._create_session("ping")
            session_id = str(session.get("id") or "")
            if not session_id:
                return {
                    "status": "error",
                    "message": "OpenCode did not return a session id.",
                }
            response = self._request_any(
                "POST",
                f"/session/{session_id}/message",
                {
                    "parts": [
                        {"type": "text", "text": "Respond with one word: acknowledged."}
                    ]
                },
            )
            messages = _extract_assistant_messages(response)
            return {
                "status": "ok",
                "sessionId": session_id,
                "response": messages[0] if messages else None,
                "rawEvents": len(response) if isinstance(response, list) else 1,
            }
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            return {
                "status": "error",
                "message": _opencode_error_message(error.code, body),
            }
        except URLError as error:
            return {
                "status": "unreachable",
                "message": f"OpenCode is unreachable: {error.reason}",
            }
        except TimeoutError:
            return {"status": "timeout", "message": "OpenCode request timed out."}
        except OpenCodeProtocolError as error:
            return {"status": "error", "message": str(error)}

    def run_analysis(
        self,
        run_id: str,
        package_context: dict[str, Any],
        *,
        on_oc_event: Callable[[str, str], None] | None = None,
    ) -> OpenCodeAnalysisResult:
        """Build the SysMLv2 model in two passes and return (session_id, sysml).

        If ``on_oc_event`` is provided, OpenCode's /event SSE stream is watched
        during each message send and real-time events (text deltas, tool calls,
        reasoning chunks) are forwarded as (phase, message) pairs.
        """
        if not self.config.base_url:
            return OpenCodeAnalysisResult(None, None, [])
        try:
            logger.info("[opencode] creating analysis session for run %s", run_id[:8])
            session = self._create_session(run_id)
            session_id = str(session.get("id") or "")
            if not session_id:
                logger.warning("[opencode] session creation failed — no id returned")
                return OpenCodeAnalysisResult(None, None, [])
            logger.info("[opencode] session created: %s", session_id)
            tool_errors: list[dict[str, str]] = []

            # Pass 1 — architecture (components, ports, connections).
            if on_oc_event:
                logger.info("[opencode:pass] Pass 1 of 2 — architecture analysis")
                on_oc_event("opencode_pass", "Pass 1 of 2 — architecture analysis")
            map_response = self._send_prompt_streaming(
                session_id, analysis_prompt(package_context), on_oc_event=on_oc_event
            )
            map_errors = _extract_tool_errors(map_response, "architecture")
            tool_errors.extend(map_errors)
            _emit_tool_errors(map_errors, on_oc_event)
            system_map = _extract_sysml_content(map_response)
            if not system_map:
                logger.warning("[opencode] pass 1 produced no SysML; nothing to enrich")
                return OpenCodeAnalysisResult(session_id, None, tool_errors)
            logger.info("[opencode] pass 1 architecture: %d chars", len(system_map))
            self._post_completion_marker(session_id)

            # Pass 2 — depth + purpose (drill into each repository, document each element).
            if on_oc_event:
                logger.info("[opencode:pass] Pass 2 of 2 — enrichment")
                on_oc_event("opencode_pass", "Pass 2 of 2 — enrichment")
            enrich_response = self._send_prompt_streaming(
                session_id,
                enrichment_prompt(package_context, system_map),
                on_oc_event=on_oc_event,
            )
            enrich_errors = _extract_tool_errors(enrich_response, "enrichment")
            tool_errors.extend(enrich_errors)
            _emit_tool_errors(enrich_errors, on_oc_event)
            enriched = _extract_sysml_content(enrich_response)
            if enriched:
                logger.info(
                    "[opencode] pass 2 documented model: %d chars", len(enriched)
                )
                return OpenCodeAnalysisResult(session_id, enriched, tool_errors)
            logger.warning(
                "[opencode] pass 2 produced no SysML; falling back to pass 1 model"
            )
            return OpenCodeAnalysisResult(session_id, system_map, tool_errors)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            logger.warning(
                "[opencode] HTTP error during analysis: %s",
                _opencode_error_message(error.code, body),
            )
            return OpenCodeAnalysisResult(None, None, [])
        except URLError as error:
            logger.warning("[opencode] unreachable during analysis: %s", error.reason)
            return OpenCodeAnalysisResult(None, None, [])
        except TimeoutError:
            logger.warning("[opencode] analysis request timed out")
            return OpenCodeAnalysisResult(None, None, [])
        except OpenCodeProtocolError as error:
            logger.warning("[opencode] protocol error during analysis: %s", error)
            return OpenCodeAnalysisResult(None, None, [])

    def repair_sysml(
        self,
        session_id: str,
        package_context: dict[str, Any],
        sysml_content: str,
        diagnostics: list[str],
        *,
        attempt: int,
        on_oc_event: Callable[[str, str], None] | None = None,
    ) -> OpenCodeAnalysisResult:
        if not self.config.base_url:
            return OpenCodeAnalysisResult(session_id, None, [])
        try:
            if on_oc_event:
                logger.info("[opencode:pass] Repair attempt %d", attempt)
                on_oc_event("opencode_pass", f"Repair attempt {attempt}")
            response = self._send_prompt_streaming(
                session_id,
                repair_prompt(
                    package_context,
                    sysml_content,
                    diagnostics,
                    attempt=attempt,
                ),
                on_oc_event=on_oc_event,
            )
            tool_errors = _extract_tool_errors(response, f"repair-{attempt}")
            _emit_tool_errors(tool_errors, on_oc_event)
            repaired = _extract_sysml_content(response)
            if repaired:
                logger.info(
                    "[opencode] repair attempt %d model: %d chars",
                    attempt,
                    len(repaired),
                )
            else:
                logger.warning(
                    "[opencode] repair attempt %d produced no SysML",
                    attempt,
                )
            return OpenCodeAnalysisResult(session_id, repaired, tool_errors)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            logger.warning(
                "[opencode] HTTP error during repair: %s",
                _opencode_error_message(error.code, body),
            )
            return OpenCodeAnalysisResult(session_id, None, [])
        except URLError as error:
            logger.warning("[opencode] unreachable during repair: %s", error.reason)
            return OpenCodeAnalysisResult(session_id, None, [])
        except TimeoutError:
            logger.warning("[opencode] repair request timed out")
            return OpenCodeAnalysisResult(session_id, None, [])
        except OpenCodeProtocolError as error:
            logger.warning("[opencode] protocol error during repair: %s", error)
            return OpenCodeAnalysisResult(session_id, None, [])

    def improve_sysml_coverage(
        self,
        session_id: str,
        package_context: dict[str, Any],
        sysml_content: str,
        diagnostics: list[str],
        *,
        attempt: int,
        on_oc_event: Callable[[str, str], None] | None = None,
    ) -> OpenCodeAnalysisResult:
        if not self.config.base_url:
            return OpenCodeAnalysisResult(session_id, None, [])
        try:
            if on_oc_event:
                logger.info("[opencode:pass] Coverage enhancement attempt %d", attempt)
                on_oc_event("opencode_pass", f"Coverage enhancement attempt {attempt}")
            response = self._send_prompt_streaming(
                session_id,
                coverage_repair_prompt(
                    package_context,
                    sysml_content,
                    diagnostics,
                    attempt=attempt,
                ),
                on_oc_event=on_oc_event,
            )
            tool_errors = _extract_tool_errors(response, f"coverage-{attempt}")
            _emit_tool_errors(tool_errors, on_oc_event)
            improved = _extract_sysml_content(response)
            if improved:
                logger.info(
                    "[opencode] coverage enhancement attempt %d model: %d chars",
                    attempt,
                    len(improved),
                )
            else:
                logger.warning(
                    "[opencode] coverage enhancement attempt %d produced no SysML",
                    attempt,
                )
            return OpenCodeAnalysisResult(session_id, improved, tool_errors)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            logger.warning(
                "[opencode] HTTP error during coverage enhancement: %s",
                _opencode_error_message(error.code, body),
            )
            return OpenCodeAnalysisResult(session_id, None, [])
        except URLError as error:
            logger.warning(
                "[opencode] unreachable during coverage enhancement: %s",
                error.reason,
            )
            return OpenCodeAnalysisResult(session_id, None, [])
        except TimeoutError:
            logger.warning("[opencode] coverage enhancement request timed out")
            return OpenCodeAnalysisResult(session_id, None, [])
        except OpenCodeProtocolError as error:
            logger.warning(
                "[opencode] protocol error during coverage enhancement: %s", error
            )
            return OpenCodeAnalysisResult(session_id, None, [])

    def list_sessions(self) -> list[dict[str, Any]]:
        if not self.config.base_url:
            return []
        try:
            response = self._request_any("GET", "/session", None)
            if isinstance(response, list):
                return response
            if isinstance(response, dict):
                items = response.get("items", [])
                return items if isinstance(items, list) else []
        except (HTTPError, URLError, TimeoutError, OpenCodeProtocolError):
            pass
        return []

    def get_session_usage(self, session_id: str | None) -> dict[str, Any]:
        if not session_id:
            return {}
        session = next(
            (
                item
                for item in self.list_sessions()
                if isinstance(item, dict) and str(item.get("id") or "") == session_id
            ),
            None,
        )
        session_usage = _usage_from_session(session) if session is not None else {}
        message_usage = _usage_from_messages(self.get_session_messages(session_id))
        return _merge_usage(session_usage, message_usage)

    def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        if not self.config.base_url:
            return []
        try:
            response = self._request_any("GET", f"/session/{session_id}/message", None)
            if isinstance(response, list):
                return response
            if isinstance(response, dict):
                items = response.get("items", [])
                return items if isinstance(items, list) else []
        except (HTTPError, URLError, TimeoutError, OpenCodeProtocolError):
            pass
        return []

    def _create_session(self, run_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {"title": f"SYSML {run_id[:8]}"}
        if self.config.model_id and self.config.provider_id:
            body["model"] = {
                "id": self.config.model_id,
                "providerID": self.config.provider_id,
            }
        if self.config.agent:
            body["agent"] = self.config.agent
        response = self._request_any("POST", "/session", body)
        return response if isinstance(response, dict) else {}

    def _send_message(
        self, session_id: str, text: str, system: str | None = None
    ) -> Any:
        body: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if self.config.model_id and self.config.provider_id:
            body["model"] = {
                "providerID": self.config.provider_id,
                "modelID": self.config.model_id,
            }
        if self.config.agent:
            body["agent"] = self.config.agent
        if system:
            body["system"] = system
        return self._request_any("POST", f"/session/{session_id}/message", body)

    def _send_prompt_streaming(
        self,
        session_id: str,
        text: str,
        *,
        on_oc_event: Callable[[str, str], None] | None = None,
    ) -> Any:
        """Send a message and watch /event in parallel for real-time text/reasoning deltas.

        Uses the confirmed-working POST /session/{id}/message endpoint (JSON response)
        while subscribing to GET /event (SSE) in a background thread for streaming events.
        """
        if not on_oc_event or not self.config.base_url:
            return self._send_message(session_id, text)

        stop = threading.Event()
        watcher = threading.Thread(
            target=self._watch_events,
            args=(session_id, on_oc_event, stop),
            daemon=True,
            name=f"oc-events-{session_id[:8]}",
        )
        watcher.start()
        try:
            return self._send_message(session_id, text)
        finally:
            stop.set()

    def _watch_events(
        self,
        session_id: str,
        on_oc_event: Callable[[str, str], None],
        stop: threading.Event,
    ) -> None:
        """Subscribe to GET /event SSE and forward text/reasoning deltas for this session."""
        url = _join_url(self.config.base_url or "", "/event")
        request = Request(url, method="GET")
        request.add_header("Accept", "text/event-stream")
        request.add_header("Cache-Control", "no-cache")
        if self.config.password:
            request.add_header(
                "Authorization",
                _basic_auth_header(self.config.username, self.config.password),
            )
        timeout = (
            self.config.timeout_seconds
            if self.config.timeout_seconds and self.config.timeout_seconds > 0
            else None
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                for raw_line in response:
                    if stop.is_set():
                        break
                    line = raw_line.decode("utf-8").rstrip("\r\n")
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "message.part.updated":
                        continue
                    props = event.get("properties", {})
                    if not isinstance(props, dict):
                        continue
                    part = props.get("part", {})
                    if not isinstance(part, dict):
                        continue
                    if part.get("sessionID") != session_id:
                        continue
                    part_type = part.get("type", "")
                    delta = props.get("delta")

                    if part_type == "text" and isinstance(delta, str) and delta:
                        logger.debug("[opencode:text] %s", delta)
                        on_oc_event("opencode_delta", delta)

                    elif part_type == "reasoning" and isinstance(delta, str) and delta:
                        logger.info("[opencode:thinking] %s", delta)
                        on_oc_event("opencode_reasoning", delta)

        except Exception as exc:
            logger.debug(
                "[opencode] event watch for session %s ended: %s", session_id[:8], exc
            )

    def _post_completion_marker(self, session_id: str) -> None:
        """Record a 'Complete @ <time>' marker in the session transcript."""
        marker = (
            f"Complete @ {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )
        try:
            self._request_any(
                "POST",
                f"/session/{session_id}/message",
                {"parts": [{"type": "text", "text": marker}], "noReply": True},
            )
            logger.info("[opencode] posted completion marker: %s", marker)
        except (HTTPError, URLError, TimeoutError, OpenCodeProtocolError) as error:
            logger.warning("[opencode] failed to post completion marker: %s", error)

    def _request_any(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        response_body = self._request_text(method, path, body)
        logger.debug(
            "[opencode] %s %s ← %d bytes: %r",
            method,
            path,
            len(response_body),
            response_body[:2000],
        )
        if not response_body.strip():
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as error:
            raise OpenCodeProtocolError(
                f"OpenCode {method} {path} returned a non-JSON response."
            ) from error

    def _request_text(self, method: str, path: str, body: dict[str, Any] | None) -> str:
        url = _join_url(self.config.base_url or "", path)
        logger.debug("[opencode] → %s %s", method, url)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.config.password:
            request.add_header(
                "Authorization",
                _basic_auth_header(self.config.username, self.config.password),
            )
        timeout = (
            self.config.timeout_seconds
            if self.config.timeout_seconds and self.config.timeout_seconds > 0
            else None
        )
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")


def health_to_json(health: OpenCodeHealth) -> dict[str, object]:
    return asdict(health)


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _basic_auth_header(username: str, password: str) -> str:
    credentials = f"{username}:{password}".encode("utf-8")
    token = base64.b64encode(credentials).decode("ascii")
    return f"Basic {token}"


def _health_message(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "OpenCode health endpoint responded."
    if isinstance(payload, dict):
        status = payload.get("status")
        if isinstance(status, str):
            return f"OpenCode health endpoint responded with status {status}."
    return "OpenCode health endpoint responded."


def _extract_sysml_content(response: Any) -> str | None:
    messages = _extract_assistant_messages(response)
    logger.debug("[opencode] extracted %d assistant message(s)", len(messages))
    if not messages:
        _debug_response_structure(response)
        return None
    text = "\n".join(messages).strip()
    logger.debug("[opencode] joined message text (%d chars): %r", len(text), text[:500])
    fenced = re.search(r"```(?:sysml)?\s*\n(.*?)```", text, re.DOTALL)
    if fenced and "package" in fenced.group(1):
        return fenced.group(1).strip()
    block = re.search(r"(?ms)^\s*package\s.*\}\s*$", text)
    if block:
        return block.group(0).strip()
    start = text.find("package ")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1].strip()
    logger.debug(
        "[opencode] no SysML pattern matched — text starts with: %r", text[:100]
    )
    return None


def _debug_response_structure(response: Any) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return
    if isinstance(response, list):
        logger.debug("[opencode] response is list of %d events", len(response))
        for i, event in enumerate(response[:5]):
            if isinstance(event, dict):
                logger.debug(
                    "[opencode]   event[%d] keys=%s type=%r role=%r",
                    i,
                    list(event.keys()),
                    event.get("type"),
                    event.get("role"),
                )
    elif isinstance(response, dict):
        logger.debug("[opencode] response is dict keys=%s", list(response.keys()))
        items = response.get("items")
        if isinstance(items, list):
            logger.debug("[opencode]   .items has %d entries", len(items))


def _emit_tool_errors(
    tool_errors: list[dict[str, str]],
    on_oc_event: Callable[[str, str], None] | None,
) -> None:
    if not on_oc_event:
        return
    for error in tool_errors:
        tool = error.get("tool") or "tool"
        path = error.get("path") or "unknown path"
        message = error.get("message") or "unknown error"
        on_oc_event(
            "opencode_tool_error",
            f"{error.get('pass', 'analysis')} {tool} failed for {path}: {message}",
        )


def _extract_tool_errors(response: Any, pass_name: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for event in _response_events(response):
        parts = event.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            state = part.get("state", {})
            if not isinstance(state, dict) or state.get("status") != "error":
                continue
            tool_error = str(state.get("error") or "").strip()
            tool_input = state.get("input", {})
            tool_input = tool_input if isinstance(tool_input, dict) else {}
            path = (
                tool_input.get("path")
                or tool_input.get("filePath")
                or tool_input.get("cwd")
                or ""
            )
            errors.append(
                {
                    "pass": pass_name,
                    "tool": str(part.get("tool") or "tool"),
                    "path": str(path),
                    "message": tool_error,
                }
            )
    return errors


def _response_events(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [event for event in response if isinstance(event, dict)]
    if isinstance(response, dict):
        items = response.get("items")
        if isinstance(items, list):
            return [event for event in items if isinstance(event, dict)]
        return [response]
    return []


def _extract_assistant_messages(response: Any) -> list[str]:
    messages: list[str] = []
    for event in _response_events(response):
        role_or_type = event.get("type") or event.get("role") or ""
        if role_or_type != "assistant":
            continue
        text = _message_text(event)
        if text:
            messages.append(text)

    if not messages and isinstance(response, dict) and "parts" in response:
        text = _message_text(response)
        if text:
            messages.append(text)

    return messages


def _message_text(item: dict[str, Any]) -> str | None:
    parts = item.get("parts")
    if not isinstance(parts, list):
        return None
    text_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type not in ("text", None):
            continue
        text = part.get("text") or part.get("content")
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())
    return "\n".join(text_parts) if text_parts else None


def _opencode_error_message(status_code: int, body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return f"OpenCode returned HTTP {status_code}."
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return f"OpenCode rejected the pass: {message.strip()}"
    return f"OpenCode returned HTTP {status_code}."


def _usage_from_session(session: dict[str, Any]) -> dict[str, Any]:
    raw_usage = session.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    input_tokens = _first_int(
        (usage, session),
        (
            "inputTokens",
            "input_tokens",
            "promptTokens",
            "prompt_tokens",
        ),
    )
    output_tokens = _first_int(
        (usage, session),
        (
            "outputTokens",
            "output_tokens",
            "completionTokens",
            "completion_tokens",
        ),
    )
    total_tokens = _first_int(
        (usage, session), ("totalTokens", "total_tokens", "tokens")
    )
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    cost = _first_float((usage, session), ("cost", "totalCost", "total_cost"))
    result = {
        "cost": cost,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "raw": {
            key: value
            for key, value in session.items()
            if key in {"cost", "usage", "model", "providerID", "time"}
        },
    }
    return {key: value for key, value in result.items() if value is not None}


def _usage_from_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    input_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    total_tokens = 0
    cost = 0.0
    request_count = 0
    saw_cost = False

    for message in messages:
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "step-finish":
                continue
            tokens = part.get("tokens")
            if not isinstance(tokens, dict):
                continue
            request_count += 1
            input_tokens += _int_value(tokens.get("input"))
            output_tokens += _int_value(tokens.get("output"))
            reasoning_tokens += _int_value(tokens.get("reasoning"))
            cache = tokens.get("cache")
            if isinstance(cache, dict):
                cache_read_tokens += _int_value(cache.get("read"))
                cache_write_tokens += _int_value(cache.get("write"))
            total_tokens += _int_value(tokens.get("total"))
            part_cost = _float_value(part.get("cost"))
            if part_cost is not None:
                saw_cost = True
                cost += part_cost

    if request_count == 0:
        return {}
    if total_tokens == 0:
        total_tokens = (
            input_tokens
            + output_tokens
            + reasoning_tokens
            + cache_read_tokens
            + cache_write_tokens
        )
    result: dict[str, Any] = {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "reasoningTokens": reasoning_tokens,
        "cacheReadTokens": cache_read_tokens,
        "cacheWriteTokens": cache_write_tokens,
        "totalTokens": total_tokens,
        "requestCount": request_count,
        "raw": {
            "messageCount": len(messages),
            "stepFinishCount": request_count,
        },
    }
    if saw_cost:
        result["cost"] = cost
    return result


def _merge_usage(
    session_usage: dict[str, Any], message_usage: dict[str, Any]
) -> dict[str, Any]:
    if not session_usage:
        return message_usage
    if not message_usage:
        return session_usage

    result = dict(session_usage)
    for key in (
        "inputTokens",
        "outputTokens",
        "reasoningTokens",
        "cacheReadTokens",
        "cacheWriteTokens",
        "totalTokens",
        "requestCount",
    ):
        if key in message_usage:
            result[key] = message_usage[key]
    if "cost" not in result and "cost" in message_usage:
        result["cost"] = message_usage["cost"]
    if "cost" in message_usage:
        result["calculatedCost"] = message_usage["cost"]

    result["raw"] = {
        "session": session_usage.get("raw", {}),
        "messages": message_usage.get("raw", {}),
    }
    return {key: value for key, value in result.items() if value is not None}


def _first_int(
    sources: tuple[dict[str, Any], ...], keys: tuple[str, ...]
) -> int | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
    return None


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _float_value(value: Any) -> float | None:
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


def _first_float(
    sources: tuple[dict[str, Any], ...], keys: tuple[str, ...]
) -> float | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value.strip())
                except ValueError:
                    pass
    return None
