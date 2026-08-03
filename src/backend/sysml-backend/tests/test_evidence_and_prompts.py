from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sysml_backend.services.evidence import build_repository_evidence  # noqa: E402
from sysml_backend.services.sysml_prompts import analysis_system_prompt  # noqa: E402
from sysml_backend.services.sysml_validation import validate_sysml_model  # noqa: E402


def test_documentation_and_ci_mentions_are_not_required_coverage(tmp_path: Path):
    repo = tmp_path / "repos" / "demo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "Dockerfile").write_text("FROM scratch\nEXPOSE 11434\n", encoding="utf-8")
    (repo / "README.md").write_text(
        "Community integrations include OpenAI.\n"
        "PostgreSQL can be added by clients.\n"
        "AWS tools can call the API.\n",
        encoding="utf-8",
    )
    (repo / ".github" / "workflows" / "test.yml").write_text(
        "env:\n  KAFKA: localhost\n",
        encoding="utf-8",
    )

    evidence = build_repository_evidence(
        {"repositories": [{"name": "demo", "path": "repos/demo"}]}, tmp_path
    )

    summary = evidence["summary"]
    assert "deployment" in summary["requiredCategories"]
    assert "external_api" not in summary["requiredCategories"]
    assert "database" not in summary["requiredCategories"]
    assert "cloud" not in summary["requiredCategories"]
    assert "messaging" not in summary["requiredCategories"]
    assert "external_api" in summary["tentativeCategories"]
    assert any(
        record["context"] == "documentation" and record["confidence"] == "low"
        for record in evidence["records"]
    )
    assert any(
        record["context"] == "non_runtime" and record["confidence"] == "low"
        for record in evidence["records"]
    )


def test_platform_repository_owns_required_multi_repo_coverage(tmp_path: Path):
    platform = tmp_path / "repos" / "platform"
    service = tmp_path / "repos" / "service"
    platform.mkdir(parents=True)
    service.mkdir(parents=True)
    (platform / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (service / "docker-compose.yml").write_text(
        "REDIS_URL: redis://cache\nPOSTGRES_HOST: postgres\n",
        encoding="utf-8",
    )

    evidence = build_repository_evidence(
        {
            "repositories": [
                {"name": "service", "path": "repos/service", "role": "service"},
                {
                    "name": "platform",
                    "path": "repos/platform",
                    "role": "platform",
                },
            ]
        },
        tmp_path,
    )

    assert evidence["summary"]["requiredCategories"] == ["deployment"]
    assert "cache" in evidence["summary"]["tentativeCategories"]
    assert "database" in evidence["summary"]["tentativeCategories"]


def test_analysis_prompt_requires_a_single_sourced_operating_scope():
    prompt = analysis_system_prompt()

    assert "Choose one concrete operating scope" in prompt
    assert "Do not combine mutually exclusive modes" in prompt
    assert "compatibility endpoint" in prompt
    assert 'attribute source: String = "repo:path"' in prompt


def test_validation_requires_scope_and_part_provenance():
    model = """
package Demo {
  part def AppDef {
    port http { direction = in; }
  }
  part MonitoredSuite {
    part app : AppDef;
  }
}
"""

    result = validate_sysml_model(
        model,
        repository_count=1,
        tool_errors=[],
        evidence={"summary": {"requiredCategories": []}},
    )

    assert result.status == "needs_attention"
    assert any("runtime scope" in warning for warning in result.warnings)
    assert any("source path" in warning for warning in result.warnings)
