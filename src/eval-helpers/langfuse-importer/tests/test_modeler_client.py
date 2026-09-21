from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importer.modeler_client import ModelerClient, ModelerClientError
from importer.safe_tar import UnsafeTarMemberError, safe_extract_tar


def _make_tar(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_safe_extract_tar_rejects_parent_directory_paths(tmp_path: Path):
    dest = tmp_path / "bundle"
    archive_bytes = _make_tar({"../escape.txt": b"bad"})
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        with pytest.raises(UnsafeTarMemberError):
            safe_extract_tar(archive, dest)
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_tar_rejects_absolute_paths(tmp_path: Path):
    dest = tmp_path / "bundle"
    archive_bytes = _make_tar({"/etc/passwd": b"bad"})
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        with pytest.raises(UnsafeTarMemberError):
            safe_extract_tar(archive, dest)


def test_safe_extract_tar_writes_expected_files(tmp_path: Path):
    dest = tmp_path / "bundle"
    archive_bytes = _make_tar({"manifest.json": b"{}"})
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        safe_extract_tar(archive, dest)
    assert (dest / "manifest.json").read_text(encoding="utf-8") == "{}"


@patch("importer.modeler_client.requests.get")
def test_modeler_client_wraps_connection_errors(mock_get):
    mock_get.side_effect = requests.ConnectionError("offline")
    client = ModelerClient("http://localhost:8080")

    with pytest.raises(ModelerClientError, match="Request to .* failed"):
        client.list_telemetry_runs()


@patch("importer.modeler_client.requests.get")
def test_modeler_client_wraps_invalid_json(mock_get):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("invalid")
    mock_get.return_value = response
    client = ModelerClient("http://localhost:8080")

    with pytest.raises(ModelerClientError, match="not valid JSON"):
        client.fetch_manifest("run-1")


@patch("importer.modeler_client.requests.get")
def test_download_bundle_wraps_unsafe_archive(mock_get, tmp_path: Path):
    archive_bytes = _make_tar({"../escape.txt": b"bad"})
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.content = archive_bytes
    mock_get.return_value = response
    client = ModelerClient("http://localhost:8080")

    with pytest.raises(ModelerClientError, match="escapes the extraction directory"):
        client.download_bundle("run-1", tmp_path / "dest")
