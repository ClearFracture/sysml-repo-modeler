from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from .app import DATA_DIR, create_app


def _configure_logging() -> None:
    level_name = os.environ.get("IMPORTER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    langfuse_logger = logging.getLogger("langfuse")
    if os.environ.get("LANGFUSE_DEBUG", "").lower() in {"1", "true", "yes"}:
        langfuse_logger.setLevel(logging.DEBUG)
    elif level <= logging.DEBUG:
        langfuse_logger.setLevel(logging.DEBUG)


def main() -> None:
    _configure_logging()
    host = os.environ.get("IMPORTER_LISTEN_HOST", "127.0.0.1")
    port = int(os.environ.get("IMPORTER_LISTEN_PORT", "8790"))
    data_dir_value = os.environ.get("IMPORTER_DATA_DIR")
    data_dir = Path(data_dir_value) if data_dir_value else DATA_DIR
    app = create_app(data_dir)
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("IMPORTER_LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
