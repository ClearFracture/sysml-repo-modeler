from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from .config_store import ConfigStore, ImporterConfig
from .import_state import ImportStateStore, ScanRecord
from .langfuse_import import LangfuseImportError, import_bundle
from .modeler_client import ModelerClient, ModelerClientError


class ImporterService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.config_store = ConfigStore(data_dir / "config.json")
        self.state_store = ImportStateStore(data_dir / "imports.db")
        self.cache_dir = data_dir / "bundles"

    def get_config(self) -> dict[str, object]:
        config = self.config_store.load()
        return self.config_store.to_public_json(config)

    def update_config(self, payload: dict[str, Any]) -> dict[str, object]:
        current = self.config_store.load()
        secret = payload.get("langfuseSecretKey")
        if secret is None:
            secret = payload.get("langfuse_secret_key")
        updated = ImporterConfig(
            modeler_base_url=str(
                payload.get("modelerBaseUrl")
                or payload.get("modeler_base_url")
                or current.modeler_base_url
            ).rstrip("/"),
            langfuse_host=str(
                payload.get("langfuseHost")
                or payload.get("langfuse_host")
                or current.langfuse_host
            ).rstrip("/"),
            langfuse_public_key=str(
                payload.get("langfusePublicKey")
                or payload.get("langfuse_public_key")
                or current.langfuse_public_key
            ),
            langfuse_secret_key=str(secret if secret is not None else current.langfuse_secret_key),
            langfuse_project_name=str(
                payload.get("langfuseProjectName")
                or payload.get("langfuse_project_name")
                or current.langfuse_project_name
            ),
        )
        self.config_store.save(updated)
        return self.config_store.to_public_json(updated)

    def refresh_scans(self) -> dict[str, Any]:
        config = self.config_store.load()
        client = ModelerClient(config.modeler_base_url)
        try:
            runs = client.list_telemetry_runs()
        except ModelerClientError as error:
            raise RuntimeError(f"Failed to refresh scans from modeler: {error}") from error
        updated = self.state_store.upsert_from_modeler(runs)
        scans = self.state_store.list_scans()
        return {
            "refreshedAt": scans[0].last_refreshed_at if scans else None,
            "runCount": len(scans),
            "updated": updated,
            "runs": [scan.to_json() for scan in scans],
            "summary": {
                "total": len(scans),
                "pending": sum(1 for scan in scans if scan.needs_push()),
                "pushed": sum(1 for scan in scans if scan.push_status == "success"),
                "failed": sum(1 for scan in scans if scan.push_status == "failed"),
                "stale": sum(1 for scan in scans if scan.push_status == "stale"),
            },
        }

    def list_scans(self) -> list[dict[str, Any]]:
        return [scan.to_json() for scan in self.state_store.list_scans()]

    def push_scan(self, run_id: str) -> dict[str, Any]:
        record = self.state_store.get_scan(run_id)
        if record is None:
            raise RuntimeError(f"Scan {run_id} is not tracked. Refresh scans first.")
        if record.telemetry_export_status != "completed":
            raise RuntimeError(
                f"Scan {run_id} does not have a completed telemetry export."
            )
        config = self.config_store.load()
        try:
            result = self._push_bundle(record, config)
        except (ModelerClientError, LangfuseImportError) as error:
            self.state_store.mark_push_result(
                run_id,
                success=False,
                error=str(error),
            )
            raise RuntimeError(str(error)) from error
        self.state_store.mark_push_result(
            run_id,
            success=True,
            bundle_fingerprint=record.bundle_fingerprint,
            langfuse_session_id=result.get("langfuseSessionId"),
        )
        updated = self.state_store.get_scan(run_id)
        return {
            "result": result,
            "scan": updated.to_json() if updated else None,
        }

    def push_pending(self) -> dict[str, Any]:
        scans = self.state_store.list_scans()
        pending = [
            scan
            for scan in scans
            if scan.telemetry_export_status == "completed"
            and scan.push_status in {None, "failed", "stale"}
        ]
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for scan in pending:
            try:
                results.append(self.push_scan(scan.run_id))
            except RuntimeError as error:
                errors.append({"runId": scan.run_id, "error": str(error)})
        return {
            "attempted": len(pending),
            "succeeded": len(results),
            "failed": len(errors),
            "results": results,
            "errors": errors,
            "runs": self.list_scans(),
        }

    def _push_bundle(
        self,
        record: ScanRecord,
        config: ImporterConfig,
    ) -> dict[str, Any]:
        client = ModelerClient(config.modeler_base_url)
        temp_dir = Path(tempfile.mkdtemp(prefix="sysml-bundle-"))
        try:
            bundle_dir = client.download_bundle(record.run_id, temp_dir)
            return import_bundle(
                bundle_dir,
                config=config,
                scan_version=record.scan_version,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
