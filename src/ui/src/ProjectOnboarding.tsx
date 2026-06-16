import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  DownloadCloud,
  Pencil,
  GitBranch,
  Loader2,
  MessageSquare,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import {
  formatRunOutcome,
  validationReviewFromEvent,
  validationReviewFromEvents,
  type ValidationReview,
} from './runValidation';

export type Project = {
  name: string;
  slug: string;
  workspace?: WorkspaceStatus;
  repositories?: RepositoryInventory[];
};

export type WorkspaceStatus = {
  available?: boolean;
  path?: string | null;
};

export type RepositoryInventory = {
  name?: string;
  url?: string;
  path?: string;
  workspace?: WorkspaceStatus;
  status?: string;
  error?: string | null;
  git?: {
    commit?: string | null;
    branch?: string | null;
    remote_url?: string | null;
    dirty?: boolean;
    error?: string | null;
  };
};

type RepositoryDraft = {
  id: number;
  url: string;
  branch: string;
};

type RunArtifact = {
  passId?: string;
  pass_id?: string;
};

export type ProjectRun = {
  runId?: string;
  run_id?: string;
  projectName?: string;
  packageName?: string;
  status?: string;
  trigger?: string;
  startedAt?: string;
  started_at?: string;
  completedAt?: string;
  completed_at?: string;
  repositoryCount?: number;
  changedCount?: number;
  changed_count?: number;
  unchangedCount?: number;
  unchanged_count?: number;
  artifacts?: RunArtifact[];
  opencodeSessionId?: string;
  opencode_session_id?: string;
  opencodeCost?: number | null;
  opencode_cost?: number | null;
  inputTokens?: number | null;
  input_tokens?: number | null;
  outputTokens?: number | null;
  output_tokens?: number | null;
  totalTokens?: number | null;
  total_tokens?: number | null;
  opencodeUsage?: Record<string, unknown>;
  opencode_usage?: Record<string, unknown>;
};

type OpenCodeSession = {
  id: string;
  title?: string;
  time?: { created?: number; updated?: number };
  cost?: number;
};

type OpenCodeMessagePart = {
  type?: string;
  text?: string;
};

type OpenCodeMessage = {
  type?: string;
  parts?: OpenCodeMessagePart[];
  input?: Record<string, unknown>;
  tool?: string;
  time?: { created?: number };
};

type RunEvent = {
  timestamp?: string;
  phase?: string;
  level?: string;
  message?: string;
  reasoningSummary?: string | null;
  reasoning_summary?: string | null;
};

type SyncStreamEvent = {
  type?: string;
  phase?: string;
  repository?: string;
  stage?: string;
  percent?: number;
  message?: string;
  repositoryCount?: number;
  repositories?: RepositoryInventory[];
  failureCount?: number;
  error?: string;
};

type OpenCodeHealth = {
  configured?: boolean;
  status?: string;
  base_url?: string | null;
  baseUrl?: string | null;
  message?: string;
};

type ServiceStatus = OpenCodeHealth & {
  service?: string;
  version?: string;
};

type RuntimeStatus = {
  status?: string;
  repositoryMode?: string;
  storage?: string;
  projectWorkspace?: ServiceStatus & { path?: string | null };
  opencode?: OpenCodeHealth;
};

type ProjectOnboardingProps = {
  backendBaseUrl: string;
  onLoadRunSnapshot: (run: ProjectRun) => Promise<void>;
  projects: Project[];
  selectedProjectSlug: string;
  onSelectedProjectSlugChange: (slug: string) => void;
  onProjectsChange: (projects: Project[]) => void;
  projectRuns: ProjectRun[];
  selectedRunId: string;
  onSelectedRunIdChange: Dispatch<SetStateAction<string>>;
  onProjectRunsChange: (runs: ProjectRun[]) => void;
};

export default function ProjectOnboarding({
  backendBaseUrl,
  onLoadRunSnapshot,
  projects,
  selectedProjectSlug,
  onSelectedProjectSlugChange,
  onProjectsChange,
  projectRuns,
  selectedRunId,
  onSelectedRunIdChange,
  onProjectRunsChange,
}: ProjectOnboardingProps) {
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [draftRepos, setDraftRepos] = useState<RepositoryDraft[]>([]);
  const [inventory, setInventory] = useState<RepositoryInventory[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | undefined>();
  const [opencodeHealth, setOpenCodeHealth] = useState<OpenCodeHealth | undefined>();
  const [opencodeSessions, setOpencodeSessions] = useState<OpenCodeSession[]>([]);
  const [selectedSession, setSelectedSession] = useState<{ id: string; messages: OpenCodeMessage[] } | undefined>();
  const [sessionsView, setSessionsView] = useState(false);
  const [status, setStatus] = useState('Ready');
  const [githubToken, setGithubToken] = useState('');
  const [rememberGithubToken, setRememberGithubToken] = useState(false);
  const [editingProjectName, setEditingProjectName] = useState(false);
  const [projectNameDraft, setProjectNameDraft] = useState('');
  const [blockingNotice, setBlockingNotice] = useState<{ title: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [phaseLabel, setPhaseLabel] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const phaseStartRef = useRef<number | null>(null);
  const runStartRef = useRef<Date | null>(null);
  const selectedProjectSlugRef = useRef(selectedProjectSlug);

  const selectedProject = useMemo(
    () => projects.find((project) => project.slug === selectedProjectSlug),
    [projects, selectedProjectSlug],
  );
  const importedCount = inventory.filter((repo) => repo.status === 'imported' || repo.status === 'refreshed').length;
  const warningCount = inventory.filter((repo) => repo.error || repo.git?.error).length;
  const dirtyCount = inventory.filter((repo) => repo.git?.dirty).length;
  const workspaceUnavailable =
    selectedProject?.workspace?.available === false || inventory.some((repo) => repo.workspace?.available === false);
  const selectedRun = useMemo(
    () => projectRuns.find((run) => runIdOf(run) === selectedRunId),
    [projectRuns, selectedRunId],
  );
  const selectedRunReview = useMemo(() => validationReviewFromEvents(events), [events]);

  const refreshProjects = useCallback(async () => {
    const response = await fetch(`${backendBaseUrl}/api/projects`);
    if (!response.ok) {
      throw new Error(`Backend returned ${response.status} for /api/projects`);
    }
    const payload = (await response.json()) as { projects?: Project[] };
    const nextProjects = payload.projects ?? [];
    onProjectsChange(nextProjects);
    if (!selectedProjectSlugRef.current && nextProjects[0]) {
      onSelectedProjectSlugChange(nextProjects[0].slug);
      setInventory(nextProjects[0].repositories ?? []);
    }
  }, [backendBaseUrl, onProjectsChange, onSelectedProjectSlugChange]);

  const refreshProjectRuns = useCallback(
    async (slug: string) => {
      if (!slug) {
        onProjectRunsChange([]);
        onSelectedRunIdChange('');
        return;
      }
      const response = await fetch(`${backendBaseUrl}/api/projects/${slug}/runs`);
      if (!response.ok) {
        throw new Error(`Backend returned ${response.status} for /api/projects/${slug}/runs`);
      }
      const payload = (await response.json()) as { runs?: ProjectRun[] };
      const runs = payload.runs ?? [];
      onProjectRunsChange(runs);
      onSelectedRunIdChange((current) =>
        current && runs.some((run) => runIdOf(run) === current) ? current : runIdOf(runs[0]) || '',
      );
    },
    [backendBaseUrl, onProjectRunsChange, onSelectedRunIdChange],
  );

  const refreshRuntimeStatus = useCallback(async () => {
    const response = await fetch(`${backendBaseUrl}/api/status`);
    if (!response.ok) {
      throw new Error(`Backend returned ${response.status} for /api/status`);
    }
    const payload = (await response.json()) as RuntimeStatus;
    setRuntimeStatus(payload);
    setOpenCodeHealth(payload.opencode);
  }, [backendBaseUrl]);

  const fetchSessions = useCallback(async () => {
    const res = await fetch(`${backendBaseUrl}/api/opencode/sessions`);
    if (!res.ok) throw new Error(`Backend returned ${res.status} for /api/opencode/sessions`);
    const payload = (await res.json()) as { sessions?: OpenCodeSession[] };
    setOpencodeSessions(payload.sessions ?? []);
    setSessionsView(true);
    setSelectedSession(undefined);
  }, [backendBaseUrl]);

  const inspectSession = useCallback(
    async (sessionId: string) => {
      const res = await fetch(`${backendBaseUrl}/api/opencode/sessions/${sessionId}/messages`);
      if (!res.ok) throw new Error(`Backend returned ${res.status}`);
      const payload = (await res.json()) as { messages?: OpenCodeMessage[] };
      setSelectedSession({ id: sessionId, messages: payload.messages ?? [] });
    },
    [backendBaseUrl],
  );

  const connectToRun = useCallback(
    (runId: string) => {
      eventSourceRef.current?.close();
      setEvents([]);
      setBusy(true);
      runStartRef.current = null;
      setStatus('Reconnecting to running scan...');

      const es = new EventSource(`${backendBaseUrl}/api/runs/${runId}/events/stream`);
      eventSourceRef.current = es;

      es.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data as string) as RunEvent;
          // Anchor run-start time to the first event's timestamp.
          if (!runStartRef.current && event.timestamp) {
            runStartRef.current = new Date(event.timestamp);
          }
          if (event.phase === 'opencode_pass') {
            const passStart = event.timestamp ? new Date(event.timestamp).getTime() : Date.now();
            phaseStartRef.current = passStart;
            setElapsedSeconds(Math.floor((Date.now() - passStart) / 1000));
            setPhaseLabel(event.message ?? null);
            setEvents((prev) => [...prev, event]);
            return;
          }
          setEvents((prev) => [...prev, event]);
          if (event.message) setStatus(event.message.slice(0, 100));
        } catch {
          // ignore malformed frames
        }
      };

      es.addEventListener('done', () => {
        es.close();
        eventSourceRef.current = null;
        setBusy(false);
        setStatus('Scan complete');
        const slug = selectedProjectSlugRef.current;
        const runsUrl = slug ? `${backendBaseUrl}/api/projects/${slug}/runs` : `${backendBaseUrl}/api/runs`;
        fetch(runsUrl)
          .then((r) => r.json())
          .then((payload) => {
            const runs = (payload as { runs?: ProjectRun[] }).runs ?? [];
            const completed = runs.find((r) => (r.runId ?? r.run_id) === runId);
            onProjectRunsChange(runs);
            if (completed) {
              onSelectedRunIdChange(runId);
              if (completed.opencodeSessionId) {
                inspectSession(completed.opencodeSessionId).catch(() => {});
                setSessionsView(true);
              }
            }
          })
          .catch(() => {});
      });

      es.onerror = () => {
        es.close();
        eventSourceRef.current = null;
        setBusy(false);
        setStatus('Stream closed');
      };
    },
    [backendBaseUrl, inspectSession, onProjectRunsChange, onSelectedRunIdChange],
  );

  useEffect(() => {
    selectedProjectSlugRef.current = selectedProjectSlug;
  }, [selectedProjectSlug]);

  useEffect(() => {
    setProjectNameDraft(selectedProject?.name ?? '');
    setEditingProjectName(false);
  }, [selectedProject?.name, selectedProjectSlug]);

  useEffect(() => {
    refreshProjects().catch((error) => {
      setStatus(error instanceof Error ? error.message : 'Unable to load projects');
    });
    refreshRuntimeStatus().catch(() => {
      setRuntimeStatus({ status: 'unknown' });
      setOpenCodeHealth({ configured: false, status: 'unknown', message: 'OpenCode health is unavailable.' });
    });
    // Reconnect to any scan that was already running when the component mounted.
    fetch(`${backendBaseUrl}/api/runs/inflight`)
      .then((r) => (r.ok ? r.json() : null))
      .then((payload) => {
        const runIds = (payload as { runIds?: string[] } | null)?.runIds ?? [];
        if (runIds[0]) connectToRun(runIds[0]);
      })
      .catch(() => {});
    return () => {
      eventSourceRef.current?.close();
    };
  }, [backendBaseUrl, connectToRun, refreshProjects, refreshRuntimeStatus]);

  useEffect(() => {
    setInventory(selectedProject?.repositories ?? []);
  }, [selectedProject]);

  useEffect(() => {
    if (!selectedProjectSlug) {
      setGithubToken('');
      setRememberGithubToken(false);
      return;
    }
    const stored = window.sessionStorage.getItem(githubTokenStorageKey(selectedProjectSlug)) ?? '';
    setGithubToken(stored);
    setRememberGithubToken(Boolean(stored));
  }, [selectedProjectSlug]);

  useEffect(() => {
    refreshProjectRuns(selectedProjectSlug).catch((error) => {
      onProjectRunsChange([]);
      onSelectedRunIdChange('');
      setStatus(error instanceof Error ? error.message : 'Unable to load project runs');
    });
  }, [onProjectRunsChange, onSelectedRunIdChange, refreshProjectRuns, selectedProjectSlug]);

  useEffect(() => {
    const runId = runIdOf(selectedRun);
    if (!runId) {
      setEvents([]);
      runStartRef.current = null;
      return;
    }
    if (busy) {
      return;
    }
    fetch(`${backendBaseUrl}/api/runs/${runId}/events`)
      .then((response) => (response.ok ? response.json() : { events: [] }))
      .then((payload) => {
        const nextEvents = ((payload as { events?: RunEvent[] }).events ?? []) as RunEvent[];
        setEvents(nextEvents);
        const firstTimestamp = nextEvents.find((event) => event.timestamp)?.timestamp;
        runStartRef.current = firstTimestamp ? new Date(firstTimestamp) : null;
      })
      .catch(() => {
        setEvents([]);
        runStartRef.current = null;
      });
  }, [backendBaseUrl, busy, selectedRun]);

  useEffect(() => {
    if (busy) {
      phaseStartRef.current = null;
      setPhaseLabel(null);
      setElapsedSeconds(0);
      elapsedTimerRef.current = setInterval(() => {
        const start = phaseStartRef.current;
        setElapsedSeconds(start != null ? Math.floor((Date.now() - start) / 1000) : (s) => s + 1);
      }, 1000);
    } else {
      if (elapsedTimerRef.current) {
        clearInterval(elapsedTimerRef.current);
        elapsedTimerRef.current = null;
      }
      phaseStartRef.current = null;
      setPhaseLabel(null);
    }
    return () => {
      if (elapsedTimerRef.current) clearInterval(elapsedTimerRef.current);
    };
  }, [busy]);

  useEffect(() => {
    if (!blockingNotice) {
      return;
    }
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    return () => window.removeEventListener('beforeunload', warnBeforeUnload);
  }, [blockingNotice]);

  const createProject = async () => {
    if (!projectName.trim()) {
      setStatus('Project name is required');
      return;
    }
    setBusy(true);
    setStatus('Creating project');
    try {
      const response = await postJson(`${backendBaseUrl}/api/projects`, { name: projectName.trim() });
      const project = response.project as Project;
      await refreshProjects();
      onSelectedProjectSlugChange(project.slug);
      setProjectName('');
      setCreatingProject(false);
      setStatus(`Created ${project.name}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Project creation failed');
    } finally {
      setBusy(false);
    }
  };

  const addDraftRepo = () => {
    setDraftRepos((current) => [...current, { id: Date.now(), url: '', branch: '' }]);
  };

  const removeDraftRepo = (id: number) => {
    setDraftRepos((current) => current.filter((repo) => repo.id !== id));
  };

  const importDraftRepo = async (draft: RepositoryDraft) => {
    const url = draft.url.trim();
    if (!url) {
      setStatus('Enter a repository URL');
      return;
    }
    if (!selectedProjectSlug) {
      setStatus('Select or create a project first');
      return;
    }
    setBusy(true);
    setBlockingNotice({
      title: 'Cloning repository',
      message: `Hold on, we're cloning ${repositoryNameFromUrl(url)} into this project.`,
    });
    setStatus('Adding repository');
    try {
      const branch = draft.branch.trim() || undefined;
      const response = await postJson(`${backendBaseUrl}/api/projects/${selectedProjectSlug}/repositories/import`, {
        ...githubCredentialPayload(githubToken),
        repositories: [{ url, ...(branch ? { branch } : {}) }],
      });
      setInventory(response.repositories ?? []);
      setDraftRepos((current) => current.filter((repo) => repo.id !== draft.id));
      await refreshProjects();
      setStatus(`Added ${repositoryNameFromUrl(url)}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Repository add failed');
    } finally {
      setBlockingNotice(null);
      setBusy(false);
    }
  };

  const importAllDraftRepos = async () => {
    if (!selectedProjectSlug) {
      setStatus('Select or create a project first');
      return;
    }
    const clean = draftRepos
      .filter((repo) => repo.url.trim())
      .map(({ url, branch }) => {
        const b = branch.trim();
        return b ? { url: url.trim(), branch: b } : { url: url.trim() };
      });
    if (clean.length === 0) {
      setStatus('Add at least one repository URL');
      return;
    }
    setBusy(true);
    setBlockingNotice({
      title: 'Cloning repositories',
      message: `Hold on, we're cloning ${clean.length} repositories into this project.`,
    });
    setStatus(`Importing ${clean.length} repositories`);
    try {
      const response = await postJson(`${backendBaseUrl}/api/projects/${selectedProjectSlug}/repositories/import`, {
        ...githubCredentialPayload(githubToken),
        repositories: clean,
      });
      setInventory(response.repositories ?? []);
      setDraftRepos([]);
      await refreshProjects();
      setStatus(`Imported ${clean.length} repositories`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Repository import failed');
    } finally {
      setBlockingNotice(null);
      setBusy(false);
    }
  };

  const removeRepository = async (repoName: string) => {
    if (!selectedProjectSlug) return;
    setBusy(true);
    setStatus(`Removing ${repoName}`);
    try {
      const response = await fetch(
        `${backendBaseUrl}/api/projects/${selectedProjectSlug}/repositories/${encodeURIComponent(repoName)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { message?: string };
        throw new Error(body.message ?? `Backend returned ${response.status}`);
      }
      const payload =
        response.status !== 204
          ? ((await response.json().catch(() => ({}))) as { repositories?: RepositoryInventory[] })
          : {};
      const updatedRepos = payload.repositories ?? inventory.filter((repo) => repo.name !== repoName);
      setInventory(updatedRepos);
      onProjectsChange(
        projects.map((proj) => (proj.slug === selectedProjectSlug ? { ...proj, repositories: updatedRepos } : proj)),
      );
      await refreshProjects();
      setStatus(`Removed ${repoName}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Repository removal failed');
    } finally {
      setBusy(false);
    }
  };

  const renameProject = async () => {
    if (!selectedProjectSlug) {
      setStatus('Select or create a project first');
      return;
    }
    const cleanName = projectNameDraft.trim();
    if (!cleanName) {
      setStatus('Project name is required');
      return;
    }
    setBusy(true);
    setStatus('Renaming project');
    try {
      await patchJson(`${backendBaseUrl}/api/projects/${selectedProjectSlug}`, { name: cleanName });
      await refreshProjects();
      setEditingProjectName(false);
      setStatus(`Renamed project to ${cleanName}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Project rename failed');
    } finally {
      setBusy(false);
    }
  };

  const deleteProject = async () => {
    if (!selectedProjectSlug || !selectedProject) return;
    const confirmed = window.confirm(
      `Delete ${selectedProject.name}? This removes the project, repositories, scans, and artifacts.`,
    );
    if (!confirmed) return;
    setBusy(true);
    setStatus(`Deleting ${selectedProject.name}`);
    try {
      const response = await fetch(`${backendBaseUrl}/api/projects/${selectedProjectSlug}`, { method: 'DELETE' });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { message?: string };
        throw new Error(body.message ?? `Backend returned ${response.status}`);
      }
      const remaining = projects.filter((project) => project.slug !== selectedProjectSlug);
      onProjectsChange(remaining);
      onSelectedProjectSlugChange(remaining[0]?.slug ?? '');
      onProjectRunsChange([]);
      onSelectedRunIdChange('');
      setInventory([]);
      window.sessionStorage.removeItem(githubTokenStorageKey(selectedProjectSlug));
      setStatus(`Deleted ${selectedProject.name}`);
      await refreshProjects();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Project delete failed');
    } finally {
      setBusy(false);
    }
  };

  const syncRepositories = async () => {
    if (!selectedProjectSlug) {
      setStatus('Select or create a project first');
      return;
    }
    setBusy(true);
    setBlockingNotice({
      title: 'Syncing repositories',
      message: "Hold on, we're cloning missing repositories and pulling existing repositories.",
    });
    setStatus('Syncing repositories (clone missing, pull existing)');
    try {
      let finalRepositories: RepositoryInventory[] | undefined;
      let finalFailureCount = 0;
      await postEventStream(
        `${backendBaseUrl}/api/projects/${selectedProjectSlug}/repositories/sync/stream`,
        githubCredentialPayload(githubToken),
        (event) => {
          if (event.type === 'start') {
            const count = event.repositoryCount;
            const message = typeof count === 'number' ? `Syncing ${count} repositories.` : 'Repository sync started.';
            setStatus(message);
            setBlockingNotice({ title: 'Syncing repositories', message });
            return;
          }
          if (event.type === 'progress') {
            const message = syncProgressMessage(event);
            setStatus(message);
            setBlockingNotice({ title: 'Syncing repositories', message });
            return;
          }
          if (event.type === 'complete') {
            finalRepositories = event.repositories ?? [];
            finalFailureCount = event.failureCount ?? 0;
            setInventory(finalRepositories);
            setBlockingNotice({
              title: 'Syncing repositories',
              message: finalFailureCount ? `Sync completed with ${finalFailureCount} failures.` : 'Repository sync complete.',
            });
            return;
          }
          if (event.type === 'error') {
            throw new Error(event.message ?? 'Repository sync failed');
          }
        },
      );
      await refreshProjects();
      const repositories = finalRepositories ?? [];
      const failures =
        finalFailureCount ||
        repositories.filter((repo: RepositoryInventory) => repo.status === 'failed' || repo.error || repo.git?.error)
          .length;
      setStatus(
        failures
          ? `Repository sync completed with ${failures} failure${failures === 1 ? '' : 's'}`
          : 'Repository sync complete',
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Repository sync failed');
    } finally {
      setBlockingNotice(null);
      setBusy(false);
    }
  };

  const updateGithubToken = (value: string) => {
    setGithubToken(value);
    if (rememberGithubToken && selectedProjectSlug) {
      const key = githubTokenStorageKey(selectedProjectSlug);
      if (value.trim()) {
        window.sessionStorage.setItem(key, value);
      } else {
        window.sessionStorage.removeItem(key);
      }
    }
  };

  const updateRememberGithubToken = (enabled: boolean) => {
    setRememberGithubToken(enabled);
    if (!selectedProjectSlug) return;
    const key = githubTokenStorageKey(selectedProjectSlug);
    if (enabled && githubToken.trim()) {
      window.sessionStorage.setItem(key, githubToken);
    }
    if (!enabled) {
      window.sessionStorage.removeItem(key);
    }
  };

  const monitorProject = async () => {
    if (!selectedProjectSlug) {
      setStatus('Select or create a project first');
      return;
    }
    setBusy(true);
    setStatus('Starting scan');
    try {
      const response = await postJson(`${backendBaseUrl}/api/projects/${selectedProjectSlug}/monitor`, {});
      const run = response.run as ProjectRun;
      const runId = run.runId ?? run.run_id;
      if (!runId) {
        setStatus('Scan started (no run id returned)');
        setBusy(false);
        return;
      }
      connectToRun(runId);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Scan failed');
      setBusy(false);
    }
  };

  return (
    <section className="projects-view">
      <div className="projects-header">
        <div>
          <h2>Projects</h2>
          <p>{status}</p>
        </div>
        <div className="projects-header-actions">
          <button className="tool-button" disabled={busy} onClick={refreshProjects} type="button">
            <RefreshCw size={16} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      <div className="projects-grid">
        <section className="project-panel">
          <div className="project-summary">
            <SummaryMetric label="Project" value={selectedProject?.name ?? 'None'} />
            <SummaryMetric label="Repos" value={inventory.length.toString()} />
            <SummaryMetric label="Imported" value={importedCount.toString()} tone={importedCount ? 'ok' : 'neutral'} />
            <SummaryMetric
              label="Workspace"
              value={workspaceUnavailable ? 'Missing' : 'Ready'}
              tone={workspaceUnavailable ? 'warn' : 'ok'}
            />
            <SummaryMetric
              label="Warnings"
              value={(warningCount + dirtyCount).toString()}
              tone={warningCount || dirtyCount ? 'warn' : 'ok'}
            />
          </div>

          <div className="repo-list-header">
            <h3>Project</h3>
            <div className="repo-list-header-actions">
              <button
                className="tool-button"
                disabled={!selectedProjectSlug || busy}
                onClick={() => setEditingProjectName((current) => !current)}
                title="Rename project"
                type="button"
              >
                <Pencil size={16} />
                <span>Rename</span>
              </button>
              <button
                className="tool-button"
                onClick={() => {
                  setCreatingProject(true);
                  onSelectedProjectSlugChange('');
                }}
                title="New project"
                type="button"
              >
                <Plus size={16} />
                <span>New</span>
              </button>
              <button
                className="icon-button icon-button--danger"
                disabled={!selectedProjectSlug || busy}
                onClick={deleteProject}
                title="Delete project"
                type="button"
              >
                <Trash2 size={16} />
              </button>
            </div>
          </div>

          {editingProjectName && selectedProjectSlug ? (
            <div className="project-form-row">
              <input
                autoFocus
                value={projectNameDraft}
                onChange={(event) => setProjectNameDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') renameProject();
                  if (event.key === 'Escape') {
                    setEditingProjectName(false);
                    setProjectNameDraft(selectedProject?.name ?? '');
                  }
                }}
                placeholder="Project name"
              />
              <button className="tool-button" disabled={busy} onClick={renameProject} type="button">
                <CheckCircle2 size={16} />
                <span>Save</span>
              </button>
            </div>
          ) : null}

          {creatingProject && (
            <div className="project-form-row">
              <input
                autoFocus
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') createProject();
                  if (event.key === 'Escape') {
                    setCreatingProject(false);
                    setProjectName('');
                  }
                }}
                placeholder="Project name"
              />
              <button className="tool-button" disabled={busy} onClick={createProject} type="button">
                <Plus size={16} />
                <span>Create</span>
              </button>
              <button
                className="icon-button"
                onClick={() => {
                  setCreatingProject(false);
                  setProjectName('');
                }}
                title="Cancel"
                type="button"
              >
                <Trash2 size={16} />
              </button>
            </div>
          )}

          <div className="project-list">
            {projects.length === 0 && !creatingProject && (
              <p className="repo-list-empty">No projects yet. Create one above.</p>
            )}
            {projects.map((project) => (
              <button
                className={`project-list-item ${selectedProjectSlug === project.slug ? 'project-list-item--active' : ''}`}
                key={project.slug}
                onClick={() => {
                  onSelectedProjectSlugChange(project.slug);
                  setCreatingProject(false);
                }}
                type="button"
              >
                <span className="project-list-item-name">{project.name}</span>
                <span className="project-list-item-slug">{project.slug}</span>
              </button>
            ))}
          </div>

          <div className="repo-list-header">
            <h3>Repositories</h3>
            <div className="repo-list-header-actions">
              {draftRepos.length > 1 && (
                <button
                  className="tool-button"
                  disabled={busy || !selectedProjectSlug}
                  onClick={importAllDraftRepos}
                  title="Import all pending repositories"
                  type="button"
                >
                  <CheckCircle2 size={16} />
                  <span>Clone All</span>
                </button>
              )}
              <button
                className="tool-button"
                disabled={!selectedProjectSlug}
                onClick={addDraftRepo}
                title="Add repository"
                type="button"
              >
                <Plus size={16} />
                <span>Add</span>
              </button>
            </div>
          </div>

          <div className="project-form-row">
            <input
              autoComplete="off"
              onChange={(event) => updateGithubToken(event.target.value)}
              placeholder="GitHub token for private repositories"
              type="password"
              value={githubToken}
            />
            <label className="checkbox-control">
              <input
                checked={rememberGithubToken}
                disabled={!selectedProjectSlug}
                onChange={(event) => updateRememberGithubToken(event.target.checked)}
                type="checkbox"
              />
              <span>Remember for session</span>
            </label>
          </div>

          <div className="project-actions project-actions--top">
            <button
              className="tool-button"
              disabled={busy || !selectedProjectSlug}
              onClick={syncRepositories}
              title="Clone missing repositories and pull existing repositories"
              type="button"
            >
              <DownloadCloud size={16} />
              <span>Sync Repos</span>
            </button>
            <button
              className="tool-button"
              disabled={busy || !selectedProjectSlug || workspaceUnavailable}
              onClick={monitorProject}
              title={
                workspaceUnavailable
                  ? 'This runtime cannot access the project workspace.'
                  : 'Start scan for this project'
              }
              type="button"
            >
              <Play size={16} />
              <span>Scan</span>
            </button>
          </div>

          <div className="repo-list">
            {inventory.length === 0 && draftRepos.length === 0 && (
              <p className="repo-list-empty">No repositories yet.</p>
            )}
            {inventory.map((repo) => (
              <div className="repo-item" key={`${repo.name}-${repo.path}`}>
                <div className="repo-item-meta">
                  <div className="repo-item-title">
                    <strong>{repo.name}</strong>
                    <StatusPill status={repo.status ?? 'unknown'} />
                  </div>
                  <small>
                    {repo.git?.branch ?? 'detached'} / {shortCommit(repo.git?.commit)}
                  </small>
                  {repo.git?.remote_url ? <small>{repo.git.remote_url}</small> : null}
                  {repo.workspace?.available === false ? <em>Workspace path is unavailable on this runtime.</em> : null}
                  {repo.error || repo.git?.error ? <em>{repo.error ?? repo.git?.error}</em> : null}
                </div>
                <button
                  className="icon-button"
                  disabled={busy}
                  onClick={() => repo.name && removeRepository(repo.name)}
                  title="Remove repository"
                  type="button"
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
            {draftRepos.map((draft) => (
              <div className="repository-draft" key={draft.id}>
                <div className="repository-row">
                  <input
                    autoFocus
                    value={draft.url}
                    onChange={(event) => updateRepository(draft.id, { url: event.target.value }, setDraftRepos)}
                    onKeyDown={(event) => event.key === 'Enter' && importDraftRepo(draft)}
                    placeholder="Repository URL"
                  />
                  <input
                    value={draft.branch}
                    onChange={(event) => updateRepository(draft.id, { branch: event.target.value }, setDraftRepos)}
                    onKeyDown={(event) => event.key === 'Enter' && importDraftRepo(draft)}
                    placeholder="Branch (optional)"
                    className="branch-input"
                  />
                  <button
                    className="tool-button"
                    disabled={busy || !draft.url.trim()}
                    onClick={() => importDraftRepo(draft)}
                    type="button"
                  >
                    <CheckCircle2 size={16} />
                    <span>Add + Clone</span>
                  </button>
                  <button
                    className="icon-button"
                    onClick={() => removeDraftRepo(draft.id)}
                    title="Cancel"
                    type="button"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
                {draft.url.trim() ? (
                  <small className="repository-draft-name">
                    <GitBranch size={13} />
                    {repositoryNameFromUrl(draft.url)}
                    {draft.branch.trim() ? <span className="branch-badge">{draft.branch.trim()}</span> : null}
                  </small>
                ) : null}
              </div>
            ))}
          </div>
        </section>

        <section className="project-panel">
          <div className="repo-list-header">
            <h3>Scan Diagnostics</h3>
            <div className="repo-list-header-actions">
              <select
                className="run-select"
                disabled={!projectRuns.length || busy}
                onChange={(event) => {
                  const runId = event.target.value;
                  const run = projectRuns.find((candidate) => runIdOf(candidate) === runId);
                  onSelectedRunIdChange(runId);
                  if (run) {
                    onLoadRunSnapshot(run).catch((error) =>
                      setStatus(error instanceof Error ? error.message : 'Failed to load run snapshot'),
                    );
                  }
                }}
                title="Saved scan version"
                value={selectedRunId}
              >
                {projectRuns.length ? (
                  projectRuns.map((run, index) => (
                    <option key={runIdOf(run) || index} value={runIdOf(run)}>
                      {formatRunOption(run, index, projectRuns.length)}
                    </option>
                  ))
                ) : (
                  <option value="">No saved scans</option>
                )}
              </select>
              {busy ? (
                <>
                  <Loader2 className="spin" size={16} />
                  {phaseLabel && <span className="elapsed-label">{phaseLabel}</span>}
                  <span className="elapsed-timer">{formatElapsed(elapsedSeconds)}</span>
                </>
              ) : null}
              <button
                className="tool-button"
                onClick={() =>
                  fetchSessions().catch((e) => setStatus(e instanceof Error ? e.message : 'Failed to load sessions'))
                }
                title="Browse raw OpenCode sessions"
                type="button"
              >
                <MessageSquare size={16} />
                <span>OpenCode Sessions</span>
              </button>
            </div>
          </div>

          <div className="opencode-card">
            <div className="opencode-card-icon">
              {isHealthyStatus(runtimeStatus?.status) ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
            </div>
            <div>
              <strong>Backend {runtimeStatus?.status ?? 'unknown'}</strong>
              <span>
                {formatRepositoryMode(runtimeStatus?.repositoryMode)} / {runtimeStatus?.storage ?? 'storage unknown'}
              </span>
            </div>
          </div>

          <div className="opencode-card">
            <div className="opencode-card-icon">
              {isHealthyStatus(runtimeStatus?.projectWorkspace?.status) ? (
                <CheckCircle2 size={18} />
              ) : (
                <AlertCircle size={18} />
              )}
            </div>
            <div>
              <strong>Project workspace {runtimeStatus?.projectWorkspace?.status ?? 'unknown'}</strong>
              <span>{runtimeStatus?.projectWorkspace?.message ?? 'Project workspace status is unavailable.'}</span>
              {runtimeStatus?.projectWorkspace?.path ? <small>{runtimeStatus.projectWorkspace.path}</small> : null}
            </div>
          </div>

          <div className="opencode-card">
            <div className="opencode-card-icon">
              {isHealthyStatus(opencodeHealth?.status) ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
            </div>
            <div>
              <strong>OpenCode {opencodeHealth?.status ?? 'unknown'}</strong>
              <span>{opencodeHealth?.message ?? 'OpenCode health is unavailable.'}</span>
              {opencodeHealth?.base_url || opencodeHealth?.baseUrl ? (
                <small>{opencodeHealth.base_url ?? opencodeHealth.baseUrl}</small>
              ) : null}
            </div>
          </div>

          {selectedRun ? (
            <div className="opencode-card">
              <div className="opencode-card-icon">
                {selectedRun.status === 'failed' ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}
              </div>
              <div>
                <strong>Saved scan {shortId(runIdOf(selectedRun))}</strong>
                <span>
                  {formatRunOutcome(selectedRun.status)} /{' '}
                  {formatDateTime(selectedRun.startedAt ?? selectedRun.started_at)}
                </span>
                {formatRunUsage(selectedRun) ? <small>{formatRunUsage(selectedRun)}</small> : null}
                {selectedRunReview?.warnings.length ? <ValidationNotes review={selectedRunReview} compact /> : null}
                {sessionIdOf(selectedRun) ? (
                  <button
                    className="inline-link-button"
                    onClick={() => {
                      const sessionId = sessionIdOf(selectedRun);
                      if (!sessionId) return;
                      inspectSession(sessionId).catch((e) =>
                        setStatus(e instanceof Error ? e.message : 'Failed to load OpenCode session'),
                      );
                      setSessionsView(true);
                    }}
                    type="button"
                  >
                    OpenCode session {shortId(sessionIdOf(selectedRun))}
                  </button>
                ) : (
                  <small>No linked OpenCode session</small>
                )}
              </div>
            </div>
          ) : null}

          {!sessionsView ? (
            <div className="event-log">
              {events.length === 0 ? (
                <p>No scan events yet.{busy ? ' Waiting for scan to start...' : ''}</p>
              ) : (
                events.map((event, index) => (
                  <EventRow key={`${event.timestamp}-${index}`} event={event} runStart={runStartRef.current} />
                ))
              )}
              {busy && (
                <div className="event-row event-row--loading">
                  <Loader2 className="spin" size={14} />
                  <p>Running…</p>
                </div>
              )}
            </div>
          ) : selectedSession ? (
            <div className="event-log">
              <div className="repo-list-header">
                <button className="tool-button" onClick={() => setSelectedSession(undefined)} type="button">
                  <ChevronLeft size={16} />
                  <span>OpenCode Sessions</span>
                </button>
                <small>{selectedSession.id}</small>
              </div>
              {selectedSession.messages.length === 0 ? (
                <p>No messages in this session.</p>
              ) : (
                selectedSession.messages.map((msg, i) => <SessionMessage key={i} message={msg} />)
              )}
            </div>
          ) : (
            <div className="event-log">
              <div className="repo-list-header">
                <button className="tool-button" onClick={() => setSessionsView(false)} type="button">
                  <ChevronLeft size={16} />
                  <span>Scan Events</span>
                </button>
                <span>{opencodeSessions.length} OpenCode session(s)</span>
              </div>
              {opencodeSessions.length === 0 ? (
                <p>No OpenCode sessions found.</p>
              ) : (
                opencodeSessions.map((session) => (
                  <button
                    className="event-row session-row"
                    key={session.id}
                    onClick={() =>
                      inspectSession(session.id).catch((e) =>
                        setStatus(e instanceof Error ? e.message : 'Failed to load session'),
                      )
                    }
                    type="button"
                  >
                    <span>{session.id.slice(0, 8)}</span>
                    <strong>{session.title ?? '(no title)'}</strong>
                    {session.cost != null ? <small>{formatCurrency(session.cost)}</small> : null}
                    <small>{session.time?.created ? new Date(session.time.created).toLocaleString() : ''}</small>
                  </button>
                ))
              )}
            </div>
          )}
        </section>
      </div>

      {blockingNotice ? (
        <div className="blocking-modal" role="alertdialog" aria-modal="true" aria-labelledby="blocking-modal-title">
          <div className="blocking-modal__panel">
            <Loader2 className="spin" size={28} />
            <div>
              <h3 id="blocking-modal-title">{blockingNotice.title}</h3>
              <p>{blockingNotice.message}</p>
              <small>This can take a bit for large or private repositories. Do not refresh or close this tab.</small>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function SummaryMetric({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  tone?: 'neutral' | 'ok' | 'warn';
}) {
  return (
    <div className={`summary-metric summary-metric--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const tone =
    normalized.includes('fail') || normalized.includes('error')
      ? 'warn'
      : normalized.includes('import') || normalized.includes('refresh') || normalized.includes('sync')
        ? 'ok'
        : 'neutral';
  return <span className={`status-pill status-pill--${tone}`}>{formatRunOutcome(status)}</span>;
}

function isHealthyStatus(status?: string) {
  return status === 'ok' || status === 'unconfigured';
}

function runIdOf(run?: ProjectRun): string {
  return run?.runId ?? run?.run_id ?? '';
}

function sessionIdOf(run?: ProjectRun): string | undefined {
  return run?.opencodeSessionId ?? run?.opencode_session_id;
}

function shortId(value?: string): string {
  return value ? value.slice(0, 8) : 'unknown';
}

function formatDateTime(value?: string): string {
  return value ? new Date(value).toLocaleString() : 'unknown date';
}

function formatRunOption(run: ProjectRun, index: number, total: number): string {
  const startedAt = run.startedAt ?? run.started_at;
  const version = Math.max(1, total - index);
  const label = index === 0 ? `v${version} latest` : `v${version}`;
  const date = startedAt ? new Date(startedAt).toLocaleString() : 'unknown date';
  const status = formatRunOutcome(run.status);
  const session = sessionIdOf(run);
  const usage = formatRunUsage(run);
  const parts = [`${label} - ${date} - ${status}`];
  if (usage) parts.push(usage);
  if (session) parts.push(`OC ${shortId(session)}`);
  return parts.join(' - ');
}

function formatRepositoryMode(mode?: string) {
  if (mode === 'backend-workspace') return 'backend workspace';
  return 'repository mode unknown';
}

function githubCredentialPayload(githubToken: string) {
  const trimmed = githubToken.trim();
  return trimmed ? { githubToken: trimmed } : {};
}

function githubTokenStorageKey(projectSlug: string) {
  return `projectAnalyzer.githubToken.${projectSlug}`;
}

async function postJson(url: string, payload: object) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error((body as { message?: string }).message ?? `Backend returned ${response.status}`);
  }
  return body;
}

async function postEventStream(url: string, payload: object, onEvent: (event: SyncStreamEvent) => void) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { message?: string };
    throw new Error(body.message ?? `Backend returned ${response.status}`);
  }
  if (!response.body) {
    throw new Error('Backend did not return a sync stream');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      const payloadText = frame
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n');
      if (!payloadText) continue;
      const event = JSON.parse(payloadText) as SyncStreamEvent;
      if (event.type === 'done') return;
      onEvent(event);
    }
    if (done) break;
  }
}

async function patchJson(url: string, payload: object) {
  const response = await fetch(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error((body as { message?: string }).message ?? `Backend returned ${response.status}`);
  }
  return body;
}

function syncProgressMessage(event: SyncStreamEvent) {
  const repo = event.repository ?? 'repository';
  if (typeof event.percent === 'number' && event.stage) {
    return `${repo}: ${event.stage} ${event.percent}%`;
  }
  if (event.message) return event.message;
  if (event.phase?.endsWith('_start')) return `${repo}: starting`;
  if (event.phase?.endsWith('_complete')) return `${repo}: complete`;
  if (event.phase?.endsWith('_failed')) return `${repo}: failed`;
  return `${repo}: syncing`;
}

function formatRunUsage(run: ProjectRun): string {
  const totalTokens = run.totalTokens ?? run.total_tokens;
  const inputTokens = run.inputTokens ?? run.input_tokens;
  const outputTokens = run.outputTokens ?? run.output_tokens;
  const cost = run.opencodeCost ?? run.opencode_cost;
  const parts: string[] = [];
  if (totalTokens != null) {
    parts.push(`${formatCompactNumber(totalTokens)} tokens`);
  } else if (inputTokens != null || outputTokens != null) {
    parts.push(`${formatCompactNumber((inputTokens ?? 0) + (outputTokens ?? 0))} tokens`);
  }
  if (cost != null) {
    parts.push(formatCurrency(cost));
  }
  return parts.join(' / ');
}

function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: value < 1 ? 4 : 2,
  }).format(value);
}

function updateRepository(
  id: number,
  patch: Partial<RepositoryDraft>,
  setRepositories: Dispatch<SetStateAction<RepositoryDraft[]>>,
) {
  setRepositories((current) => current.map((repo) => (repo.id === id ? { ...repo, ...patch } : repo)));
}

function shortCommit(commit?: string | null) {
  return commit ? commit.slice(0, 8) : 'no commit';
}

function SessionMessage({ message }: { message: OpenCodeMessage }) {
  if (message.type === 'assistant') {
    const text =
      message.parts
        ?.filter((p) => p.type === 'text')
        .map((p) => p.text)
        .join('') ?? '';
    return (
      <div className="event-row session-message session-message--assistant">
        <strong>assistant</strong>
        <p style={{ whiteSpace: 'pre-wrap' }}>{text}</p>
      </div>
    );
  }
  if (message.type === 'tool-call') {
    return (
      <div className="event-row session-message session-message--tool">
        <strong>tool</strong>
        <p>
          {message.tool}({JSON.stringify(message.input ?? {}).slice(0, 120)})
        </p>
      </div>
    );
  }
  if (message.type === 'user') {
    const text =
      message.parts
        ?.filter((p) => p.type === 'text')
        .map((p) => p.text)
        .join('') ?? '';
    return (
      <div className="event-row session-message session-message--user">
        <strong>user</strong>
        <p>
          {text.slice(0, 200)}
          {text.length > 200 ? '…' : ''}
        </p>
      </div>
    );
  }
  return null;
}

function ValidationNotes({ review, compact = false }: { review: ValidationReview; compact?: boolean }) {
  return (
    <div className={compact ? 'validation-notes validation-notes--compact' : 'validation-notes'}>
      <strong>{review.summary ?? 'Generated SysML needs review.'}</strong>
      <ul>
        {review.warnings.map((warning) => (
          <li key={warning}>{warning}</li>
        ))}
      </ul>
      {review.metrics ? <small>{formatValidationMetrics(review.metrics)}</small> : null}
    </div>
  );
}

function EventRow({ event, runStart }: { event: RunEvent; runStart: Date | null }) {
  const phase = event.phase ?? '';
  const validationReview = validationReviewFromEvent(event);
  const timeChip =
    runStart && event.timestamp ? (
      <span className="event-time">
        +{formatElapsed(Math.max(0, Math.floor((new Date(event.timestamp).getTime() - runStart.getTime()) / 1000)))}
      </span>
    ) : null;

  if (phase === 'opencode_pass') {
    return (
      <div className="event-row event-row--pass">
        {timeChip}
        <p>{event.message}</p>
      </div>
    );
  }
  if (phase === 'opencode_delta') {
    return (
      <div className="event-row event-row--delta">
        {timeChip}
        <p>{event.message}</p>
      </div>
    );
  }
  if (phase === 'opencode_reasoning') {
    return (
      <div className="event-row event-row--reasoning">
        {timeChip}
        <span>thinking</span>
        <p>{event.message}</p>
      </div>
    );
  }
  if (validationReview) {
    return (
      <div className="event-row event-row--validation">
        {timeChip}
        <span>validation</span>
        <ValidationNotes review={validationReview} />
      </div>
    );
  }
  return (
    <div className="event-row">
      {timeChip}
      <span>{phase}</span>
      <strong>{event.level}</strong>
      <p>{event.message}</p>
    </div>
  );
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatValidationMetrics(metrics: Record<string, number>): string {
  const parts = [
    metricPart(metrics.part_defs, 'part defs'),
    metricPart(metrics.ports, 'ports'),
    metricPart(metrics.ports_with_direction, 'directed'),
    metricPart(metrics.connects, 'connections'),
  ].filter(Boolean);
  return parts.join(' / ');
}

function metricPart(value: number | undefined, label: string): string {
  return value == null ? '' : `${value} ${label}`;
}

function repositoryNameFromUrl(url: string) {
  const trimmedUrl = url.trim().replace(/[/\\]+$/, '');
  const lastSegment =
    trimmedUrl
      .split(/[/\\:]/)
      .filter(Boolean)
      .pop() ?? 'repository';
  return lastSegment.replace(/\.git$/i, '') || 'repository';
}
