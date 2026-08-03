from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_MAX_RENDER_DIAGNOSTICS = 25


@dataclass(frozen=True)
class SysmlValidationResult:
    status: str
    summary: str
    metrics: dict[str, int]
    warnings: list[str]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def validate_sysml_model(
    sysml_content: str | None,
    *,
    repository_count: int,
    tool_errors: list[dict[str, str]],
    evidence: dict[str, Any] | None = None,
) -> SysmlValidationResult:
    inaccessible_paths = [
        error
        for error in tool_errors
        if _is_file_access_error(error.get("message", ""))
    ]
    if inaccessible_paths:
        first = inaccessible_paths[0]
        path = first.get("path") or "unknown path"
        return SysmlValidationResult(
            status="failed",
            summary=f"OpenCode could not access repository path: {path}",
            metrics=_metrics(sysml_content or ""),
            warnings=[error.get("message", "") for error in inaccessible_paths],
        )

    if not sysml_content or not sysml_content.strip():
        return SysmlValidationResult(
            status="failed",
            summary="OpenCode did not return SysML content.",
            metrics=_metrics(""),
            warnings=[],
        )

    metrics = _metrics(sysml_content)
    warnings: list[str] = []
    status = "completed"

    if metrics["packages"] == 0:
        return SysmlValidationResult(
            status="failed",
            summary="Generated content does not contain a SysML package.",
            metrics=metrics,
            warnings=["No package declaration was found."],
        )

    if metrics["brace_balance"] != 0:
        return SysmlValidationResult(
            status="failed",
            summary="Generated SysML has unbalanced braces.",
            metrics=metrics,
            warnings=[f"Brace balance is {metrics['brace_balance']}."],
        )

    render_diagnostics = renderable_sysml_diagnostics(sysml_content)
    if render_diagnostics:
        return SysmlValidationResult(
            status="failed",
            summary="Generated SysML is not renderable.",
            metrics=metrics,
            warnings=render_diagnostics,
        )

    if repository_count > 0 and metrics["part_defs"] < repository_count:
        status = "needs_attention"
        warnings.append(
            f"Expected at least {repository_count} part definition(s), found {metrics['part_defs']}."
        )

    if repository_count > 0 and metrics["ports"] == 0 and metrics["connects"] == 0:
        status = "needs_attention"
        warnings.append("Generated model has no ports or connections.")

    coverage_warnings = _coverage_warnings(sysml_content, evidence)
    if coverage_warnings:
        status = "needs_attention"
        warnings.extend(coverage_warnings)

    provenance_warnings = _provenance_warnings(metrics)
    if provenance_warnings:
        status = "needs_attention"
        warnings.extend(provenance_warnings)

    direction_warnings = _direction_warnings(sysml_content, metrics)
    if direction_warnings:
        status = "needs_attention"
        warnings.extend(direction_warnings)

    if metrics["chars"] < 1000 and repository_count > 0:
        status = "needs_attention"
        warnings.append("Generated model is unusually small for a repository analysis.")

    if warnings:
        return SysmlValidationResult(
            status=status,
            summary="Generated SysML needs review.",
            metrics=metrics,
            warnings=warnings,
        )

    return SysmlValidationResult(
        status="completed",
        summary="Generated SysML passed basic quality checks.",
        metrics=metrics,
        warnings=[],
    )


def _metrics(sysml_content: str) -> dict[str, int]:
    return {
        "chars": len(sysml_content),
        "packages": len(re.findall(r"\bpackage\b", sysml_content)),
        "part_defs": len(re.findall(r"\bpart\s+def\b", sysml_content)),
        "ports": len(re.findall(r"\bport\s+(?!def\b)", sysml_content)),
        "ports_with_direction": len(
            re.findall(r"\bdirection\s*=\s*(?:in|out|both)\s*;", sysml_content)
        ),
        "bidirectional_ports": len(
            re.findall(r"\bdirection\s*=\s*both\s*;", sysml_content)
        ),
        "connects": len(re.findall(r"\bconnect\b", sysml_content)),
        "docs": len(re.findall(r"\bdoc\s*/\*", sysml_content)),
        "scope_attributes": len(
            re.findall(r"\battribute\s+scope\s*:\s*String\s*=", sysml_content)
        ),
        "source_attributes": len(
            re.findall(r"\battribute\s+source\s*:\s*String\s*=", sysml_content)
        ),
        "brace_balance": sysml_content.count("{") - sysml_content.count("}"),
    }


def _coverage_warnings(
    sysml_content: str,
    evidence: dict[str, Any] | None,
) -> list[str]:
    if not evidence:
        return []
    summary = evidence.get("summary", {})
    categories = summary.get("requiredCategories", summary.get("categories", []))
    if not isinstance(categories, list):
        return []

    normalized_model = sysml_content.lower()
    warnings: list[str] = []
    for category in sorted({str(category) for category in categories}):
        terms = _coverage_terms(category)
        if not terms:
            continue
        if not any(term in normalized_model for term in terms):
            warnings.append(
                f"Evidence includes {category} signals, but the SysML model does not include matching architecture terms."
            )
    return warnings


def _provenance_warnings(metrics: dict[str, int]) -> list[str]:
    warnings: list[str] = []
    if metrics["scope_attributes"] == 0:
        warnings.append(
            "MonitoredSuite does not declare the concrete deployment or runtime scope."
        )
    if metrics["part_defs"] > metrics["source_attributes"]:
        warnings.append(
            "Only "
            f"{metrics['source_attributes']} of {metrics['part_defs']} part definitions "
            "cite a repository-relative source path."
        )
    return warnings


def _coverage_terms(category: str) -> list[str]:
    return {
        "cache": ["redis", "memcached", "cache"],
        "cloud": [
            "aws",
            "eks",
            "vpc",
            "alb",
            "route53",
            "acm",
            "iam",
            "ecr",
            "nat",
            "cloudwatch",
        ],
        "database": ["postgres", "postgresql", "mysql", "mariadb", "sql", "database"],
        "deployment": [
            "kubernetes",
            "deployment",
            "argo",
            "helm",
            "compose",
            "namespace",
            "image",
        ],
        "external_api": [
            "openai",
            "openrouter",
            "anthropic",
            "langsmith",
            "huggingface",
            "sharepoint",
            "teams",
            "external",
        ],
        "identity": ["keycloak", "oidc", "oauth", "jwt", "identity", "auth", "cognito"],
        "ingress": [
            "ingress",
            "host",
            "fqdn",
            "route53",
            "alb",
            "http",
            "https",
            "users",
        ],
        "messaging": ["rabbit", "amqp", "kafka", "broker", "queue", "message"],
        "observability": [
            "cloudwatch",
            "prometheus",
            "grafana",
            "otel",
            "metrics",
            "logs",
            "observability",
        ],
        "search": ["opensearch", "elasticsearch", "search"],
        "secret": ["secret", "secrets", "keyvault", "vault", "iam"],
        "storage": ["storage", "volume", "pvc", "s3", "efs", "ebs", "nfs", "bucket"],
        "vector": ["milvus", "vector"],
    }.get(category, [])


def _direction_warnings(sysml_content: str, metrics: dict[str, int]) -> list[str]:
    if metrics["ports"] == 0:
        return []
    warnings: list[str] = []
    if metrics["ports_with_direction"] == 0:
        warnings.append("Generated model declares ports but no port directions.")
        return warnings

    directed_ratio = metrics["ports_with_direction"] / metrics["ports"]
    if directed_ratio < 0.75:
        warnings.append(
            f"Only {metrics['ports_with_direction']} of {metrics['ports']} ports declare direction."
        )

    if metrics["connects"] >= 5 and metrics["ports_with_direction"] > 0:
        both_ratio = metrics["bidirectional_ports"] / metrics["ports_with_direction"]
        if both_ratio >= 0.75:
            warnings.append(
                "Most directed ports are marked `both`; use `in` or `out` unless traffic is truly bidirectional."
            )
    return warnings


def _is_file_access_error(message: str) -> bool:
    lowered = message.lower()
    return (
        "no such file or directory" in lowered
        or "file not found" in lowered
        or "cannot access" in lowered
    )


def renderable_sysml_diagnostics(sysml_content: str) -> list[str]:
    """Return diagnostics for renderer-blocking SysML structure issues.

    This mirrors the UI renderer's current contract: a package should contain
    part definitions, one renderable system block, explicit instances in that
    system, and connections whose endpoints resolve to declared instances and
    ports.
    """
    definitions = _parse_part_definitions(sysml_content)
    systems = _parse_systems(sysml_content)
    if not systems:
        return [
            "No system part block with explicit instances and connections was found."
        ]

    system = max(systems, key=lambda candidate: len(candidate["connections"]))
    instances = {instance["name"]: instance for instance in system["instances"]}
    diagnostics: list[str] = []

    if not instances:
        diagnostics.append(
            f"{system['name']} has connections but no declared part instances."
        )

    for instance in system["instances"]:
        if instance["type"] not in definitions:
            diagnostics.append(
                f"{instance['name']} references missing part definition {instance['type']}."
            )

    for connection in system["connections"]:
        _validate_endpoint(
            connection["id"],
            "source",
            connection["source"],
            instances,
            definitions,
            diagnostics,
        )
        _validate_endpoint(
            connection["id"],
            "target",
            connection["target"],
            instances,
            definitions,
            diagnostics,
        )

    if len(diagnostics) > _MAX_RENDER_DIAGNOSTICS:
        return [
            *diagnostics[:_MAX_RENDER_DIAGNOSTICS],
            f"{len(diagnostics) - _MAX_RENDER_DIAGNOSTICS} additional render diagnostics omitted.",
        ]
    return diagnostics


def _validate_endpoint(
    connection_id: str,
    role: str,
    endpoint: dict[str, Any],
    instances: dict[str, dict[str, str]],
    definitions: dict[str, dict[str, Any]],
    diagnostics: list[str],
) -> None:
    instance_name = endpoint["instance"]
    instance = instances.get(instance_name)
    if instance is None:
        diagnostics.append(
            f"{connection_id} references missing {role} part {instance_name}."
        )
        return

    port = endpoint.get("port")
    if not port:
        return

    definition = definitions.get(instance["type"])
    if definition is None:
        return

    if not _endpoint_port_exists(definition, endpoint):
        address = ".".join(
            [endpoint["instance"], *endpoint.get("path", []), endpoint["port"]]
        )
        diagnostics.append(f"{address} is not declared on {instance['type']}.")


def _endpoint_port_exists(
    definition: dict[str, Any],
    endpoint: dict[str, Any],
) -> bool:
    ports = definition["ports"]
    subparts = definition["parts"]
    for segment in endpoint.get("path", []):
        next_part = next(
            (part for part in subparts if part["name"] == segment),
            None,
        )
        if next_part is None:
            return False
        ports = next_part["ports"]
        subparts = next_part["parts"]
    return endpoint["port"] in ports


def _parse_part_definitions(sysml_content: str) -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    regex = re.compile(
        rf"\bpart\s+def\s+({_IDENTIFIER})(?:\s*:>\s*{_IDENTIFIER})?\s*\{{"
    )
    position = 0
    while match := regex.search(sysml_content, position):
        block = _read_block(sysml_content, match.start())
        if block is None:
            position = match.end()
            continue
        body, end = block
        definitions[match.group(1)] = {
            "ports": _parse_ports(_strip_subpart_blocks(body)),
            "parts": _parse_subparts(body),
        }
        position = end
    return definitions


def _parse_subparts(body: str) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    regex = re.compile(rf"\bpart\s+(?!def\b)({_IDENTIFIER})\s*\{{")
    position = 0
    while match := regex.search(body, position):
        block = _read_block(body, match.start())
        if block is None:
            position = match.end()
            continue
        part_body, end = block
        parts.append(
            {
                "name": match.group(1),
                "ports": _parse_ports(_strip_subpart_blocks(part_body)),
                "parts": _parse_subparts(part_body),
            }
        )
        position = end
    return parts


def _parse_ports(body: str) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(
            rf"\bport\s+({_IDENTIFIER})(?:\s*:\s*{_IDENTIFIER})?\s*(?:\{{|;)",
            body,
        )
    }


def _parse_systems(sysml_content: str) -> list[dict[str, Any]]:
    systems: list[dict[str, Any]] = []
    regex = re.compile(rf"\bpart\s+(?!def\b)({_IDENTIFIER})\s*\{{")
    position = 0
    while match := regex.search(sysml_content, position):
        block = _read_block(sysml_content, match.start())
        if block is None:
            position = match.end()
            continue
        body, end = block
        instances = _parse_instances(body)
        connections = _parse_connections(body)
        if instances or connections:
            systems.append(
                {
                    "name": match.group(1),
                    "instances": instances,
                    "connections": connections,
                }
            )
        position = end
    return systems


def _parse_instances(body: str) -> list[dict[str, str]]:
    return [
        {"name": match.group(1), "type": match.group(2)}
        for match in re.finditer(
            rf"\bpart\s+({_IDENTIFIER})\s*:\s*({_IDENTIFIER})\s*;",
            body,
        )
    ]


def _parse_connections(body: str) -> list[dict[str, Any]]:
    connections: list[dict[str, Any]] = []
    path = rf"{_IDENTIFIER}(?:\.{_IDENTIFIER})*"
    regex = re.compile(
        rf"\bconnect\s+({path})\s+to\s+({path})(?:\s*:\s*{_IDENTIFIER})?\s*;"
    )
    for match in regex.finditer(body):
        source = _parse_endpoint(match.group(1))
        target = _parse_endpoint(match.group(2))
        connections.append(
            {
                "id": f"{_endpoint_key(source)}->{_endpoint_key(target)}-{len(connections)}",
                "source": source,
                "target": target,
            }
        )
    return connections


def _parse_endpoint(path_expression: str) -> dict[str, Any]:
    segments = path_expression.split(".")
    if len(segments) == 1:
        return {"instance": segments[0], "path": [], "port": None}
    return {
        "instance": segments[0],
        "path": segments[1:-1],
        "port": segments[-1],
    }


def _endpoint_key(endpoint: dict[str, Any]) -> str:
    return ".".join(
        [
            value
            for value in [
                endpoint["instance"],
                *endpoint.get("path", []),
                endpoint.get("port"),
            ]
            if value
        ]
    )


def _strip_subpart_blocks(body: str) -> str:
    result = list(body)
    regex = re.compile(rf"\bpart\s+(?!def\b){_IDENTIFIER}\s*\{{")
    position = 0
    while match := regex.search(body, position):
        block = _read_block(body, match.start())
        if block is None:
            position = match.end()
            continue
        _, end = block
        for index in range(match.start(), end):
            result[index] = " "
        position = end
    return "".join(result)


def _read_block(text: str, start: int) -> tuple[str, int] | None:
    open_brace = text.find("{", start)
    if open_brace == -1:
        return None

    depth = 0
    for index in range(open_brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : index], index + 1
    return None
