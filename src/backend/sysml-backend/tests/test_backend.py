from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when tests are run from the service directory
# without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sysml_backend.services.architecture import (  # noqa: E402
    ArchitectureClassifier,
    ArchitectureClassifierConfig,
    apply_llm_architecture_decisions,
    finalize_architecture,
)
from sysml_backend.services.evidence import _scan_file  # noqa: E402
from sysml_backend.services.opencode_client import (  # noqa: E402
    OpenCodeClient,
    OpenCodeConfig,
    _extract_json_payload,
    _extract_provider_error,
    _extract_sysml_content,
)
from sysml_backend.services.repository_importer import (  # noqa: E402
    _name_from_url,
    _role_directory,
    _safe_name,
)
from sysml_backend.services.workspace import slugify  # noqa: E402
from sysml_backend.utils.env import _parse_env_line  # noqa: E402
from sysml_backend.utils.mapping import pick  # noqa: E402

# ---- env parsing -----------------------------------------------------------


def test_parse_env_line_strips_quotes_and_comments():
    assert _parse_env_line('FOO="bar"') == ("FOO", "bar")
    assert _parse_env_line("FOO = bar ") == ("FOO", "bar")
    assert _parse_env_line("# comment") == (None, "")
    assert _parse_env_line("noequals") == (None, "")


# ---- slug / safe name ------------------------------------------------------


def test_slug_normalizes():
    assert slugify("Example System!") == "example-system"
    assert slugify("  A__B  ") == "a__b"
    assert slugify("!!!") == ""


def test_safe_name_and_name_from_url():
    assert _safe_name("weird/name*.git") == "weird-name-.git"
    assert (
        _name_from_url("https://github.com/example/identity-service.git")
        == "identity-service"
    )
    assert _name_from_url("git@github.com:org/platform-apps.git") == "platform-apps"


def test_role_directory_mapping():
    assert _role_directory("argo") == "argo"
    assert _role_directory("docs") == "docs"
    # source/service/unknown sit directly under repos/ (no doubled subdir).
    assert _role_directory("source") == ""
    assert _role_directory("anything-else") == ""


# ---- mapping.pick ----------------------------------------------------------


def test_pick_prefers_first_present_non_none():
    assert pick({"remoteUrl": "x"}, "remote_url", "remoteUrl") == "x"
    assert pick({"remote_url": "a", "remoteUrl": "b"}, "remote_url", "remoteUrl") == "a"
    assert (
        pick({"remote_url": None, "remoteUrl": "b"}, "remote_url", "remoteUrl") == "b"
    )
    assert pick({}, "a", "b", default=0) == 0


# ---- OpenCode SysML extraction --------------------------------------------


def _assistant(text: str) -> list[dict]:
    return [{"type": "assistant", "parts": [{"type": "text", "text": text}]}]


def test_extract_sysml_fenced():
    response = _assistant("Here you go:\n```sysml\npackage P { }\n```")
    assert _extract_sysml_content(response) == "package P { }"


def test_extract_sysml_bare_with_preamble():
    response = _assistant(
        "Sure, here is the model:\n\npackage P {\n  part def A { }\n}"
    )
    extracted = _extract_sysml_content(response)
    assert extracted is not None and extracted.startswith("package P")
    assert extracted.rstrip().endswith("}")


def test_extract_sysml_none_when_no_model():
    assert _extract_sysml_content(_assistant("I could not find any overlays.")) is None


def test_extract_architecture_decisions_from_fenced_json():
    payload = _extract_json_payload(
        _assistant(
            '```json\n{"decisions":[{"id":"component_0","value":"api_service"}]}\n```'
        )
    )
    assert payload["decisions"][0]["value"] == "api_service"


# ---- architecture classification ------------------------------------------


def test_jev_is_optional_and_llm_resolves_all_candidates():
    classifier = ArchitectureClassifier(
        ArchitectureClassifierConfig(
            api_key=None,
            base_url="https://api.typesafe.ai",
            model="jev-1.13.0",
            confidence_threshold=0.8,
            timeout_seconds=30,
        )
    )
    inventory = classifier.classify(
        {
            "name": "Demo",
            "repositories": [{"name": "orders-api", "path": "repos/orders-api"}],
        },
        {
            "records": [
                {
                    "category": "database",
                    "kind": "postgresql",
                    "name": "DATABASE_URL",
                    "repository": "orders-api",
                    "path": ".env.example",
                    "line": 2,
                    "excerpt": "DATABASE_URL=<redacted>",
                }
            ]
        },
    )
    assert inventory["classifier"]["status"] == "unconfigured"
    assert inventory["summary"]["unresolvedCount"] == 2

    resolved = apply_llm_architecture_decisions(
        inventory,
        [
            {
                "id": "component_0",
                "value": "api_service",
                "confidence": 0.93,
                "reason": "The repository exposes an application API.",
            },
            {
                "id": "dependency_0",
                "value": "runtime_database",
                "confidence": 0.98,
                "reason": "DATABASE_URL is runtime database evidence.",
            },
        ],
    )
    assert resolved["summary"]["unresolvedCount"] == 0
    assert resolved["components"][0]["decision"]["source"] == "llm_fallback"
    assert resolved["dependencies"][0]["classification"] == "runtime_database"


def test_llm_unknown_is_a_completed_classification_decision():
    inventory = {
        "components": [],
        "dependencies": [
            {
                "key": "dependency:orders:observability:metrics",
                "questionId": "dependency_0",
                "classification": "unknown",
            }
        ],
        "unresolved": [
            {
                "id": "dependency_0",
                "kind": "dependency_classification",
                "subjectKey": "dependency:orders:observability:metrics",
                "allowedValues": ["platform_service", "unknown"],
            }
        ],
        "deduplication": {"decisions": [], "mergedGroups": []},
    }
    resolved = apply_llm_architecture_decisions(
        inventory,
        [
            {
                "id": "dependency_0",
                "value": "unknown",
                "confidence": 0.75,
                "reason": "The evidence does not identify a concrete service.",
            }
        ],
    )
    assert resolved["summary"]["unresolvedCount"] == 0
    assert resolved["dependencies"][0]["classification"] == "unknown"
    assert resolved["dependencies"][0]["decision"]["status"] == "accepted"


def test_same_topic_dependencies_stay_separate_without_identity_decision():
    dependencies = [
        {
            "key": "dependency:orders:database:orders_db",
            "sourceComponentKey": "component:orders",
            "topic": "database",
            "targetName": "orders-db",
            "aliases": [],
            "evidenceRefs": ["orders:compose.yml:10"],
            "evidence": [],
            "classification": "runtime_database",
            "decision": {"status": "accepted", "confidence": 0.9},
        },
        {
            "key": "dependency:orders:database:audit_db",
            "sourceComponentKey": "component:orders",
            "topic": "database",
            "targetName": "audit-db",
            "aliases": [],
            "evidenceRefs": ["orders:compose.yml:20"],
            "evidence": [],
            "classification": "runtime_database",
            "decision": {"status": "accepted", "confidence": 0.9},
        },
    ]
    inventory = {
        "components": [],
        "dependencies": dependencies,
        "unresolved": [],
        "deduplication": {"decisions": [], "mergedGroups": []},
    }
    finalized = finalize_architecture(inventory)
    assert finalized["summary"]["dependencyCount"] == 2


def test_evidence_preserves_names_for_multiple_database_dependencies(tmp_path):
    env_file = tmp_path / ".env.example"
    env_file.write_text(
        "ORDERS_DATABASE_URL=postgres://orders\n"
        "AUDIT_DATABASE_URL=postgres://audit\n"
        "BACKEND_LISTEN_HOST=0.0.0.0\n",
        encoding="utf-8",
    )
    records = _scan_file("orders-api", "application", tmp_path, env_file)
    assert {record["name"] for record in records} == {
        "ORDERS_DATABASE_URL",
        "AUDIT_DATABASE_URL",
    }


def test_service_endpoint_dependency_links_registered_repositories():
    classifier = ArchitectureClassifier(
        ArchitectureClassifierConfig(
            api_key=None,
            base_url="https://api.typesafe.ai",
            model="jev-1.13.0",
            confidence_threshold=0.8,
            timeout_seconds=30,
        )
    )
    inventory = classifier.classify(
        {
            "name": "Commerce",
            "repositories": [
                {"name": "orders-api", "path": "repos/orders-api"},
                {"name": "payments-api", "path": "repos/payments-api"},
            ],
        },
        {
            "records": [
                {
                    "category": "service",
                    "kind": "service_endpoint",
                    "name": "PAYMENTS_API_URL",
                    "repository": "orders-api",
                    "path": ".env.example",
                    "line": 3,
                    "excerpt": "PAYMENTS_API_URL=http://payments-api:8080",
                }
            ]
        },
    )
    assert inventory["dependencies"][0]["targetComponentKey"] == (
        "component:payments_api"
    )


def test_extract_provider_credit_error_without_exposing_response_headers():
    response = _provider_credit_error()

    error = _extract_provider_error(response)

    assert error == {
        "code": "credit_balance_exhausted",
        "statusCode": 429,
        "provider": "openai",
        "message": (
            "OpenAI API credits are exhausted. Add credits or configure a "
            "provider account with available quota."
        ),
    }


def test_run_analysis_reports_provider_error_during_enrichment(monkeypatch):
    client = OpenCodeClient(OpenCodeConfig(base_url="http://opencode.test"))
    architecture = _assistant("package P { part def A { } }")
    responses = iter([architecture, _provider_credit_error()])
    emitted_events: list[tuple[str, str]] = []

    monkeypatch.setattr(client, "_create_session", lambda run_id: {"id": "session-1"})
    monkeypatch.setattr(
        client,
        "_send_prompt_streaming",
        lambda *args, **kwargs: next(responses),
    )
    monkeypatch.setattr(client, "_post_completion_marker", lambda session_id: None)

    result = client.run_analysis(
        "run-1",
        {"repositories": []},
        on_oc_event=lambda phase, message: emitted_events.append((phase, message)),
    )

    assert result.sysml_content == "package P { part def A { } }"
    assert result.provider_error is not None
    assert result.provider_error["code"] == "credit_balance_exhausted"
    assert (
        "opencode_provider_error",
        result.provider_error["message"],
    ) in emitted_events


def _provider_credit_error() -> dict:
    return {
        "info": {
            "role": "assistant",
            "providerID": "openai",
            "error": {
                "name": "APIError",
                "data": {
                    "message": "You have no credits remaining.",
                    "statusCode": 429,
                    "responseHeaders": {"set-cookie": "secret"},
                    "responseBody": (
                        '{"error":{"type":"insufficient_quota",'
                        '"code":"credit_balance_exhausted"}}'
                    ),
                },
            },
        },
        "parts": [],
    }
