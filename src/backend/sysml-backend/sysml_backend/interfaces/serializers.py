from __future__ import annotations

from typing import Any

from ..services import PackageRegistration


def project_to_response(project: Any) -> dict[str, Any]:
    return {
        "name": project.name,
        "slug": project.slug,
        "path": project.path,
        "repositories": project.repositories,
        "createdAt": project.created_at,
        "updatedAt": project.updated_at,
    }


def cycle_to_response(cycle: Any) -> dict[str, Any]:
    return {
        "runId": cycle.run_id,
        "packageName": cycle.package_name,
        "projectName": cycle.package_name,
        "trigger": cycle.trigger,
        "status": cycle.status,
        "startedAt": cycle.started_at,
        "completedAt": cycle.completed_at,
        "repositoryCount": cycle.repository_count,
        "changedCount": cycle.changed_count,
        "unchangedCount": cycle.unchanged_count,
        "artifacts": [
            {
                "passId": artifact.pass_id,
                "outputDir": artifact.output_dir,
                "suiteModelPath": artifact.suite_model_path,
                "suiteEvidencePath": artifact.suite_evidence_path,
                "unresolvedServicesPath": artifact.unresolved_services_path,
            }
            for artifact in cycle.artifacts
        ],
        "opencodeSessionId": getattr(cycle, "opencode_session_id", None),
        "opencodeUsage": getattr(cycle, "opencode_usage", None) or {},
    }


def package_registration_to_response(
    registration: PackageRegistration,
) -> dict[str, Any]:
    return {
        "name": registration.name,
        "path": registration.path,
        "repositories": [
            {
                "path": repository.path,
                "isGitRepository": repository.is_git_repository,
                "commit": repository.commit,
                "branch": repository.branch,
                "remoteUrl": repository.remote_url,
                "dirty": repository.dirty,
                "error": repository.error,
            }
            for repository in registration.repositories
        ],
    }
