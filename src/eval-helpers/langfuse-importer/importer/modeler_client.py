from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import requests

from .safe_tar import UnsafeTarMemberError, safe_extract_tar


class ModelerClientError(Exception):
    pass


class ModelerClient:
    def __init__(self, base_url: str, timeout_seconds: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def list_telemetry_runs(self) -> list[dict[str, Any]]:
        return self._request_json_list(
            f"{self.base_url}/api/telemetry/runs",
            key="runs",
        )

    def fetch_manifest(self, run_id: str) -> dict[str, Any]:
        payload = self._request_json(
            f"{self.base_url}/api/telemetry/runs/{run_id}",
        )
        manifest = payload.get("manifest")
        return manifest if isinstance(manifest, dict) else {}

    def download_bundle(self, run_id: str, dest: Path) -> Path:
        try:
            response = requests.get(
                f"{self.base_url}/api/telemetry/runs/{run_id}/bundle",
                timeout=self.timeout_seconds,
            )
            _raise_for_status(response)
            dest.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
                safe_extract_tar(archive, dest)
        except ModelerClientError:
            raise
        except UnsafeTarMemberError as error:
            raise ModelerClientError(str(error)) from error
        except requests.RequestException as error:
            raise ModelerClientError(
                f"Failed to download telemetry bundle for run {run_id}: {error}"
            ) from error
        except (tarfile.TarError, OSError) as error:
            raise ModelerClientError(
                f"Failed to extract telemetry bundle for run {run_id}: {error}"
            ) from error

        directories = [path for path in dest.iterdir() if path.is_dir()]
        if len(directories) == 1:
            return directories[0]
        nested = dest / run_id
        return nested if nested.is_dir() else dest

    def _request_json(self, url: str) -> dict[str, Any]:
        try:
            response = requests.get(url, timeout=self.timeout_seconds)
            _raise_for_status(response)
            payload = response.json()
        except ModelerClientError:
            raise
        except requests.RequestException as error:
            raise ModelerClientError(f"Request to {url} failed: {error}") from error
        except (json.JSONDecodeError, ValueError) as error:
            raise ModelerClientError(f"Response from {url} was not valid JSON.") from error

        if not isinstance(payload, dict):
            raise ModelerClientError(f"Response from {url} was not a JSON object.")
        return payload

    def _request_json_list(self, url: str, *, key: str) -> list[dict[str, Any]]:
        payload = self._request_json(url)
        values = payload.get(key)
        if values is None:
            return []
        if not isinstance(values, list):
            raise ModelerClientError(f"Response from {url} did not include a {key} list.")
        return [value for value in values if isinstance(value, dict)]


def _raise_for_status(response: requests.Response) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        message = response.text.strip() or str(error)
        raise ModelerClientError(message) from error
