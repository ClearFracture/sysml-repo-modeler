from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from .app import DATA_DIR, create_app


def main() -> None:
    host = os.environ.get("IMPORTER_LISTEN_HOST", "127.0.0.1")
    port = int(os.environ.get("IMPORTER_LISTEN_PORT", "8790"))
    data_dir_value = os.environ.get("IMPORTER_DATA_DIR")
    data_dir = Path(data_dir_value) if data_dir_value else DATA_DIR
    app = create_app(data_dir)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
