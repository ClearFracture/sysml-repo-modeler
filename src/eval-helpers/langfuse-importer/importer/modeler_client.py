from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any

import requests


class ModelerClientError(Exception):
    pass


class ModelerClient:
    def __init__(self, base_url: str, timeout_seconds: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def list_telemetry_runs(self) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.base_url}/api/telemetry/runs",
            timeout=self.timeout_seconds,
        )
        _raise_for_status(response)
        payload = response.json()
        runs = payload.get("runs")
        return runs if isinstance(runs, list) else []

    def fetch_manifest(self, run_id: str) -> dict[str, Any]:
        response = requests.get(
            f"{self.base_url}/api/telemetry/runs/{run_id}",
            timeout=self.timeout_seconds,
        )
        _raise_for_status(response)
        payload = response.json()
        manifest = payload.get("manifest")
        return manifest if isinstance(manifest, dict) else {}

    def download_bundle(self, run_id: str, dest: Path) -> Path:
        response = requests.get(
            f"{self.base_url}/api/telemetry/runs/{run_id}/bundle",
            timeout=self.timeout_seconds,
        )
        _raise_for_status(response)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
            archive.extractall(dest)
        directories = [path for path in dest.iterdir() if path.is_dir()]
        if len(directories) == 1:
            return directories[0]
        nested = dest / run_id
        return nested if nested.is_dir() else dest


def _raise_for_status(response: requests.Response) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        message = response.text.strip() or str(error)
        raise ModelerClientError(message) from error
