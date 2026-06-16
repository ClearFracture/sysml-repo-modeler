from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import pick


@dataclass(frozen=True)
class ArtifactSet:
    pass_id: str
    output_dir: str
    suite_model_path: str
    suite_evidence_path: str
    unresolved_services_path: str


class ArtifactWriter:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)

    def write_opencode_sysml(
        self,
        run_id: str,
        sysml_content: str,
        *,
        validation: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> ArtifactSet:
        pass_id = "opencode-synthesis"
        output_dir = self.output_root / "runs" / run_id / pass_id
        output_dir.mkdir(parents=True, exist_ok=True)

        suite_model_path = output_dir / "suite-model.sysml"
        suite_evidence_path = output_dir / "suite-evidence.json"
        unresolved_services_path = output_dir / "unresolved-services.md"

        suite_model_path.write_text(sysml_content, encoding="utf-8")
        suite_evidence_path.write_text(
            json.dumps(
                {
                    "passId": pass_id,
                    "source": "opencode",
                    "validation": validation or {},
                    "summary": (evidence or {}).get("summary", {}),
                    "repositories": (evidence or {}).get("repositories", []),
                    "records": (evidence or {}).get("records", []),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        unresolved_services_path.write_text(
            _unresolved_summary("OpenCode Synthesis", validation),
            encoding="utf-8",
        )
        self._write_latest(
            suite_model_path, suite_evidence_path, unresolved_services_path, run_id
        )

        return ArtifactSet(
            pass_id=pass_id,
            output_dir=str(output_dir),
            suite_model_path=str(suite_model_path),
            suite_evidence_path=str(suite_evidence_path),
            unresolved_services_path=str(unresolved_services_path),
        )

    def write_fallback_sysml(self, run_id: str, package: dict[str, Any]) -> ArtifactSet:
        """Write a bare repo-metadata model used when OpenCode produces no SysML.

        Emits one `part def` per repository with documentation and metadata attributes only —
        no invented ports or connections — so the renderer always has something to show.
        """
        pass_id = "repo-metadata"
        output_dir = self.output_root / "runs" / run_id / pass_id
        output_dir.mkdir(parents=True, exist_ok=True)

        normalized = [
            _normalize_repository(repository, index)
            for index, repository in enumerate(_repositories(package), 1)
        ]
        sysml = _build_fallback_sysml(package.get("name", "Package"), normalized)

        suite_model_path = output_dir / "suite-model.sysml"
        suite_evidence_path = output_dir / "suite-evidence.json"
        unresolved_services_path = output_dir / "unresolved-services.md"

        suite_model_path.write_text(sysml, encoding="utf-8")
        suite_evidence_path.write_text(
            json.dumps(
                {"passId": pass_id, "source": "fallback", "records": []}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        unresolved_services_path.write_text(
            "# Repository Metadata Fallback\n\n"
            "OpenCode did not return a SysML model for this cycle. "
            "This snapshot lists the repositories only.\n",
            encoding="utf-8",
        )
        self._write_latest(
            suite_model_path, suite_evidence_path, unresolved_services_path, run_id
        )

        return ArtifactSet(
            pass_id=pass_id,
            output_dir=str(output_dir),
            suite_model_path=str(suite_model_path),
            suite_evidence_path=str(suite_evidence_path),
            unresolved_services_path=str(unresolved_services_path),
        )

    def _write_latest(
        self,
        suite_model_path: Path,
        suite_evidence_path: Path,
        unresolved_services_path: Path,
        run_id: str,
    ) -> None:
        latest_dir = self.output_root / "runs" / run_id / "latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(suite_model_path, latest_dir / "suite-model.sysml")
        shutil.copyfile(suite_evidence_path, latest_dir / "suite-evidence.json")
        shutil.copyfile(unresolved_services_path, latest_dir / "unresolved-services.md")


def _repositories(package: dict[str, Any]) -> list[dict[str, Any]]:
    repositories = package.get("repositories", [])
    return repositories if isinstance(repositories, list) else []


def _unresolved_summary(title: str, validation: dict[str, Any] | None) -> str:
    lines = [f"# {title}", "", "Generated by OpenCode."]
    if validation:
        lines.extend(["", f"Status: {validation.get('status', 'unknown')}"])
        summary = validation.get("summary")
        if summary:
            lines.append(str(summary))
        warnings = validation.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def _build_fallback_sysml(
    package_name: str, normalized_repositories: list[dict[str, Any]]
) -> str:
    safe_package_name = _identifier(f"{package_name}Architecture")

    service_defs: list[str] = []
    instances: list[str] = []
    for repository in normalized_repositories:
        service_defs.extend(
            [
                f"part def {repository['definition_name']} {{",
                "    doc /*",
                f"     * Repository {repository['name']}; model not yet synthesized by OpenCode.",
                "     */",
                f"    attribute repositoryName: String = {_sysml_string(repository['name'])};",
                f"    attribute repositoryBranch: String = {_sysml_string(repository['branch'])};",
                f"    attribute repositoryCommit: String = {_sysml_string(repository['commit'])};",
                "}",
                "",
            ]
        )
        instances.append(
            f"    part {repository['instance_name']}: {repository['definition_name']};"
        )

    if not instances:
        instances.append("    part unresolvedPackage: unresolved_packageServiceDef;")
        service_defs.extend(
            [
                "part def unresolved_packageServiceDef {",
                "    doc /* No repositories were registered for this package. */",
                "}",
                "",
            ]
        )

    return "\n".join(
        [
            f"package {safe_package_name} {{",
            "",
            *service_defs,
            "part MonitoredSuite {",
            "    doc /* Repository inventory; OpenCode synthesis unavailable for this cycle. */",
            "",
            *instances,
            "}",
            "",
            "view 'Monitored Suite': PartDefinitionView {",
            "    expose MonitoredSuite::*;",
            "}",
            "}",
            "",
        ]
    )


def _normalize_repository(repository: dict[str, Any], index: int) -> dict[str, Any]:
    raw_name = (
        repository.get("name")
        or Path(str(repository.get("path", f"repository-{index}"))).name
    )
    name = str(raw_name or f"repository-{index}")
    return {
        "name": name,
        "definition_name": _identifier(f"{name}ServiceDef"),
        "instance_name": _identifier(name),
        "path": str(repository.get("path") or ""),
        "role": str(repository.get("role") or "unknown"),
        "remote_url": str(pick(repository, "remote_url", "remoteUrl") or ""),
        "commit": str(repository.get("commit") or ""),
        "branch": str(repository.get("branch") or ""),
        "import_status": str(repository.get("importStatus") or ""),
        "dirty": bool(repository.get("dirty")),
    }


def _identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "Generated"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned[0].lower() + cleaned[1:]


def _sysml_string(value: str) -> str:
    return json.dumps(str(value))
