"""Web / IO layer: HTTP servers, request handling, and serialization."""

from __future__ import annotations

from .serializers import (
    cycle_to_response,
    package_registration_to_response,
    project_to_response,
)
from .web_common import ALLOWED_ARTIFACTS, is_unsafe_segment
from .http_handler import SysmlBackendHandler
from .server import Services, SysmlBackendServer, build_services, create_server, main

__all__ = [
    "cycle_to_response",
    "package_registration_to_response",
    "project_to_response",
    "ALLOWED_ARTIFACTS",
    "is_unsafe_segment",
    "SysmlBackendHandler",
    "Services",
    "SysmlBackendServer",
    "build_services",
    "create_server",
    "main",
]
