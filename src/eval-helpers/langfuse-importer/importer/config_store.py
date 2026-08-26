from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ImporterConfig:
    modeler_base_url: str = "http://localhost:8080"
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_project_name: str = ""


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ImporterConfig:
        if not self.path.is_file():
            return ImporterConfig()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return ImporterConfig()
        return ImporterConfig(
            modeler_base_url=str(payload.get("modelerBaseUrl") or payload.get("modeler_base_url") or "http://localhost:8080"),
            langfuse_host=str(payload.get("langfuseHost") or payload.get("langfuse_host") or "http://localhost:3000"),
            langfuse_public_key=str(payload.get("langfusePublicKey") or payload.get("langfuse_public_key") or ""),
            langfuse_secret_key=str(payload.get("langfuseSecretKey") or payload.get("langfuse_secret_key") or ""),
            langfuse_project_name=str(payload.get("langfuseProjectName") or payload.get("langfuse_project_name") or ""),
        )

    def save(self, config: ImporterConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "modelerBaseUrl": config.modeler_base_url.rstrip("/"),
            "langfuseHost": config.langfuse_host.rstrip("/"),
            "langfusePublicKey": config.langfuse_public_key,
            "langfuseSecretKey": config.langfuse_secret_key,
            "langfuseProjectName": config.langfuse_project_name,
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def to_public_json(self, config: ImporterConfig) -> dict[str, object]:
        return {
            "modelerBaseUrl": config.modeler_base_url,
            "langfuseHost": config.langfuse_host,
            "langfusePublicKey": config.langfuse_public_key,
            "langfuseProjectName": config.langfuse_project_name,
            "hasSecretKey": bool(config.langfuse_secret_key),
        }
