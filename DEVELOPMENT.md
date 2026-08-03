# Local Development

This guide covers running SysML Repo Modeler from source with Vite hot reload and a
backend running locally. Unlike the [Docker Compose](README.md#quick-start) path,
source development does **not** start Postgres or OpenCode for you — you point
the app at your own instances.

For a high-level overview of the project, see the [README](README.md).

## Prerequisites

- Python 3.11 or newer.
- Node.js 20.19 or newer with npm (Node.js 22.12 or newer also works).
- A reachable Postgres database.
- Optional: a running OpenCode server, if you want analysis runs to use OpenCode.

## Backing Services

You can supply your own Postgres and OpenCode instances, or start just those two
services with the development Compose file. It exposes their ports on the host so
a source-run backend can reach them:

```powershell
docker compose -f docker-compose.dev.yml up --build
```

This starts:

- Postgres on `localhost:5432`
- OpenCode on `localhost:4096`

OpenCode mounts `./packages` (the default project workspace) read-only, so it
sees the same files the source-run backend writes. Point the backend at these
services with:

```text
DATABASE_URL=postgres://sysml:sysml@localhost:5432/sysml #pragma: allowlist secret
OPENCODE_BASE_URL=http://localhost:4096
```

## Configuration

Copy the example environment file and fill in your values:

```powershell
copy .env.example .env
```

At minimum, set:

```text
DATABASE_URL=postgres://<user>:<password>@<host>:<port>/<database> #pragma: allowlist secret
OPENAI_API_KEY=<key, if running OpenCode locally>
```

Variables most relevant to source development:

| Variable              | Default                 | Purpose                                     |
| --------------------- | ----------------------- | ------------------------------------------- |
| `BACKEND_LISTEN_HOST` | `127.0.0.1`             | Backend bind address                        |
| `BACKEND_LISTEN_PORT` | `8765`                  | Backend listen port                         |
| `DATABASE_URL`        | blank                   | Required Postgres connection string         |
| `OPENCODE_BASE_URL`   | `http://127.0.0.1:4096` | OpenCode server URL; leave blank to disable |
| `OPENAI_API_KEY`      | blank                   | Provider key used by OpenCode               |

See the [README configuration table](README.md#configuration) for the full set.

## Setup

Install backend dependencies and run migrations:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,asgi]"
.\.venv\Scripts\alembic.exe upgrade head
```

Install frontend dependencies:

```powershell
cd src\ui
npm install
cd ..\..
```

## Running

Start the backend and frontend in separate terminals.

Backend:

```powershell
.\.venv\Scripts\Activate.ps1
python -m sysml_backend
```

Frontend:

```powershell
npm --prefix src\ui run dev
```

Then open `http://127.0.0.1:5173`.

- **Backend:** `http://127.0.0.1:8765`
- **Vite UI:** `http://127.0.0.1:5173`

The Vite dev server proxies `/api` to `http://127.0.0.1:8765`.

### Running Components Individually

**Backend:**

```powershell
.\.venv\Scripts\Activate.ps1
python -m sysml_backend
```

Health check: `http://127.0.0.1:8765/api/health`

**ASGI / API docs:**

```powershell
python -m sysml_backend.interfaces.asgi
```

Then open `http://127.0.0.1:8765/docs`.

**Frontend:**

```powershell
cd src\ui
npm run dev
```

**Production UI build:**

```powershell
cd src\ui
npm run build
```

When running from source, the backend automatically serves `src/ui/dist` if the
frontend has been built.

## Development Tooling

The `dev` extra installs `pytest`, `ruff`, `pre-commit`, and `detect-secrets`
for Python testing, linting, and secret scanning.

Install the pre-commit hooks so checks run automatically on each commit:

```powershell
python -m pip install -e ".[dev]"
pre-commit install
```

Run the backend test suite:

```powershell
pytest src\backend\sysml-backend\tests
```

Run the linter and all hooks on demand:

```powershell
ruff check .
pre-commit run --all-files
```

Run frontend checks:

```powershell
cd src\ui
npm run format:check
npm run build
```

## Troubleshooting

- **`opencode_tool_error` with `No such file or directory`** — the backend and
  OpenCode are not sharing the same project workspace mount.
- **A run finishes as `needs_attention` or `failed`** — status reflects OpenCode
  tool errors and SysML quality checks, not just process completion. Inspect run
  events at `/api/runs/<run-id>/events`.
- **Private repository sync fails** — use an HTTPS repository URL and re-enter
  the GitHub token in the UI before syncing. Tokens are never stored by the
  backend.
