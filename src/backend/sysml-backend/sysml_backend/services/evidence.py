from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_MAX_FILE_BYTES = 256_000
_MAX_RECORDS_PER_REPOSITORY = 80
_TEXT_SUFFIXES = {
    ".conf",
    ".dockerfile",
    ".env",
    ".ini",
    ".json",
    ".md",
    ".properties",
    ".py",
    ".sh",
    ".tf",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}

_SIGNALS: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "deployment",
        "kubernetes",
        re.compile(
            r"\b(kind|apiVersion):\s*(Deployment|StatefulSet|DaemonSet|Service|Job|CronJob)\b",
            re.I,
        ),
    ),
    ("deployment", "docker", re.compile(r"\b(FROM|EXPOSE|ENTRYPOINT|CMD)\b")),
    (
        "ingress",
        "ingress",
        re.compile(
            r"\b(kind:\s*Ingress|HTTPProxy|ingressClassName|virtualhost|fqdn|host:\s*[-A-Za-z0-9.*]+)",
            re.I,
        ),
    ),
    (
        "port",
        "port",
        re.compile(
            r"\b(containerPort|targetPort|port|EXPOSE)\s*:?\s*([0-9]{2,5})\b", re.I
        ),
    ),
    (
        "database",
        "postgresql",
        re.compile(
            r"\b(POSTGRES|POSTGRESQL|PGHOST|PGPORT|DATABASE_URL|JDBC_DATABASE_URL)\b",
            re.I,
        ),
    ),
    (
        "database",
        "mysql",
        re.compile(r"\b(MYSQL|MARIADB|MYSQL_DATABASE|MYSQL_HOST)\b", re.I),
    ),
    (
        "messaging",
        "rabbitmq",
        re.compile(r"\b(RABBITMQ|AMQP|AMQPS|CELERY_BROKER|MESSAGE_BROKER)\b", re.I),
    ),
    ("messaging", "kafka", re.compile(r"\b(KAFKA|BOOTSTRAP_SERVERS)\b", re.I)),
    ("cache", "redis", re.compile(r"\b(REDIS|REDIS_URL|CACHE_URL)\b", re.I)),
    ("cache", "memcached", re.compile(r"\b(MEMCACHED|MEMCACHE)\b", re.I)),
    (
        "search",
        "opensearch",
        re.compile(r"\b(OPENSEARCH|ELASTICSEARCH|ELASTIC_URL|SEARCH_URL)\b", re.I),
    ),
    ("vector", "milvus", re.compile(r"\b(MILVUS|VECTOR_DB|VECTOR_STORE)\b", re.I)),
    (
        "identity",
        "identity",
        re.compile(r"\b(KEYCLOAK|OIDC|OAUTH|JWT|AUTH0|AZURE_AD|COGNITO)\b", re.I),
    ),
    (
        "secret",
        "secret_ref",
        re.compile(
            r"\b(secretKeyRef|Secret|AWS_SECRETS|KEYVAULT|VAULT|SECRET_NAME)\b", re.I
        ),
    ),
    (
        "storage",
        "persistent_storage",
        re.compile(
            r"\b(PersistentVolumeClaim|claimName|storageClassName|mountPath|S3_BUCKET|AWS_S3|MINIO|EFS|EBS|NFS)\b",
            re.I,
        ),
    ),
    (
        "observability",
        "observability",
        re.compile(
            r"\b(CLOUDWATCH|PROMETHEUS|GRAFANA|OTEL|OPENTELEMETRY|METRICS|LOG_LEVEL)\b",
            re.I,
        ),
    ),
    (
        "cloud",
        "aws_platform",
        re.compile(
            r"\b(AWS|EKS|VPC|Route53|ROUTE53|ALB|ACM|IAM|ECR|NAT|SECURITY_GROUP|CloudWatch)\b"
        ),
    ),
    (
        "external_api",
        "llm_provider",
        re.compile(
            r"\b(OPENAI|OPENROUTER|ANTHROPIC|LANGCHAIN|LANGSMITH|HUGGINGFACE|HF_TOKEN)\b",
            re.I,
        ),
    ),
    (
        "external_api",
        "collaboration",
        re.compile(
            r"\b(SHAREPOINT|MICROSOFT_TEAMS|TEAMS_WEBHOOK|SLACK_WEBHOOK)\b", re.I
        ),
    ),
]

_CATEGORY_LABELS = {
    "cache": "cache",
    "cloud": "cloud platform/networking",
    "database": "database",
    "deployment": "deployment/runtime",
    "external_api": "third-party/external API",
    "identity": "identity/auth",
    "ingress": "ingress",
    "messaging": "messaging",
    "observability": "observability",
    "port": "network port",
    "search": "search service",
    "secret": "secrets",  # pragma: allowlist secret
    "storage": "storage",
    "vector": "vector store",
}


def build_repository_evidence(
    package: dict[str, Any], workspace_root: Path
) -> dict[str, Any]:
    workspace_root = workspace_root.resolve()
    repositories = package.get("repositories", [])
    repositories = repositories if isinstance(repositories, list) else []
    records: list[dict[str, Any]] = []
    by_repository: list[dict[str, Any]] = []

    for repository in repositories:
        if not isinstance(repository, dict):
            continue
        repo_name = _repository_name(repository)
        repo_path = _repository_path(repository, workspace_root)
        repo_records: list[dict[str, Any]] = []
        if repo_path and repo_path.exists() and repo_path.is_dir():
            for path in _candidate_files(repo_path):
                repo_records.extend(_scan_file(repo_name, repo_path, path))
                if len(repo_records) >= _MAX_RECORDS_PER_REPOSITORY:
                    repo_records = repo_records[:_MAX_RECORDS_PER_REPOSITORY]
                    break
        by_repository.append(
            {
                "name": repo_name,
                "path": str(repo_path or repository.get("path") or ""),
                "accessible": bool(
                    repo_path and repo_path.exists() and repo_path.is_dir()
                ),
                "categories": sorted({record["category"] for record in repo_records}),
                "recordCount": len(repo_records),
            }
        )
        records.extend(repo_records)

    categories = sorted({record["category"] for record in records})
    return {
        "summary": {
            "repositoryCount": len(repositories),
            "accessibleRepositoryCount": sum(
                1 for repo in by_repository if repo["accessible"]
            ),
            "recordCount": len(records),
            "categories": categories,
            "coverageRequirements": coverage_requirements(categories),
        },
        "repositories": by_repository,
        "records": records,
        "promptSummary": evidence_prompt_summary(records, workspace_root),
    }


def coverage_requirements(categories: list[str]) -> list[str]:
    return [
        f"Model {_CATEGORY_LABELS.get(category, category)} evidence when present."
        for category in categories
        if category != "port"
    ]


def evidence_prompt_summary(records: list[dict[str, Any]], workspace_root: Path) -> str:
    if not records:
        return "No deterministic repository evidence was extracted before synthesis."

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["category"], []).append(record)

    lines = ["Deterministic repository evidence found before synthesis:"]
    for category in sorted(grouped):
        category_records = grouped[category][:12]
        label = _CATEGORY_LABELS.get(category, category)
        lines.append(f"- {label}:")
        for record in category_records:
            path = _display_path(record, workspace_root)
            lines.append(
                f"  - {record['repository']}:{path}:{record['line']} "
                f"{record['kind']} ({record['name']})"
            )
        omitted = len(grouped[category]) - len(category_records)
        if omitted > 0:
            lines.append(
                f"  - {omitted} additional {label} evidence record(s) omitted from prompt."
            )
    return "\n".join(lines)


def _candidate_files(repo_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in repo_path.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if (
            path.name in {"Dockerfile", "docker-compose.yml", "docker-compose.yaml"}
            or path.suffix.lower() in _TEXT_SUFFIXES
        ):
            files.append(path)
    return sorted(
        files,
        key=lambda path: (
            0 if _is_architecture_file(path) else 1,
            len(path.parts),
            str(path).lower(),
        ),
    )


def _scan_file(repository: str, repo_path: Path, path: Path) -> list[dict[str, Any]]:
    if path.stat().st_size > _MAX_FILE_BYTES:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        sanitized = _sanitize_line(line)
        if not sanitized:
            continue
        for category, kind, regex in _SIGNALS:
            match = regex.search(sanitized)
            if not match:
                continue
            records.append(
                {
                    "category": category,
                    "kind": kind,
                    "name": _signal_name(match),
                    "repository": repository,
                    "path": str(path.relative_to(repo_path)),
                    "line": line_number,
                    "excerpt": sanitized[:180],
                }
            )
            break
    return _dedupe_records(records)


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        key = (
            record["category"],
            record["kind"],
            record["path"],
            str(record["name"]).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _repository_name(repository: dict[str, Any]) -> str:
    raw = (
        repository.get("name") or repository.get("remote_url") or repository.get("path")
    )
    if raw:
        return Path(str(raw).rstrip("/")).stem or str(raw)
    return "repository"


def _repository_path(repository: dict[str, Any], workspace_root: Path) -> Path | None:
    raw_path = repository.get("path")
    if not raw_path:
        return None
    path = Path(str(raw_path))
    return path if path.is_absolute() else workspace_root / path


def _signal_name(match: re.Match[str]) -> str:
    for group in reversed(match.groups()):
        if group and not group.isdigit():
            return group.strip().strip('"').strip("'")
    return match.group(0).strip()


def _sanitize_line(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "//")):
        return ""
    stripped = re.sub(
        r"(?i)(password|passwd|token|secret|api[_-]?key|access[_-]?key)(\s*[:=]\s*)(\S+)",
        r"\1\2<redacted>",
        stripped,
    )
    return stripped


def _display_path(record: dict[str, Any], workspace_root: Path) -> str:
    _ = workspace_root
    return str(record["path"]).replace("\\", "/")


def _is_architecture_file(path: Path) -> bool:
    lowered = str(path).lower()
    return any(
        term in lowered
        for term in (
            "argo",
            "chart",
            "compose",
            "deploy",
            "deployment",
            "dockerfile",
            "helm",
            "httpproxy",
            "ingress",
            "k8s",
            "kubernetes",
            "service",
            "values",
        )
    )
