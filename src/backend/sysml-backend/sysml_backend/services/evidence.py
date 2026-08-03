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

_NON_RUNTIME_PATH_PARTS = {
    ".devcontainer",
    ".github",
    "bench",
    "benchmark",
    "benchmarks",
    "docs",
    "example",
    "examples",
    "fixture",
    "fixtures",
    "test",
    "testdata",
    "testing",
    "tests",
}
_DOCUMENTATION_FILENAMES = {
    "changelog.md",
    "contributing.md",
    "history.md",
    "readme.md",
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
        repo_role = str(repository.get("role") or "")
        repo_path = _repository_path(repository, workspace_root)
        repo_records: list[dict[str, Any]] = []
        if repo_path and repo_path.exists() and repo_path.is_dir():
            for path in _candidate_files(repo_path):
                repo_records.extend(
                    _scan_file(repo_name, repo_role, repo_path, path)
                )
                if len(repo_records) >= _MAX_RECORDS_PER_REPOSITORY:
                    repo_records = repo_records[:_MAX_RECORDS_PER_REPOSITORY]
                    break
        by_repository.append(
            {
                "name": repo_name,
                "role": repo_role or None,
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
    platform_records = [
        record for record in records if record.get("repositoryRole") == "platform"
    ]
    coverage_records = platform_records or records
    required_categories = sorted(
        {
            record["category"]
            for record in coverage_records
            if record.get("confidence") == "high"
        }
    )
    tentative_categories = sorted(set(categories) - set(required_categories))
    return {
        "summary": {
            "repositoryCount": len(repositories),
            "accessibleRepositoryCount": sum(
                1 for repo in by_repository if repo["accessible"]
            ),
            "recordCount": len(records),
            "categories": categories,
            "requiredCategories": required_categories,
            "tentativeCategories": tentative_categories,
            "coverageRequirements": coverage_requirements(required_categories),
        },
        "repositories": by_repository,
        "records": records,
        "promptSummary": evidence_prompt_summary(records, workspace_root),
    }


def coverage_requirements(categories: list[str]) -> list[str]:
    return [
        (
            f"Verify and model in-scope {_CATEGORY_LABELS.get(category, category)} "
            "evidence from canonical runtime or deployment files."
        )
        for category in categories
        if category != "port"
    ]


def evidence_prompt_summary(records: list[dict[str, Any]], workspace_root: Path) -> str:
    if not records:
        return "No deterministic repository evidence was extracted before synthesis."

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (str(record.get("confidence") or "low"), record["category"])
        grouped.setdefault(key, []).append(record)

    lines = [
        "Deterministic repository evidence found before synthesis.",
        "Treat high-confidence records as topology candidates after checking their files.",
        "Treat medium/low-confidence records as search leads only; they do not prove a deployed component.",
    ]
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    for confidence, category in sorted(
        grouped, key=lambda key: (confidence_rank.get(key[0], 9), key[1])
    ):
        category_records = _balanced_prompt_records(
            grouped[(confidence, category)], limit=12
        )
        label = _CATEGORY_LABELS.get(category, category)
        lines.append(f"- {label} ({confidence} confidence):")
        for record in category_records:
            path = _display_path(record, workspace_root)
            lines.append(
                f"  - {record['repository']}:{path}:{record['line']} "
                f"{record['kind']} ({record['name']}); context={record['context']}"
            )
        omitted = len(grouped[(confidence, category)]) - len(category_records)
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
            _context_rank(_evidence_context(path.relative_to(repo_path))),
            0 if _is_architecture_file(path.relative_to(repo_path)) else 1,
            _architecture_file_priority(path.relative_to(repo_path)),
            len(path.parts),
            str(path).lower(),
        ),
    )


def _scan_file(
    repository: str,
    repository_role: str,
    repo_path: Path,
    path: Path,
) -> list[dict[str, Any]]:
    if path.stat().st_size > _MAX_FILE_BYTES:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []

    relative_path = path.relative_to(repo_path)
    context = _evidence_context(relative_path)
    confidence = _evidence_confidence(relative_path, context)
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
                    "repositoryRole": repository_role or None,
                    "path": str(relative_path),
                    "line": line_number,
                    "excerpt": sanitized[:180],
                    "context": context,
                    "confidence": confidence,
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
    filename = path.name.lower()
    stem = filename.rsplit(".", 1)[0]
    directory_parts = {part.lower().strip("._-") for part in path.parts[:-1]}
    if filename == "dockerfile" or filename.endswith(".dockerfile"):
        return True
    if filename in {"chart.yaml", "compose.yml", "compose.yaml"}:
        return True
    if stem.startswith(("docker-compose", "compose.")):
        return True
    if filename.startswith(("httpproxy.", "ingress.", "values.")):
        return True
    return bool(
        directory_parts
        & {"argo", "deploy", "deployment", "deployments", "helm", "k8s", "kubernetes"}
    )


def _evidence_context(path: Path) -> str:
    filename = path.name.lower()
    if any(_is_non_runtime_name(part) for part in (*path.parts[:-1], filename)):
        return "non_runtime"
    if filename in _DOCUMENTATION_FILENAMES or path.suffix.lower() == ".md":
        return "documentation"
    if _is_architecture_file(path):
        return "deployment"
    if path.suffix.lower() in {
        ".env",
        ".ini",
        ".json",
        ".properties",
        ".toml",
        ".yaml",
        ".yml",
    }:
        return "configuration"
    return "source"


def _evidence_confidence(path: Path, context: str) -> str:
    _ = path
    if context == "deployment":
        return "high"
    if context in {"configuration", "source"}:
        return "medium"
    return "low"


def _context_rank(context: str) -> int:
    return {
        "deployment": 0,
        "configuration": 1,
        "source": 2,
        "documentation": 3,
        "non_runtime": 4,
    }.get(context, 5)


def _architecture_file_priority(path: Path) -> int:
    filename = path.name.lower()
    if filename in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        return 0
    if filename == "dockerfile":
        return 1
    return 2


def _balanced_prompt_records(
    records: list[dict[str, Any]], *, limit: int
) -> list[dict[str, Any]]:
    by_repository: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_repository.setdefault(str(record["repository"]), []).append(record)

    selected: list[dict[str, Any]] = []
    index = 0
    while len(selected) < limit:
        added = False
        for repository_records in by_repository.values():
            if index < len(repository_records):
                selected.append(repository_records[index])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        index += 1
    return selected


def _is_non_runtime_name(value: str) -> bool:
    lowered = value.lower()
    normalized = lowered.strip("._-")
    normalized_non_runtime = {
        part.strip("._-") for part in _NON_RUNTIME_PATH_PARTS
    }
    if lowered in _NON_RUNTIME_PATH_PARTS or normalized in normalized_non_runtime:
        return True
    if normalized in {"dev", "devcontainer", "development"}:
        return True
    tokens = {token for token in re.split(r"[^a-z0-9]+", lowered) if token}
    if tokens & {
        "bench",
        "benchmark",
        "benchmarks",
        "dev",
        "development",
        "example",
        "examples",
        "fixture",
        "fixtures",
        "spec",
        "test",
        "tests",
        "testing",
    }:
        return True
    return any(
        normalized.startswith(prefix)
        for prefix in ("bench", "example", "fixture", "test")
    )
