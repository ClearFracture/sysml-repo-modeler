from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from contextvars import ContextVar
from typing import Any

from langfuse._utils import parse_error as langfuse_parse_error

_active_tracker: ContextVar[LangfuseDeliveryTracker | None] = ContextVar(
    "langfuse_delivery_tracker",
    default=None,
)

_ORIGINAL_HANDLE_EXCEPTION = langfuse_parse_error.handle_exception
_ORIGINAL_HANDLE_FERN_EXCEPTION = langfuse_parse_error.handle_fern_exception


class LangfuseDeliveryError(Exception):
    pass


class LangfuseDeliveryTracker(AbstractContextManager["LangfuseDeliveryTracker"]):
    """Collect Langfuse SDK export failures that are logged but not raised."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self._log_handlers: list[tuple[logging.Logger, logging.Handler]] = []
        self._token: Any = None

    def __enter__(self) -> LangfuseDeliveryTracker:
        self._token = _active_tracker.set(self)
        langfuse_parse_error.handle_exception = self._track_handle_exception
        langfuse_parse_error.handle_fern_exception = self._track_handle_fern_exception
        self._install_log_handler(logging.getLogger("langfuse"))
        self._install_log_handler(
            logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        langfuse_parse_error.handle_exception = _ORIGINAL_HANDLE_EXCEPTION
        langfuse_parse_error.handle_fern_exception = _ORIGINAL_HANDLE_FERN_EXCEPTION
        for logger, handler in self._log_handlers:
            logger.removeHandler(handler)
        self._log_handlers.clear()
        if self._token is not None:
            _active_tracker.reset(self._token)
            self._token = None

    def record(self, message: str) -> None:
        cleaned = message.strip()
        if cleaned and cleaned not in self.errors:
            self.errors.append(cleaned)

    def raise_if_failed(self) -> None:
        if not self.errors:
            return
        raise LangfuseDeliveryError(
            "Langfuse export failed: " + " | ".join(self.errors)
        )

    def _track_handle_exception(self, exception: Exception) -> None:
        self.record(langfuse_parse_error.generate_error_message(exception))
        _ORIGINAL_HANDLE_EXCEPTION(exception)

    def _track_handle_fern_exception(self, exception: Exception) -> None:
        self.record(langfuse_parse_error.generate_error_message_fern(exception))
        _ORIGINAL_HANDLE_FERN_EXCEPTION(exception)

    def _install_log_handler(self, logger: logging.Logger) -> None:
        handler = _LangfuseExportLogHandler(self)
        handler.setLevel(logging.ERROR)
        logger.addHandler(handler)
        self._log_handlers.append((logger, handler))


class _LangfuseExportLogHandler(logging.Handler):
    def __init__(self, tracker: LangfuseDeliveryTracker) -> None:
        super().__init__()
        self._tracker = tracker

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if _is_export_failure_message(message):
            self._tracker.record(message)


def _is_export_failure_message(message: str) -> bool:
    lowered = message.lower()
    markers = (
        "api error occurred",
        "api errors occurred",
        "bad request",
        "failed to export span batch",
        "failed to process score",
        "error creating score",
        "unauthorized",
        "forbidden",
    )
    return any(marker in lowered for marker in markers)
