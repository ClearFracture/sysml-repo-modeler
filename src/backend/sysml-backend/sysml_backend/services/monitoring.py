from __future__ import annotations

import json
import logging
import threading
import time as _time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from ..utils import pick
from .artifacts import ArtifactSet, ArtifactWriter
from .evidence import build_repository_evidence
from .opencode_client import OpenCodeClient
from .packages import PackageRegistry
from .run_events import RunEventStore
from .sysml_validation import SysmlValidationResult, validate_sysml_model
from .workspace import WorkspaceManager, slugify, to_rel

logger = logging.getLogger(__name__)
_MAX_SYSML_REPAIR_ATTEMPTS = 2
_MAX_SYSML_COVERAGE_ATTEMPTS = 1

if TYPE_CHECKING:
    from .analysis_store import AnalysisStore


class MonitorInProgressError(Exception):
    """Raised when a monitor run is requested for a package that already has one running."""

    def __init__(self, package_name: str) -> None:
        super().__init__(
            f"A monitoring run is already in progress for '{package_name}'."
        )
        self.package_name = package_name


@dataclass(frozen=True)
class MonitoringCycle:
    run_id: str
    package_name: str
    trigger: str
    status: str
    started_at: str
    completed_at: str
    repository_count: int
    changed_count: int
    unchanged_count: int
    repositories: list[dict[str, Any]]
    artifacts: list[ArtifactSet]
    opencode_session_id: str | None = None
    opencode_usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class _QueuedMonitoringCycle:
    run_id: str
    package: dict[str, Any]
    package_name: str
    slug: str
    trigger: str
    subject_kind: str


class MonitoringService:
    def __init__(
        self,
        workspaces: WorkspaceManager,
        package_registry: PackageRegistry,
        event_store: RunEventStore,
        opencode_client: OpenCodeClient,
        opencode_workspace_root: str,
        analysis_store: AnalysisStore,
    ) -> None:
        self.workspaces = workspaces
        self.package_registry = package_registry
        self.event_store = event_store
        self.opencode_client = opencode_client
        self.opencode_workspace_root = opencode_workspace_root.rstrip("/")
        self.analysis_store = analysis_store
        self._inflight: set[str] = set()
        self._queue_lock = threading.Lock()
        self._queued_slugs: set[str] = set()
        self._queue: deque[_QueuedMonitoringCycle] = deque()
        self._queue_worker_active = False
        self._status_lock = threading.Lock()
        self._run_status: dict[str, str] = {}
        self._stream_lock = threading.Lock()
        self._stream_buffers: dict[
            str, str
        ] = {}  # run_id -> buffered delta text not yet stored
        self._last_flush: dict[
            str, float
        ] = {}  # run_id -> monotonic timestamp of last flush

    # ---- Public synchronous API (kept for backward compatibility) ------------

    def start_cycle(
        self, package_name: str, trigger: str = "manual"
    ) -> MonitoringCycle:
        package = self.package_registry.get_package(package_name)
        if package is None:
            raise ValueError(f"Package '{package_name}' is not registered.")
        return self._start_cycle(
            package,
            package_name,
            slugify(package_name),
            trigger,
            subject_kind="package",
        )

    def start_project_cycle(
        self, project: dict[str, Any], trigger: str = "manual"
    ) -> MonitoringCycle:
        project_name = str(project.get("name") or project.get("slug") or "project")
        slug = str(project.get("slug") or slugify(project_name))
        package = _project_to_package(project)
        return self._start_cycle(
            package, project_name, slug, trigger, subject_kind="project"
        )

    # ---- Public async API (returns run_id immediately, cycle runs in background)

    def start_cycle_async(self, package_name: str, trigger: str = "manual") -> str:
        package = self.package_registry.get_package(package_name)
        if package is None:
            raise ValueError(f"Package '{package_name}' is not registered.")
        return self._start_cycle_async(
            package,
            package_name,
            slugify(package_name),
            trigger,
            subject_kind="package",
        )

    def start_project_cycle_async(
        self, project: dict[str, Any], trigger: str = "manual"
    ) -> str:
        project_name = str(project.get("name") or project.get("slug") or "project")
        slug = str(project.get("slug") or slugify(project_name))
        package = _project_to_package(project)
        return self._start_cycle_async(
            package, project_name, slug, trigger, subject_kind="project"
        )

    def get_run_status(self, run_id: str) -> str | None:
        with self._status_lock:
            return self._run_status.get(run_id)

    def list_inflight_run_ids(self) -> list[str]:
        with self._status_lock:
            return [
                rid for rid, s in self._run_status.items() if s in {"queued", "running"}
            ]

    # ---- Internal helpers ---------------------------------------------------

    def _start_cycle(
        self,
        package: dict[str, Any],
        package_name: str,
        slug: str,
        trigger: str,
        *,
        subject_kind: str,
    ) -> MonitoringCycle:
        if not self._claim(slug):
            raise MonitorInProgressError(package_name)
        run_id = uuid4().hex
        with self._status_lock:
            self._run_status[run_id] = "running"
        try:
            cycle = self._run_cycle(
                package,
                package_name,
                slug,
                trigger,
                subject_kind=subject_kind,
                run_id=run_id,
            )
            with self._status_lock:
                self._run_status[run_id] = cycle.status
            return cycle
        except Exception:
            with self._status_lock:
                self._run_status[run_id] = "failed"
            raise
        finally:
            self._release(slug)

    def _start_cycle_async(
        self,
        package: dict[str, Any],
        package_name: str,
        slug: str,
        trigger: str,
        *,
        subject_kind: str,
    ) -> str:
        run_id = uuid4().hex
        queued = _QueuedMonitoringCycle(
            run_id=run_id,
            package=package,
            package_name=package_name,
            slug=slug,
            trigger=trigger,
            subject_kind=subject_kind,
        )
        with self._queue_lock:
            if slug in self._inflight or slug in self._queued_slugs:
                raise MonitorInProgressError(package_name)
            self._queue.append(queued)
            self._queued_slugs.add(slug)
            with self._status_lock:
                self._run_status[run_id] = "queued"
            start_worker = not self._queue_worker_active
            if start_worker:
                self._queue_worker_active = True
        self.event_store.append(
            slug,
            run_id,
            "queue",
            "info",
            f"Queued {trigger} monitoring cycle for {subject_kind} {package_name}.",
            entity=package_name,
        )
        if start_worker:
            self._start_queue_worker()
        return run_id

    def _start_queue_worker(self) -> None:
        threading.Thread(
            target=self._run_queue_worker,
            daemon=True,
            name="monitor-queue",
        ).start()

    def _run_queue_worker(self) -> None:
        while True:
            with self._queue_lock:
                if not self._queue:
                    self._queue_worker_active = False
                    return
                queued = self._queue.popleft()
                self._queued_slugs.discard(queued.slug)
                self._inflight.add(queued.slug)
            with self._status_lock:
                self._run_status[queued.run_id] = "running"
            self.event_store.append(
                queued.slug,
                queued.run_id,
                "queue",
                "info",
                f"Started queued monitoring cycle for {queued.subject_kind} {queued.package_name}.",
                entity=queued.package_name,
            )
            try:
                cycle = self._run_cycle(
                    queued.package,
                    queued.package_name,
                    queued.slug,
                    queued.trigger,
                    subject_kind=queued.subject_kind,
                    run_id=queued.run_id,
                )
                with self._status_lock:
                    self._run_status[queued.run_id] = cycle.status
            except Exception:
                logger.exception(
                    "[monitor] queued cycle failed for run %s", queued.run_id[:8]
                )
                with self._status_lock:
                    self._run_status[queued.run_id] = "failed"
                self.event_store.append(
                    queued.slug,
                    queued.run_id,
                    "cycle_complete",
                    "error",
                    f"Monitoring cycle failed for {queued.subject_kind} {queued.package_name}.",
                    entity=queued.package_name,
                )
            finally:
                with self._queue_lock:
                    self._inflight.discard(queued.slug)

    def _run_cycle(
        self,
        package: dict[str, Any],
        package_name: str,
        slug: str,
        trigger: str,
        *,
        subject_kind: str,
        run_id: str | None = None,
    ) -> MonitoringCycle:
        workspace = self.workspaces.workspace(slug)
        artifact_writer = ArtifactWriter(workspace.root)

        if run_id is None:
            run_id = uuid4().hex
        started_at = _now()
        self.event_store.append(
            slug,
            run_id,
            "cycle_start",
            "info",
            f"Started {trigger} monitoring cycle for {subject_kind} {package_name}.",
            entity=package_name,
        )

        repositories = package.get("repositories", [])
        repositories = repositories if isinstance(repositories, list) else []
        self.event_store.append(
            slug,
            run_id,
            "package_snapshot",
            "info",
            f"Captured {len(repositories)} repository metadata record(s).",
            entity=package_name,
            reasoning_summary=f"Repository metadata was read from the registered {subject_kind} snapshot without pulling or executing code.",
        )

        changed_count = 0
        unchanged_count = len(repositories)
        self.event_store.append(
            slug,
            run_id,
            "change_detection",
            "info",
            "No prior baseline is available yet; treating this cycle as the initial baseline candidate.",
            entity=package_name,
        )

        artifact_sets: list[ArtifactSet] = []
        self.event_store.append(
            slug,
            run_id,
            "opencode",
            "info",
            "Sending SysMLv2 analysis request to OpenCode.",
            entity=package_name,
        )
        opencode_context = self._opencode_package_context(package, slug)
        evidence = build_repository_evidence(package, workspace.root)
        opencode_context["evidenceSummary"] = evidence.get("promptSummary", "")
        opencode_context["coverageRequirements"] = evidence.get("summary", {}).get(
            "coverageRequirements", []
        )
        self.event_store.append(
            slug,
            run_id,
            "evidence_inventory",
            "info",
            (
                f"Extracted {evidence.get('summary', {}).get('recordCount', 0)} "
                "repository evidence record(s) before synthesis."
            ),
            entity=package_name,
            reasoning_summary=json.dumps(evidence.get("summary", {})),
        )
        for repository in opencode_context.get("repositories", []):
            if isinstance(repository, dict) and repository.get("path"):
                self.event_store.append(
                    slug,
                    run_id,
                    "opencode_path",
                    "info",
                    f"OpenCode repository path: {repository['path']}",
                    entity=package_name,
                )
        on_oc_event = self._make_opencode_event_callback(slug, run_id, package_name)
        opencode_result = self.opencode_client.run_analysis(
            run_id, opencode_context, on_oc_event=on_oc_event
        )
        opencode_session_id = opencode_result.session_id
        sysml_content = opencode_result.sysml_content
        tool_errors = list(opencode_result.tool_errors)
        self._flush_opencode_buffers(slug, run_id, package_name)
        sysml_content, validation, tool_errors = self._validate_and_repair_sysml(
            slug=slug,
            run_id=run_id,
            package_name=package_name,
            repository_count=len(repositories),
            evidence=evidence,
            opencode_context=opencode_context,
            session_id=opencode_session_id,
            sysml_content=sysml_content,
            tool_errors=tool_errors,
            on_oc_event=on_oc_event,
        )

        if sysml_content:
            synthesis_artifact = artifact_writer.write_opencode_sysml(
                run_id,
                sysml_content,
                validation=validation.to_json(),
                evidence=evidence,
            )
            artifact_sets.append(synthesis_artifact)
            self.event_store.append(
                slug,
                run_id,
                "opencode",
                "info",
                f"OpenCode SysMLv2 synthesis complete ({len(sysml_content)} chars).",
                entity=package_name,
                evidence_refs=[
                    synthesis_artifact.suite_model_path,
                    synthesis_artifact.suite_evidence_path,
                    synthesis_artifact.unresolved_services_path,
                ],
            )
        else:
            if opencode_session_id:
                self.event_store.append(
                    slug,
                    run_id,
                    "opencode",
                    "warn",
                    "OpenCode responded but no SysML content was extracted; writing repository-metadata fallback.",
                    entity=package_name,
                )
            elif self.opencode_client.config.base_url:
                self.event_store.append(
                    slug,
                    run_id,
                    "opencode",
                    "warn",
                    "OpenCode did not return a model (it likely errored or timed out — see the preceding [opencode] log line); writing repository-metadata fallback.",
                    entity=package_name,
                )
            else:
                self.event_store.append(
                    slug,
                    run_id,
                    "opencode",
                    "info",
                    "OpenCode is not configured; writing repository-metadata fallback.",
                    entity=package_name,
                )
            fallback_artifact = artifact_writer.write_fallback_sysml(run_id, package)
            artifact_sets.append(fallback_artifact)
            self.event_store.append(
                slug,
                run_id,
                fallback_artifact.pass_id,
                "info",
                "Wrote repository-metadata fallback snapshot.",
                entity=package_name,
                evidence_refs=[
                    fallback_artifact.suite_model_path,
                    fallback_artifact.suite_evidence_path,
                    fallback_artifact.unresolved_services_path,
                ],
            )

        completed_at = _now()
        opencode_usage = self.opencode_client.get_session_usage(opencode_session_id)
        cycle = MonitoringCycle(
            run_id=run_id,
            package_name=package_name,
            trigger=trigger,
            status=validation.status,
            started_at=started_at,
            completed_at=completed_at,
            repository_count=len(repositories),
            changed_count=changed_count,
            unchanged_count=unchanged_count,
            repositories=[
                _repository_change(repository) for repository in repositories
            ],
            artifacts=artifact_sets,
            opencode_session_id=opencode_session_id,
            opencode_usage=opencode_usage,
        )
        self._append_cycle(slug, cycle)
        self.event_store.append(
            slug,
            run_id,
            "cycle_complete",
            "info" if validation.status == "completed" else "warn",
            f"Finished monitoring cycle for {subject_kind} {package_name} with status {validation.status}.",
            entity=package_name,
        )
        return cycle

    def _make_opencode_event_callback(
        self, slug: str, run_id: str, entity: str
    ) -> Callable[[str, str], None]:
        """Return a callback that handles OpenCode SSE events by phase.

        Tool events are stored immediately. Text and reasoning deltas are batched
        (flushed every ~1 second or every 200 chars) to avoid flooding the event store.
        """

        def _on_oc_event(phase: str, message: str) -> None:
            if phase in {"opencode_pass", "opencode_tool_error"}:
                self.event_store.append(
                    slug,
                    run_id,
                    phase,
                    "warn" if phase == "opencode_tool_error" else "info",
                    message,
                    entity=entity,
                )
                return
            to_emit = None
            with self._stream_lock:
                key = f"{run_id}\x00{phase}"
                self._stream_buffers[key] = self._stream_buffers.get(key, "") + message
                now = _time.monotonic()
                last = self._last_flush.get(key, 0.0)
                buf = self._stream_buffers[key]
                if now - last >= 1.0 or len(buf) >= 200:
                    to_emit = buf
                    self._stream_buffers.pop(key, None)
                    self._last_flush[key] = now
            if to_emit:
                self.event_store.append(
                    slug, run_id, phase, "info", to_emit, entity=entity
                )

        return _on_oc_event

    def _validate_and_repair_sysml(
        self,
        *,
        slug: str,
        run_id: str,
        package_name: str,
        repository_count: int,
        evidence: dict[str, Any],
        opencode_context: dict[str, Any],
        session_id: str | None,
        sysml_content: str | None,
        tool_errors: list[dict[str, str]],
        on_oc_event: Callable[[str, str], None],
    ) -> tuple[str | None, SysmlValidationResult, list[dict[str, str]]]:
        validation = validate_sysml_model(
            sysml_content,
            repository_count=repository_count,
            tool_errors=tool_errors,
            evidence=evidence,
        )
        self._append_validation_event(slug, run_id, package_name, validation)

        for attempt in range(1, _MAX_SYSML_REPAIR_ATTEMPTS + 1):
            if not _should_repair_sysml_structure(
                session_id, sysml_content, validation
            ):
                break
            self.event_store.append(
                slug,
                run_id,
                "validation_repair",
                "warn",
                f"Attempting SysML repair {attempt} of {_MAX_SYSML_REPAIR_ATTEMPTS}.",
                entity=package_name,
                reasoning_summary=json.dumps(validation.to_json()),
            )
            repair_result = self.opencode_client.repair_sysml(
                session_id or "",
                opencode_context,
                sysml_content or "",
                validation.warnings,
                attempt=attempt,
                on_oc_event=on_oc_event,
            )
            tool_errors.extend(repair_result.tool_errors)
            if not repair_result.sysml_content:
                self.event_store.append(
                    slug,
                    run_id,
                    "validation_repair",
                    "warn",
                    f"SysML repair {attempt} did not return a corrected document.",
                    entity=package_name,
                )
                break
            sysml_content = repair_result.sysml_content
            self._flush_opencode_buffers(slug, run_id, package_name)
            validation = validate_sysml_model(
                sysml_content,
                repository_count=repository_count,
                tool_errors=tool_errors,
                evidence=evidence,
            )
            self._append_validation_event(slug, run_id, package_name, validation)

        for attempt in range(1, _MAX_SYSML_COVERAGE_ATTEMPTS + 1):
            if not _should_improve_sysml_coverage(
                session_id, sysml_content, validation
            ):
                break
            self.event_store.append(
                slug,
                run_id,
                "coverage_repair",
                "warn",
                (
                    f"Attempting SysML coverage enhancement {attempt} of "
                    f"{_MAX_SYSML_COVERAGE_ATTEMPTS}."
                ),
                entity=package_name,
                reasoning_summary=json.dumps(validation.to_json()),
            )
            coverage_result = self.opencode_client.improve_sysml_coverage(
                session_id or "",
                opencode_context,
                sysml_content or "",
                validation.warnings,
                attempt=attempt,
                on_oc_event=on_oc_event,
            )
            tool_errors.extend(coverage_result.tool_errors)
            if not coverage_result.sysml_content:
                self.event_store.append(
                    slug,
                    run_id,
                    "coverage_repair",
                    "warn",
                    f"SysML coverage enhancement {attempt} did not return an improved document.",
                    entity=package_name,
                )
                break
            previous_sysml_content = sysml_content
            previous_validation = validation
            sysml_content = coverage_result.sysml_content
            self._flush_opencode_buffers(slug, run_id, package_name)
            validation = validate_sysml_model(
                sysml_content,
                repository_count=repository_count,
                tool_errors=tool_errors,
                evidence=evidence,
            )
            if validation.status == "failed":
                sysml_content = previous_sysml_content
                validation = previous_validation
                self.event_store.append(
                    slug,
                    run_id,
                    "coverage_repair",
                    "warn",
                    (
                        f"SysML coverage enhancement {attempt} returned an invalid "
                        "document; keeping the prior renderable model."
                    ),
                    entity=package_name,
                    reasoning_summary=json.dumps(validation.to_json()),
                )
                break
            self._append_validation_event(slug, run_id, package_name, validation)

        return sysml_content, validation, tool_errors

    def _append_validation_event(
        self,
        slug: str,
        run_id: str,
        package_name: str,
        validation: SysmlValidationResult,
    ) -> None:
        self.event_store.append(
            slug,
            run_id,
            "validation",
            "info" if validation.status == "completed" else "warn",
            validation.summary,
            entity=package_name,
            reasoning_summary=json.dumps(validation.to_json()),
        )

    def _flush_opencode_buffers(self, slug: str, run_id: str, entity: str) -> None:
        """Flush any remaining buffered text/reasoning for this run."""
        to_emit: dict[str, str] = {}
        with self._stream_lock:
            prefix = f"{run_id}\x00"
            stale_keys = [k for k in self._stream_buffers if k.startswith(prefix)]
            for key in stale_keys:
                phase = key[len(prefix) :]
                to_emit[phase] = self._stream_buffers.pop(key)
            for key in stale_keys:
                self._last_flush.pop(key, None)
        for phase, text in to_emit.items():
            if text:
                self.event_store.append(
                    slug, run_id, phase, "info", text, entity=entity
                )

    def list_cycles(self) -> list[dict[str, Any]]:
        return self.analysis_store.list_runs()

    def list_project_cycles(self, project_slug: str) -> list[dict[str, Any]]:
        return self.analysis_store.list_runs(project_slug)

    def changes_for_run(self, run_id: str) -> dict[str, Any] | None:
        return self.analysis_store.changes_for_run(run_id)

    def _append_cycle(self, slug: str, cycle: MonitoringCycle) -> None:
        self.analysis_store.save_cycle(slug, cycle)

    def _opencode_package_context(
        self, package: dict[str, Any], slug: str
    ) -> dict[str, Any]:
        workspace = self.workspaces.workspace(slug)
        repositories = package.get("repositories", [])
        repositories = repositories if isinstance(repositories, list) else []
        remapped: list[dict[str, Any]] = []
        for repository in repositories:
            if not isinstance(repository, dict):
                continue
            updated = dict(repository)
            path = repository.get("path")
            if isinstance(path, str) and path:
                rel = to_rel(workspace, path)
                updated["path"] = (
                    rel
                    if rel.startswith("/")
                    else f"{self.opencode_workspace_root}/{slug}/{rel}"
                )
            remapped.append(updated)
        return {**package, "repositories": remapped}

    def _claim(self, slug: str) -> bool:
        with self._queue_lock:
            if self._inflight or self._queued_slugs:
                return False
            self._inflight.add(slug)
            return True

    def _release(self, slug: str) -> None:
        with self._queue_lock:
            self._inflight.discard(slug)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repository_change(repository: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": repository.get("path"),
        "commit": repository.get("commit"),
        "branch": repository.get("branch"),
        "remoteUrl": pick(repository, "remote_url", "remoteUrl"),
        "dirty": bool(repository.get("dirty")),
        "role": repository.get("role"),
        "changeStatus": "unchanged",
    }


def _project_to_package(project: dict[str, Any]) -> dict[str, Any]:
    repositories = project.get("repositories", [])
    repositories = repositories if isinstance(repositories, list) else []
    return {
        "name": project.get("name"),
        "path": project.get("path"),
        "repositories": [
            _repository_from_project_inventory(repository)
            for repository in repositories
        ],
    }


def _repository_from_project_inventory(repository: dict[str, Any]) -> dict[str, Any]:
    git = repository.get("git", {})
    git = git if isinstance(git, dict) else {}
    is_git_repository = pick(git, "is_git_repository", "isGitRepository")
    return {
        "name": repository.get("name"),
        "path": repository.get("path") or git.get("path"),
        "role": repository.get("role"),
        "importStatus": repository.get("status"),
        "is_git_repository": is_git_repository,
        "isGitRepository": is_git_repository,
        "commit": git.get("commit"),
        "branch": git.get("branch"),
        "remote_url": pick(git, "remote_url", "remoteUrl") or repository.get("url"),
        "dirty": bool(git.get("dirty")),
        "error": repository.get("error") or git.get("error"),
    }


def _should_repair_sysml_structure(
    session_id: str | None,
    sysml_content: str | None,
    validation: SysmlValidationResult,
) -> bool:
    return bool(
        session_id
        and sysml_content
        and validation.summary == "Generated SysML is not renderable."
    )


def _should_improve_sysml_coverage(
    session_id: str | None,
    sysml_content: str | None,
    validation: SysmlValidationResult,
) -> bool:
    return bool(
        session_id
        and sysml_content
        and validation.status == "needs_attention"
        and validation.summary == "Generated SysML needs review."
    )
