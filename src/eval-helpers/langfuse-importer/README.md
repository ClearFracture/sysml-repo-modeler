# SysML Langfuse Importer

Standalone helper application that reads telemetry bundles exported by [SysML Repo Modeler](../../README.md) and pushes them into a self-hosted [Langfuse](https://langfuse.com/) project.

The importer keeps its own local state: which scans exist, which have been pushed, scan versions from the modeler, and links back to SysML project metadata.

## What it does

1. Connects to the modeler's telemetry API (`/api/telemetry/runs`)
2. Tracks exported scans in a local SQLite database
3. Downloads bundle archives on push
4. Creates Langfuse **sessions per scan** (`sysml-project:{projectName}:{scanVersion}`), **traces per run** (deterministic id from `runId`), and **chronologically interleaved** observations with **historical timestamps** from the bundle:
   - **Pipeline spans** from `events.jsonl` (cycle start, validation, repair, OpenCode pass markers, tool/provider errors)
   - **OpenCode prompts, assistant turns, tool calls, reasoning, and model generations** sorted by their recorded times (not grouped after all pipeline steps)
   - **Per-step pricing** from OpenCode `step-finish` parts (`usage_details` + `cost_details`)
   - **Scan-level cost summary** from aggregated OpenCode usage
5. Attaches metadata/tags such as:
   - `sysmlRunId`, `sysmlProjectSlug`, `sysmlProjectName`
   - `scanVersion` (telemetry bundle export timestamp from the modeler)
   - tags like `sysml-repo-modeler`, `project:{slug}`, `scan-version:{exportedAt}`

### Langfuse hierarchy

```
Session  sysml-project:{projectName}:{scanVersion}
└── Trace  scan/{runId prefix}   (deterministic trace id seeded from runId; runId in metadata as sysmlRunId)
    └── span  scan-{runId prefix}   ← manifest startedAt → completedAt
        ├── span  step-{phase}              ← pipeline events (events.jsonl timestamps)
        ├── generation  prompt-turn-N       ← user prompt (message.time)
        ├── span  turn-N-assistant          ← assistant turn (message.time)
        │   ├── tool  {toolName}            ← tool state.time start/end
        │   ├── span  reasoning
        │   └── generation  model-step-M    ← tokens + cost per LLM step
        ├── span  step-{phase}              ← more pipeline events interleaved by time
        └── generation  scan-cost-summary   ← total scan usage/cost
    └── scores attached to trace (renderable, opencode_cost, token counts, …)
```

Timestamps come from the telemetry bundle (`events.jsonl` event timestamps, OpenCode `message.time`, and tool `state.time`). When message times are missing, user prompts align to `opencode_pass` events in order.

## Prerequisites

- Python 3.11+
- Node.js 20+ (to build the UI once)
- A running SysML Repo Modeler instance with exported telemetry scans
- A running self-hosted Langfuse instance

## Quick start

From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd ui
npm install
npm run build
cd ..

python -m importer
```

Open **http://127.0.0.1:8790**.

Or use the helper script:

```powershell
.\run.ps1
```

### Development mode (UI hot reload)

Terminal 1 — backend:

```powershell
.\.venv\Scripts\Activate.ps1
python -m importer
```

Terminal 2 — UI:

```powershell
cd ui
npm run dev
```

Open **http://127.0.0.1:5174** (Vite proxies `/api` to the importer backend).

## Configure the importer

In the UI **Langfuse Target** panel, set:

| Field | Example | Purpose |
|---|---|---|
| SysML modeler URL | `http://localhost:8080` | Telemetry API base URL |
| Langfuse host | `http://localhost:3000` | Self-hosted Langfuse URL |
| Langfuse project name | `sysml-repo-modeler` | Stored in trace metadata/tags |
| Langfuse public key | `pk-lf-...` | Langfuse project API key |
| Langfuse secret key | `sk-lf-...` | Langfuse project API secret |

Click **Save Configuration**. Keys are stored locally in `data/config.json` (gitignored).

## Import workflow

1. In SysML Repo Modeler, run a scan with **Export telemetry bundle** enabled.
2. In the importer, click **Refresh Scans** (or reload the page — both sync from the modeler telemetry API).
3. Review the **Tracked Scans** table. Scans no longer available from the modeler (for example after a container rebuild without telemetry volumes) are removed from the local list.
4. Push individually, or click **Push Pending** for all scans that need import.

### Push states

| Status | Meaning |
|---|---|
| Pending | Export completed, never pushed |
| Pushed | Latest bundle successfully imported |
| Stale | Bundle changed since last push — push again to update Langfuse |
| Failed | Last push attempt failed (see error text) |

Each scan's **Scan Version** matches the modeler's telemetry bundle export time (`manifest.exportedAt`). Re-exporting a scan creates a new version and marks the importer row **Stale** until you push again.

## Verify in Langfuse

1. Open your Langfuse UI (e.g. `http://localhost:3000`)
2. Go to **Tracing → Sessions**
3. Find a session named `sysml-project:{projectName}:{scanVersion}` (run id is in trace metadata as `sysmlRunId`)
4. Open a trace for a scan run (`scan/{runId prefix}`)
5. Expand **scan-pipeline** for modeler processing steps and **opencode** for LLM/tool/model-call observations
6. Check **scan-cost-summary** and per-step **model-step-N** generations for token and cost details
7. Review scores on the trace (renderable, opencode_cost, token counts)

Ingestion can take 15–30 seconds to appear.

## API (optional automation)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/config` | Current config (secret not returned) |
| `PUT` | `/api/config` | Save config |
| `GET` | `/api/scans` | List tracked scans + summary |
| `POST` | `/api/scans/refresh` | Refresh from modeler |
| `POST` | `/api/scans/{run_id}/push` | Push one scan |
| `POST` | `/api/scans/push-pending` | Push all pending/stale/failed scans |

Example:

```powershell
Invoke-RestMethod http://127.0.0.1:8790/api/scans/refresh -Method POST
Invoke-RestMethod http://127.0.0.1:8790/api/scans/push-pending -Method POST
```

## Local data

| Path | Contents |
|---|---|
| `data/config.json` | Modeler/Langfuse connection settings |
| `data/imports.db` | Scan tracking and push history |
| `data/bundles/` | Reserved for future cache use |

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `IMPORTER_LISTEN_HOST` | `127.0.0.1` | Bind address |
| `IMPORTER_LISTEN_PORT` | `8790` | Bind port |
| `IMPORTER_DATA_DIR` | `./data` | State directory |

## Self-hosted Langfuse setup (summary)

If Langfuse is not running yet:

```powershell
git clone https://github.com/langfuse/langfuse.git
cd langfuse
docker compose up -d
```

Then create a project at `http://localhost:3000` and copy API keys from **Project Settings → API Keys**.

Official guide: https://langfuse.com/self-hosting/deployment/docker-compose

## Security notes

- Telemetry bundles contain prompts, tool I/O, and generated SysML from scanned repositories.
- API keys are stored in plaintext in `data/config.json` for local use only.
- Run the importer on a trusted machine with restricted access to `data/`.

## Troubleshooting

| Issue | Check |
|---|---|
| Refresh fails | Modeler reachable? Any scans with telemetry export completed? Try `GET /api/telemetry/runs` on the modeler |
| Push fails with auth error | Langfuse host and API keys; keys must belong to the configured Langfuse project |
| No traces in Langfuse | Wait 30s; check Langfuse worker logs; confirm push status is **Pushed** |
| Scan missing after refresh | Scan must have been run with **Export telemetry bundle** enabled |
| OpenCode tools/prompts missing in Langfuse | Re-run the scan to export a fresh bundle (OpenCode v2 messages use `info.role` and are normalized at export time). Then re-push from the importer |
