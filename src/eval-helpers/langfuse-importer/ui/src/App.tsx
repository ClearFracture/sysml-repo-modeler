import { useCallback, useEffect, useMemo, useState } from 'react';

type ImporterConfig = {
  modelerBaseUrl: string;
  langfuseHost: string;
  langfusePublicKey: string;
  langfuseProjectName: string;
  hasSecretKey: boolean;
};

type ScanRecord = {
  runId: string;
  projectSlug?: string | null;
  projectName?: string | null;
  scanStatus?: string | null;
  telemetryExportStatus?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  exportedAt?: string | null;
  bundleSchemaVersion?: string | null;
  lastRefreshedAt?: string | null;
  lastPushedAt?: string | null;
  pushStatus?: string | null;
  pushError?: string | null;
  scanVersion?: string | null;
  langfuseSessionId?: string | null;
  needsPush?: boolean;
};

type ScanSummary = {
  total: number;
  pending: number;
  pushed: number;
  failed: number;
  stale: number;
};

const emptyConfig: ImporterConfig = {
  modelerBaseUrl: 'http://localhost:8080',
  langfuseHost: 'http://localhost:3000',
  langfusePublicKey: '',
  langfuseProjectName: '',
  hasSecretKey: false,
};

async function readJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      typeof payload === 'object' && payload && 'detail' in payload
        ? String((payload as { detail?: string }).detail)
        : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return payload as T;
}

export default function App() {
  const [config, setConfig] = useState<ImporterConfig>(emptyConfig);
  const [secretKey, setSecretKey] = useState('');
  const [runs, setRuns] = useState<ScanRecord[]>([]);
  const [summary, setSummary] = useState<ScanSummary>({
    total: 0,
    pending: 0,
    pushed: 0,
    failed: 0,
    stale: 0,
  });
  const [status, setStatus] = useState('Ready');
  const [busy, setBusy] = useState(false);

  const loadConfig = useCallback(async () => {
    const payload = await readJson<ImporterConfig>('/api/config');
    setConfig(payload);
  }, []);

  const loadScans = useCallback(async () => {
    setStatus('Refreshing scans from SysML Repo Modeler');
    try {
      const payload = await readJson<{
        runs: ScanRecord[];
        summary: ScanSummary;
        updated: number;
        removed?: number;
      }>('/api/scans/refresh', { method: 'POST' });
      setRuns(payload.runs);
      setSummary(payload.summary);
      const removed = payload.removed ?? 0;
      setStatus(
        removed > 0
          ? `Synced ${payload.updated} scan(s); removed ${removed} unavailable from modeler`
          : `Synced ${payload.updated} scan(s) from modeler`,
      );
    } catch (error) {
      const payload = await readJson<{ runs: ScanRecord[]; summary: ScanSummary }>('/api/scans');
      setRuns(payload.runs);
      setSummary(payload.summary);
      setStatus(
        error instanceof Error
          ? `${error.message}; showing last tracked scans`
          : 'Modeler refresh failed; showing last tracked scans',
      );
    }
  }, []);

  useEffect(() => {
    loadConfig()
      .then(() => loadScans())
      .catch((error) => setStatus(error instanceof Error ? error.message : 'Failed to load importer'));
  }, [loadConfig, loadScans]);

  const saveConfig = async () => {
    setBusy(true);
    setStatus('Saving configuration');
    try {
      const body: Record<string, string> = {
        modelerBaseUrl: config.modelerBaseUrl,
        langfuseHost: config.langfuseHost,
        langfusePublicKey: config.langfusePublicKey,
        langfuseProjectName: config.langfuseProjectName,
      };
      if (secretKey.trim()) {
        body.langfuseSecretKey = secretKey.trim();
      }
      const payload = await readJson<ImporterConfig>('/api/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setConfig(payload);
      setSecretKey('');
      setStatus('Configuration saved');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Failed to save configuration');
    } finally {
      setBusy(false);
    }
  };

  const refreshScans = async () => {
    setBusy(true);
    try {
      await loadScans();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Refresh failed');
    } finally {
      setBusy(false);
    }
  };

  const pushScan = async (runId: string) => {
    setBusy(true);
    setStatus(`Pushing scan ${shortId(runId)} to Langfuse`);
    try {
      await readJson(`/api/scans/${runId}/push`, { method: 'POST' });
      await loadScans();
      setStatus(`Pushed scan ${shortId(runId)} to Langfuse`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Push failed');
      await loadScans();
    } finally {
      setBusy(false);
    }
  };

  const pushPending = async () => {
    setBusy(true);
    setStatus('Pushing pending scans to Langfuse');
    try {
      const payload = await readJson<{ attempted: number; succeeded: number; failed: number }>(
        '/api/scans/push-pending',
        { method: 'POST' },
      );
      await loadScans();
      setStatus(
        `Push complete: ${payload.succeeded}/${payload.attempted} succeeded, ${payload.failed} failed`,
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Bulk push failed');
      await loadScans();
    } finally {
      setBusy(false);
    }
  };

  const pendingRuns = useMemo(() => runs.filter((run) => run.needsPush), [runs]);

  const sortedRuns = useMemo(
    () =>
      [...runs].sort((left, right) => {
        const projectCompare = formatSysmlProject(left).localeCompare(formatSysmlProject(right));
        if (projectCompare !== 0) {
          return projectCompare;
        }
        const leftTime = Date.parse(left.completedAt ?? left.startedAt ?? '');
        const rightTime = Date.parse(right.completedAt ?? right.startedAt ?? '');
        return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
      }),
    [runs],
  );

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <h1>SysML Langfuse Importer</h1>
          <p>{status}</p>
        </div>
        <div className="hero-actions">
          <button disabled={busy} onClick={refreshScans} type="button">
            Refresh Scans
          </button>
          <button disabled={busy || pendingRuns.length === 0} onClick={pushPending} type="button">
            Push Pending ({pendingRuns.length})
          </button>
        </div>
      </header>

      <section className="panel">
        <h2>Langfuse Target</h2>
        <div className="form-grid">
          <label>
            <span>SysML modeler URL</span>
            <input
              value={config.modelerBaseUrl}
              onChange={(event) => setConfig({ ...config, modelerBaseUrl: event.target.value })}
              placeholder="http://localhost:8080"
            />
          </label>
          <label>
            <span>Langfuse host</span>
            <input
              value={config.langfuseHost}
              onChange={(event) => setConfig({ ...config, langfuseHost: event.target.value })}
              placeholder="http://localhost:3000"
            />
          </label>
          <label>
            <span>Langfuse project name</span>
            <input
              value={config.langfuseProjectName}
              onChange={(event) => setConfig({ ...config, langfuseProjectName: event.target.value })}
              placeholder="sysml-repo-modeler"
            />
          </label>
          <label>
            <span>Langfuse public key</span>
            <input
              value={config.langfusePublicKey}
              onChange={(event) => setConfig({ ...config, langfusePublicKey: event.target.value })}
              placeholder="pk-lf-..."
            />
          </label>
          <label>
            <span>Langfuse secret key</span>
            <input
              type="password"
              value={secretKey}
              onChange={(event) => setSecretKey(event.target.value)}
              placeholder={config.hasSecretKey ? 'Saved — enter to replace' : 'sk-lf-...'}
            />
          </label>
        </div>
        <div className="panel-actions">
          <button disabled={busy} onClick={saveConfig} type="button">
            Save Configuration
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Tracked Scans</h2>
          <div className="summary-pills">
            <span>Total {summary.total}</span>
            <span>Pending {summary.pending}</span>
            <span>Pushed {summary.pushed}</span>
            <span>Stale {summary.stale}</span>
            <span>Failed {summary.failed}</span>
          </div>
        </div>
        {runs.length === 0 ? (
          <p className="empty-state">No scans tracked yet. Click Refresh Scans after exporting telemetry from the modeler.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>SysML Project</th>
                  <th>Scan Run</th>
                  <th>Scan Status</th>
                  <th>Export</th>
                  <th>Push</th>
                  <th>Scan Version</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sortedRuns.map((run) => (
                  <tr key={run.runId}>
                    <td className="project-cell">
                      <strong>{run.projectName?.trim() || 'Unknown project'}</strong>
                      <small>{run.projectSlug?.trim() || 'No project slug'}</small>
                    </td>
                    <td>
                      <strong>{shortId(run.runId)}</strong>
                      <small>{formatDate(run.completedAt ?? run.startedAt)}</small>
                      <small className="scan-session">
                        Langfuse session: {scanSessionId(run)}
                      </small>
                      <small className="scan-run-id">Run ID: {run.runId}</small>
                    </td>
                    <td>{run.scanStatus ?? '—'}</td>
                    <td>{run.telemetryExportStatus ?? '—'}</td>
                    <td>
                      <StatusBadge status={run.pushStatus} needsPush={run.needsPush} />
                      {run.pushError ? <small>{run.pushError}</small> : null}
                      {run.langfuseSessionId ? (
                        <small>Pushed to {run.langfuseSessionId}</small>
                      ) : null}
                    </td>
                    <td>{formatScanVersion(run.scanVersion ?? run.exportedAt)}</td>
                    <td>
                      <button
                        disabled={busy || !run.needsPush}
                        onClick={() => pushScan(run.runId)}
                        type="button"
                      >
                        Push
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function StatusBadge({ status, needsPush }: { status?: string | null; needsPush?: boolean }) {
  if (needsPush) {
    return <span className="badge badge--pending">{status === 'stale' ? 'Stale' : 'Pending'}</span>;
  }
  if (status === 'success') {
    return <span className="badge badge--ok">Pushed</span>;
  }
  if (status === 'failed') {
    return <span className="badge badge--error">Failed</span>;
  }
  return <span className="badge">{status ?? '—'}</span>;
}

function shortId(value?: string | null): string {
  if (!value) return '—';
  return value.length <= 10 ? value : `${value.slice(0, 8)}…`;
}

function formatDate(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatSysmlProject(run: ScanRecord): string {
  const name = run.projectName?.trim();
  const slug = run.projectSlug?.trim();
  if (name && slug) {
    return `${name} (${slug})`;
  }
  return name || slug || 'Unknown project';
}

function scanSessionId(run: ScanRecord): string {
  if (run.langfuseSessionId?.trim()) {
    return run.langfuseSessionId.trim();
  }
  const projectName = sanitizeProjectNameForSession(
    run.projectName?.trim() || run.projectSlug?.trim() || 'unknown',
  );
  const scanVersion = run.scanVersion?.trim() || run.exportedAt?.trim() || 'unknown';
  return `sysml-project:${projectName}:${scanVersion}`;
}

function sanitizeProjectNameForSession(projectName: string): string {
  return projectName.replace(/:/g, '-');
}

function formatScanVersion(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (!Number.isNaN(date.getTime())) {
    return date.toLocaleString();
  }
  return value;
}
