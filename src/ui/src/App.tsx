import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  useEdgesState,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
  useUpdateNodeInternals,
  type Edge,
  type Node,
} from '@xyflow/react';
import {
  AlertTriangle,
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Code2,
  FileUp,
  Filter,
  FolderGit2,
  Maximize2,
  RotateCcw,
  Search,
  Server,
} from 'lucide-react';
import CodeView from './CodeView';
import DetailsPanel from './DetailsPanel';
import ProjectOnboarding, { type Project, type ProjectRun } from './ProjectOnboarding';
import SysmlPartNode from './SysmlPartNode';
import { buildExcerptElements, buildTopLevelElements, type BuildContext } from './graph';
import { layoutElements } from './layout';
import { getStackedPortGroups } from './portPlacement';
import { formatRunOutcome, validationReviewFromEvents, type ValidationReview } from './runValidation';
import { parseSysml } from './sysmlParser';
import type {
  DiagramEdgeData,
  DiagramNodeData,
  EdgeFilter,
  NodeCompartmentKey,
  NodeCompartmentState,
  NodePosition,
  PortPlacement,
  Selection,
} from './types';

type ViewMode = 'interconnections' | 'projects' | 'code';
type DiagramView = { path: string[] };
type BackendLoadState = {
  status: 'idle' | 'loading' | 'loaded' | 'error';
  message: string;
};

type BackendArtifact = {
  passId?: string;
  pass_id?: string;
};

type BackendRun = {
  runId?: string;
  run_id?: string;
  projectSlug?: string;
  startedAt?: string;
  started_at?: string;
  status?: string;
  changedCount?: number;
  changed_count?: number;
  unchangedCount?: number;
  unchanged_count?: number;
  artifacts?: BackendArtifact[];
  opencodeSessionId?: string;
  opencodeCost?: number | null;
  opencode_cost?: number | null;
  inputTokens?: number | null;
  input_tokens?: number | null;
  outputTokens?: number | null;
  output_tokens?: number | null;
  totalTokens?: number | null;
  total_tokens?: number | null;
};

type RunEvent = {
  phase?: string;
  level?: string;
  message?: string;
  reasoningSummary?: string | null;
  reasoning_summary?: string | null;
};

function runStartedAt(run: BackendRun): string {
  return run.startedAt ?? run.started_at ?? '';
}

// Pick the most recent run that has a generated pass. The backend returns runs
// newest-first, but don't depend on that ordering — sort by start time descending
// so the latest run always wins regardless of how the list arrives.
function pickLatestRun(runs: BackendRun[]): BackendRun | undefined {
  return runs
    .filter((run) => (run.artifacts ?? []).length > 0)
    .sort((a, b) => runStartedAt(b).localeCompare(runStartedAt(a)))[0];
}

function preferredProjectSlug(projects: Project[], current?: string, latestRunProjectSlug?: string): string {
  const saved = readLastProjectSlug();
  const candidates = [current, saved, latestRunProjectSlug, projects[0]?.slug];
  return candidates.find((slug) => slug && projects.some((project) => project.slug === slug)) ?? '';
}

function readLastProjectSlug(): string {
  try {
    return window.localStorage.getItem(lastProjectStorageKey) ?? '';
  } catch {
    return '';
  }
}

function writeLastProjectSlug(slug: string) {
  try {
    window.localStorage.setItem(lastProjectStorageKey, slug);
  } catch {
    // Browser storage can be unavailable in private or restricted contexts.
  }
}

type BackendReviewSummary = {
  runId?: string;
  passId?: string;
  evidenceRecords: number;
  unresolvedItems: number;
  changedCount: number;
  unchangedCount: number;
  usage: string;
  validation?: ValidationReview;
};

type EvidencePayload = {
  records?: unknown[];
};

type ChangesPayload = {
  changedCount?: number;
  changed_count?: number;
  unchangedCount?: number;
  unchanged_count?: number;
};

const nodeTypes = {
  sysmlPart: SysmlPartNode,
};

const filterLabels: Record<EdgeFilter, string> = {
  all: 'All',
  selected: 'Selected',
  ingress: 'Ingress',
  data: 'Data',
  external: 'External',
};

const backendBaseUrl = import.meta.env.VITE_SYSML_BACKEND_URL?.trim() || '';
const lastProjectStorageKey = 'projectAnalyzer.lastProjectSlug';

export default function App() {
  const [sourceText, setSourceText] = useState('');
  const [sourceName, setSourceName] = useState('');
  const [viewMode, setViewMode] = useState<ViewMode>('interconnections');
  const [view, setView] = useState<DiagramView>({ path: [] });
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<EdgeFilter>('all');
  const [selection, setSelection] = useState<Selection>({ kind: 'none' });
  const [layoutNonce, setLayoutNonce] = useState(0);
  const [layoutBusy, setLayoutBusy] = useState(false);
  const [backendLoadState, setBackendLoadState] = useState<BackendLoadState>({
    status: 'idle',
    message: 'Backend snapshot not loaded',
  });
  const [backendReviewSummary, setBackendReviewSummary] = useState<BackendReviewSummary | undefined>();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectSlug, setSelectedProjectSlug] = useState('');
  const [projectRuns, setProjectRuns] = useState<ProjectRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [portPlacementOverrides, setPortPlacementOverrides] = useState<Record<string, Record<string, PortPlacement>>>(
    {},
  );
  const [lockedPortPlacementOverrides, setLockedPortPlacementOverrides] = useState<
    Record<string, Record<string, boolean>>
  >({});
  const [nodePositionOverrides, setNodePositionOverrides] = useState<Record<string, NodePosition>>({});
  const [nodeCompartmentDefaultOpen, setNodeCompartmentDefaultOpen] = useState(false);
  const [nodeCompartmentOverrides, setNodeCompartmentOverrides] = useState<
    Record<string, Partial<NodeCompartmentState>>
  >({});
  const isNodeDraggingRef = useRef(false);
  const layoutRequestedRef = useRef(true);
  const hasFitInitialViewRef = useRef(false);
  // Once a layout settles and React Flow has measured the real (content-sized)
  // boxes, run exactly one refinement pass so spacing reflects true dimensions.
  const measuredRelayoutDoneRef = useRef(false);
  const loadedRunIdRef = useRef<string | undefined>(undefined);
  const backendLoadingRef = useRef(false);
  const reactFlow = useReactFlow<Node<DiagramNodeData>, Edge<DiagramEdgeData>>();
  const updateNodeInternals = useUpdateNodeInternals();
  const nodesInitialized = useNodesInitialized();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<DiagramNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<DiagramEdgeData>>([]);

  const requestLayout = useCallback(() => {
    layoutRequestedRef.current = true;
    hasFitInitialViewRef.current = false;
    measuredRelayoutDoneRef.current = false;
    setLayoutNonce((nonce) => nonce + 1);
  }, []);

  const model = useMemo(() => parseSysml(sourceText), [sourceText]);
  const selectedNodeId = selection.kind === 'node' ? selection.id : undefined;
  const selectedPort = useMemo(
    () => (selection.kind === 'port' ? { nodeId: selection.nodeId, portName: selection.portName } : undefined),
    [selection],
  );
  const movePort = useCallback((instanceName: string, portName: string, placement: PortPlacement) => {
    setPortPlacementOverrides((current) => ({
      ...current,
      [instanceName]: {
        ...(current[instanceName] ?? {}),
        [portName]: placement,
      },
    }));
    setLockedPortPlacementOverrides((current) => ({
      ...current,
      [instanceName]: {
        ...(current[instanceName] ?? {}),
        [portName]: true,
      },
    }));
  }, []);
  const selectPort = useCallback((instanceName: string, portName: string) => {
    setSelection({ kind: 'port', nodeId: instanceName, portName });
  }, []);
  const toggleNodeCompartment = useCallback(
    (instanceName: string, compartment: NodeCompartmentKey, open: boolean) => {
      setNodeCompartmentOverrides((current) => ({
        ...current,
        [instanceName]: {
          ...(current[instanceName] ?? {}),
          [compartment]: open,
        },
      }));
      requestLayout();
    },
    [requestLayout],
  );
  const setAllNodeCompartments = useCallback(
    (open: boolean) => {
      setNodeCompartmentDefaultOpen(open);
      setNodeCompartmentOverrides({});
      requestLayout();
    },
    [requestLayout],
  );
  const buildContext = useMemo<BuildContext>(
    () => ({
      query,
      filter,
      selectedNodeId,
      selectedPort,
      placementOverrides: portPlacementOverrides,
      lockedPlacementOverrides: lockedPortPlacementOverrides,
      positionOverrides: nodePositionOverrides,
      compartmentDefaultOpen: nodeCompartmentDefaultOpen,
      compartmentOverrides: nodeCompartmentOverrides,
      onPortMove: movePort,
      onPortSelect: selectPort,
      onCompartmentToggle: toggleNodeCompartment,
    }),
    [
      filter,
      lockedPortPlacementOverrides,
      movePort,
      nodeCompartmentDefaultOpen,
      nodeCompartmentOverrides,
      nodePositionOverrides,
      portPlacementOverrides,
      query,
      selectPort,
      selectedNodeId,
      selectedPort,
      toggleNodeCompartment,
    ],
  );

  const baseElements = useMemo(
    () =>
      view.path.length > 0
        ? buildExcerptElements(model, view.path, buildContext)
        : buildTopLevelElements(model, buildContext),
    [buildContext, model, view],
  );

  useEffect(() => {
    let disposed = false;

    if (isNodeDraggingRef.current) {
      return () => {
        disposed = true;
      };
    }

    if (!layoutRequestedRef.current) {
      setNodes((currentNodes) =>
        baseElements.nodes.map((node) => ({
          ...node,
          position: currentNodes.find((currentNode) => currentNode.id === node.id)?.position ?? node.position,
        })),
      );
      setEdges(baseElements.edges);
      window.requestAnimationFrame(() => {
        baseElements.nodes.forEach((node) => updateNodeInternals(node.id));
      });
      return () => {
        disposed = true;
      };
    }

    setLayoutBusy(true);

    // Feed the real, DOM-measured sizes (when available) into ELK so spacing
    // matches the content-sized boxes rather than the pre-render estimate.
    const measuredById = new Map(reactFlow.getNodes().map((node) => [node.id, node.measured]));
    const sizedNodes = baseElements.nodes.map((node) => ({ ...node, measured: measuredById.get(node.id) }));

    layoutElements(sizedNodes, baseElements.edges)
      .then((layoutedElements) => {
        if (disposed || isNodeDraggingRef.current) {
          return;
        }

        layoutRequestedRef.current = false;
        setPortPlacementOverrides(
          Object.fromEntries(
            layoutedElements.nodes.map((node) => [
              node.id,
              Object.fromEntries(
                Object.entries(node.data.portPlacements).map(([portName, placement]) => [portName, placement]),
              ),
            ]),
          ),
        );
        setNodes(layoutedElements.nodes);
        setEdges(layoutedElements.edges);
        window.requestAnimationFrame(() => {
          layoutedElements.nodes.forEach((node) => updateNodeInternals(node.id));
          if (!hasFitInitialViewRef.current) {
            hasFitInitialViewRef.current = true;
            reactFlow.fitView({ padding: 0.22, duration: 350 });
          }
        });
      })
      .finally(() => {
        if (!disposed) {
          setLayoutBusy(false);
        }
      });

    return () => {
      disposed = true;
    };
  }, [baseElements, layoutNonce, reactFlow, setEdges, setNodes, updateNodeInternals]);

  // After the first layout settles and the boxes have been measured, run one
  // refinement pass so ELK spacing reflects the real content sizes. Guarded so it
  // fires once per requested layout (reset wherever a layout is requested).
  useEffect(() => {
    if (!nodesInitialized || measuredRelayoutDoneRef.current || isNodeDraggingRef.current) {
      return;
    }
    measuredRelayoutDoneRef.current = true;
    layoutRequestedRef.current = true;
    setLayoutNonce((nonce) => nonce + 1);
  }, [nodesInitialized]);

  useEffect(() => {
    if (!import.meta.env.DEV) {
      return;
    }

    console.groupCollapsed(`[SysML edge pathing] ${baseElements.edges.length} visible edges`);
    console.table(
      baseElements.edges.map((edge) => ({
        edge: edge.id,
        source: edge.source,
        sourceHandle: edge.sourceHandle,
        target: edge.target,
        targetHandle: edge.targetHandle,
        highlighted: Boolean(edge.data?.highlighted),
        dimmed: Boolean(edge.data?.dimmed),
      })),
    );
    console.table(
      baseElements.nodes.flatMap((node) =>
        Object.entries(node.data.portPlacements).map(([port, placement]) => ({
          node: node.id,
          port,
          side: placement.side,
          offset: Number(placement.offset.toFixed(3)),
        })),
      ),
    );
    const stackedPorts = baseElements.nodes.flatMap((node) =>
      getStackedPortGroups(node.data.portPlacements).map((group) => ({
        node: node.id,
        side: group.side,
        ports: group.ports.join(', '),
      })),
    );
    if (stackedPorts.length > 0) {
      console.warn('[SysML port placement] Stacked ports detected after normalization.');
      console.table(stackedPorts);
    }
    console.groupEnd();
  }, [baseElements.edges, baseElements.nodes]);

  const selectedNode = nodes.find((node) => selection.kind === 'node' && node.id === selection.id);
  const selectedPortNode = nodes.find((node) => selection.kind === 'port' && node.id === selection.nodeId);
  const selectedEdge = edges.find((edge) => selection.kind === 'edge' && edge.id === selection.id);

  const resetViewState = useCallback(() => {
    setSelection({ kind: 'none' });
    setPortPlacementOverrides({});
    setLockedPortPlacementOverrides({});
    setNodePositionOverrides({});
    setNodeCompartmentOverrides({});
    requestLayout();
  }, [requestLayout]);

  const loadSource = useCallback(
    (text: string, name: string) => {
      setSourceText(text);
      setSourceName(name);
      setView({ path: [] });
      resetViewState();
    },
    [resetViewState],
  );

  const applyProjects = useCallback((nextProjects: Project[]) => {
    setProjects(nextProjects);
    setSelectedProjectSlug((current) => preferredProjectSlug(nextProjects, current));
  }, []);

  const applyProjectRuns = useCallback((runs: ProjectRun[]) => {
    setProjectRuns(runs);
  }, []);

  const refreshProjects = useCallback(async () => {
    const [projectsResponse, runsResponse] = await Promise.all([
      fetch(`${backendBaseUrl}/api/projects`),
      fetch(`${backendBaseUrl}/api/runs`),
    ]);
    if (!projectsResponse.ok) {
      throw new Error(`Backend returned ${projectsResponse.status} for /api/projects`);
    }
    if (!runsResponse.ok) {
      throw new Error(`Backend returned ${runsResponse.status} for /api/runs`);
    }
    const projectsPayload = (await projectsResponse.json()) as { projects?: Project[] };
    const runsPayload = (await runsResponse.json()) as { runs?: BackendRun[] };
    const nextProjects = projectsPayload.projects ?? [];
    const latestRun = pickLatestRun(runsPayload.runs ?? []);
    setProjects(nextProjects);
    setSelectedProjectSlug((current) => {
      return preferredProjectSlug(nextProjects, current, latestRun?.projectSlug);
    });
    if (latestRun) {
      setSelectedRunId((current) => current || runIdOf(latestRun));
    }
  }, []);

  const refreshProjectRuns = useCallback(async (slug: string) => {
    if (!slug) {
      setProjectRuns([]);
      setSelectedRunId('');
      return;
    }
    const response = await fetch(`${backendBaseUrl}/api/projects/${slug}/runs`);
    if (!response.ok) {
      throw new Error(`Backend returned ${response.status} for /api/projects/${slug}/runs`);
    }
    const payload = (await response.json()) as { runs?: ProjectRun[] };
    const runs = payload.runs ?? [];
    setProjectRuns(runs);
    setSelectedRunId((current) =>
      current && runs.some((run) => runIdOf(run) === current) ? current : runIdOf(runs[0]) || '',
    );
  }, []);

  const enterExcerpt = useCallback(
    (instance: string) => {
      setView((current) => ({ path: [...current.path, instance] }));
      resetViewState();
    },
    [resetViewState],
  );

  const backOneLevel = useCallback(() => {
    setView((current) => ({ path: current.path.slice(0, -1) }));
    resetViewState();
  }, [resetViewState]);

  const navigateToBreadcrumb = useCallback(
    (depth: number) => {
      setView((current) => ({ path: current.path.slice(0, depth) }));
      resetViewState();
    },
    [resetViewState],
  );

  const loadRunSnapshot = useCallback(
    async (run: BackendRun | ProjectRun) => {
      const latestArtifact = run.artifacts?.[run.artifacts.length - 1];
      const runId = run.runId ?? run.run_id;
      const passId = latestArtifact?.passId ?? latestArtifact?.pass_id;
      if (!runId || !passId) {
        throw new Error('No generated pass snapshot is available.');
      }
      const fallbackChangedCount = run.changedCount ?? run.changed_count ?? 0;
      const fallbackUnchangedCount = run.unchangedCount ?? run.unchanged_count ?? 0;
      setSelectedRunId(runId);
      setBackendLoadState({ status: 'loading', message: `Loading ${passId}` });
      try {
        const artifactResponse = await fetch(
          `${backendBaseUrl}/api/runs/${runId}/passes/${passId}/artifacts/suite-model.sysml`,
        );
        if (!artifactResponse.ok) {
          throw new Error(`Backend returned ${artifactResponse.status} for suite-model.sysml`);
        }

        const sysml = await artifactResponse.text();
        const [evidenceResponse, unresolvedResponse, changesResponse, eventsResponse] = await Promise.all([
          fetch(`${backendBaseUrl}/api/runs/${runId}/passes/${passId}/artifacts/suite-evidence.json`),
          fetch(`${backendBaseUrl}/api/runs/${runId}/passes/${passId}/artifacts/unresolved-services.md`),
          fetch(`${backendBaseUrl}/api/runs/${runId}/changes`),
          fetch(`${backendBaseUrl}/api/runs/${runId}/events`),
        ]);

        const evidence = evidenceResponse.ok ? ((await evidenceResponse.json()) as EvidencePayload) : undefined;
        const unresolved = unresolvedResponse.ok ? await unresolvedResponse.text() : '';
        const changes = changesResponse.ok ? ((await changesResponse.json()) as ChangesPayload) : undefined;
        const eventPayload = eventsResponse.ok ? ((await eventsResponse.json()) as { events?: RunEvent[] }) : undefined;
        loadSource(sysml, `${runId}/${passId}/suite-model.sysml`);
        setBackendReviewSummary({
          runId,
          passId,
          evidenceRecords: evidence?.records?.length ?? 0,
          unresolvedItems: countUnresolvedItems(unresolved),
          changedCount: changes?.changedCount ?? changes?.changed_count ?? fallbackChangedCount,
          unchangedCount: changes?.unchangedCount ?? changes?.unchanged_count ?? fallbackUnchangedCount,
          usage: formatRunUsage(run),
          validation: validationReviewFromEvents(eventPayload?.events ?? []),
        });
        setBackendLoadState({ status: 'loaded', message: `Loaded ${passId}` });
      } catch (error) {
        setBackendReviewSummary(undefined);
        setBackendLoadState({
          status: 'error',
          message: error instanceof Error ? error.message : 'Backend snapshot load failed',
        });
        throw error;
      }
    },
    [loadSource],
  );

  useEffect(() => {
    refreshProjects().catch(() => {});
  }, [refreshProjects]);

  useEffect(() => {
    refreshProjectRuns(selectedProjectSlug).catch(() => {
      setProjectRuns([]);
      setSelectedRunId('');
    });
  }, [refreshProjectRuns, selectedProjectSlug]);

  useEffect(() => {
    if (selectedProjectSlug) {
      writeLastProjectSlug(selectedProjectSlug);
    }
  }, [selectedProjectSlug]);

  const selectedRun = useMemo(
    () => projectRuns.find((run) => runIdOf(run) === selectedRunId),
    [projectRuns, selectedRunId],
  );
  const selectedProject = useMemo(
    () => projects.find((project) => project.slug === selectedProjectSlug),
    [projects, selectedProjectSlug],
  );

  useEffect(() => {
    const runId = runIdOf(selectedRun);
    if (!selectedRun || !runId || runId === loadedRunIdRef.current) {
      return;
    }
    if (!(selectedRun.artifacts ?? []).length) {
      return;
    }
    loadRunSnapshot(selectedRun).catch(() => {});
  }, [loadRunSnapshot, selectedRun]);

  // Keep refs in sync so the polling interval doesn't hold stale values
  useEffect(() => {
    loadedRunIdRef.current = backendReviewSummary?.runId;
  }, [backendReviewSummary?.runId]);

  useEffect(() => {
    backendLoadingRef.current = backendLoadState.status === 'loading';
  }, [backendLoadState.status]);

  useEffect(() => {
    const poll = async () => {
      if (backendLoadingRef.current || !selectedProjectSlug) return;
      try {
        const res = await fetch(`${backendBaseUrl}/api/projects/${selectedProjectSlug}/runs`);
        if (!res.ok) return;
        const payload = (await res.json()) as { runs?: ProjectRun[] };
        const runs = payload.runs ?? [];
        setProjectRuns(runs);
        const latest = pickLatestRun(runs);
        if (!latest) return;
        const latestId = latest.runId ?? latest.run_id;
        if (latestId && latestId !== loadedRunIdRef.current) {
          setSelectedRunId(latestId);
          await loadRunSnapshot(latest);
        }
      } catch {
        // silently ignore — backend may be unavailable
      }
    };
    const id = setInterval(poll, 30_000);
    return () => clearInterval(id);
  }, [loadRunSnapshot, selectedProjectSlug]);

  const resetLayout = useCallback(() => {
    setPortPlacementOverrides({});
    setLockedPortPlacementOverrides({});
    setNodePositionOverrides({});
    requestLayout();
  }, [requestLayout]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <span className="brand-mark">S2</span>
          <div>
            <h1>{viewTitle(viewMode)}</h1>
            <p>{viewSubtitle(viewMode, sourceName)}</p>
          </div>
        </div>
        <div className="toolbar">
          <button
            className={`tool-button ${viewMode === 'interconnections' ? 'active' : ''}`}
            onClick={() => setViewMode('interconnections')}
            type="button"
          >
            <span>Interconnections</span>
          </button>
          <button
            className={`tool-button ${viewMode === 'projects' ? 'active' : ''}`}
            onClick={() => setViewMode('projects')}
            type="button"
          >
            <FolderGit2 size={16} />
            <span>Projects</span>
          </button>
          <button
            className={`tool-button ${viewMode === 'code' ? 'active' : ''}`}
            onClick={() => {
              setViewMode('code');
            }}
            title="Show source code"
            type="button"
          >
            <Code2 size={16} />
            <span>Code</span>
          </button>
        </div>
      </header>

      <main className="workspace">
        {viewMode === 'interconnections' ? (
          <>
            <section className="diagram-region">
              <div className="diagram-status">
                {view.path.length > 0 ? (
                  <nav className="excerpt-breadcrumb" aria-label="Diagram breadcrumb">
                    <button type="button" onClick={backOneLevel} className="excerpt-breadcrumb__back" title="Back">
                      <ArrowLeft size={15} />
                      <span>Back</span>
                    </button>
                    <button type="button" onClick={() => navigateToBreadcrumb(0)} className="excerpt-breadcrumb__link">
                      System
                    </button>
                    {view.path.map((item, index) => (
                      <span className="excerpt-breadcrumb__item" key={`${item}-${index}`}>
                        <span className="excerpt-breadcrumb__sep">/</span>
                        {index === view.path.length - 1 ? (
                          <span className="excerpt-breadcrumb__current">{item}</span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => navigateToBreadcrumb(index + 1)}
                            className="excerpt-breadcrumb__link"
                          >
                            {item}
                          </button>
                        )}
                      </span>
                    ))}
                  </nav>
                ) : null}
                <div className="viewer-selectors">
                  <select
                    aria-label="Project"
                    disabled={!projects.length}
                    onChange={(event) => {
                      setSelectedProjectSlug(event.target.value);
                      setSelectedRunId('');
                      setProjectRuns([]);
                    }}
                    title="Project"
                    value={selectedProjectSlug}
                  >
                    {projects.length ? (
                      projects.map((project) => (
                        <option key={project.slug} value={project.slug}>
                          {project.name}
                        </option>
                      ))
                    ) : (
                      <option value="">No projects</option>
                    )}
                  </select>
                  <select
                    aria-label="Saved scan"
                    disabled={!projectRuns.length}
                    onChange={(event) => {
                      const runId = event.target.value;
                      const run = projectRuns.find((candidate) => runIdOf(candidate) === runId);
                      setSelectedRunId(runId);
                      if (run) {
                        loadRunSnapshot(run).catch(() => {});
                      }
                    }}
                    title="Saved scan"
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
                </div>
                <div className="search-box">
                  <Search size={16} />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search parts or ports"
                  />
                </div>
                <div className="filter-tabs" aria-label="Connection filters">
                  <Filter size={16} />
                  {(Object.keys(filterLabels) as EdgeFilter[]).map((filterKey) => (
                    <button
                      className={filter === filterKey ? 'active' : ''}
                      key={filterKey}
                      onClick={() => setFilter(filterKey)}
                      type="button"
                    >
                      {filterLabels[filterKey]}
                    </button>
                  ))}
                </div>
                <div className="graph-actions" aria-label="Graph actions">
                  <button title="Expand node details" onClick={() => setAllNodeCompartments(true)} type="button">
                    <ChevronDown size={16} />
                    <span>Expand</span>
                  </button>
                  <button title="Collapse node details" onClick={() => setAllNodeCompartments(false)} type="button">
                    <ChevronUp size={16} />
                    <span>Collapse</span>
                  </button>
                  <button
                    title="Fit diagram to view"
                    onClick={() => reactFlow.fitView({ padding: 0.22, duration: 300 })}
                    type="button"
                  >
                    <Maximize2 size={16} />
                  </button>
                  <button title="Reset diagram layout" onClick={resetLayout} type="button">
                    <RotateCcw size={16} />
                  </button>
                </div>
              </div>

              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeDragStart={() => {
                  isNodeDraggingRef.current = true;
                }}
                onNodeDragStop={(_, node) => {
                  isNodeDraggingRef.current = false;
                  setNodePositionOverrides((current) => ({
                    ...current,
                    [node.id]: node.position,
                  }));
                }}
                onNodeClick={(_, node) => setSelection({ kind: 'node', id: node.id })}
                onNodeDoubleClick={(_, node) => {
                  if (canDrillInto(node)) {
                    enterExcerpt(node.id);
                  }
                }}
                onEdgeClick={(_, edge) => setSelection({ kind: 'edge', id: edge.id })}
                onPaneClick={() => setSelection({ kind: 'none' })}
                minZoom={0.1}
                maxZoom={2}
                proOptions={{ hideAttribution: true }}
              >
                <Background color="#c7d2d9" gap={24} />
                <Controls position="bottom-left" />
                <MiniMap
                  position="bottom-right"
                  nodeColor={(node) => (node.className?.includes('is-highlighted') ? '#d16f4d' : '#5e7f86')}
                  maskColor="rgba(247, 249, 249, 0.78)"
                />
                {layoutBusy ? (
                  <Panel position="top-center" className="layout-badge">
                    Calculating layout
                  </Panel>
                ) : null}
                {backendReviewSummary?.validation?.warnings.length ? (
                  <Panel position="top-right" className="validation-review-panel">
                    <div className="validation-review-panel__header">
                      <AlertTriangle size={15} />
                      <strong>Review Notes</strong>
                    </div>
                    <p>{backendReviewSummary.validation.summary ?? 'Generated SysML needs review.'}</p>
                    <ul>
                      {backendReviewSummary.validation.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                    {backendReviewSummary.validation.metrics ? (
                      <small>{formatValidationMetrics(backendReviewSummary.validation.metrics)}</small>
                    ) : null}
                  </Panel>
                ) : null}
              </ReactFlow>
            </section>

            <DetailsPanel
              model={model}
              selectedNode={selectedNode}
              selectedEdge={selectedEdge}
              selectedPort={
                selection.kind === 'port' && selectedPortNode
                  ? { node: selectedPortNode, portName: selection.portName }
                  : undefined
              }
            />
          </>
        ) : viewMode === 'projects' ? (
          <ProjectOnboarding
            backendBaseUrl={backendBaseUrl}
            onLoadRunSnapshot={loadRunSnapshot}
            projects={projects}
            selectedProjectSlug={selectedProjectSlug}
            onSelectedProjectSlugChange={setSelectedProjectSlug}
            onProjectsChange={applyProjects}
            projectRuns={projectRuns}
            selectedRunId={selectedRunId}
            onSelectedRunIdChange={setSelectedRunId}
            onProjectRunsChange={applyProjectRuns}
          />
        ) : viewMode === 'code' ? (
          <CodeView interconnectionName={sourceName} interconnectionSource={sourceText} />
        ) : null}
      </main>

      <footer className="statusbar">
        {viewMode === 'interconnections' ? (
          <>
            <div>
              <FileUp size={14} />
              <span>
                {selectedProject?.name ?? model.selectedSystem.name}
                {view.path.length > 0 ? ` / ${view.path.join(' / ')}` : ''}
              </span>
            </div>
            {selectedRun ? <div>scan {shortId(runIdOf(selectedRun))}</div> : null}
            <div>{nodes.length} parts</div>
            <div>{edges.length} visible connections</div>
            <div>{model.selectedSystem.connections.length} total connections</div>
            {backendReviewSummary ? (
              <>
                <div>{backendReviewSummary.evidenceRecords} evidence records</div>
                <div>{backendReviewSummary.unresolvedItems} unresolved</div>
                <div>
                  {backendReviewSummary.changedCount} changed / {backendReviewSummary.unchangedCount} unchanged
                </div>
                {backendReviewSummary.usage ? <div>{backendReviewSummary.usage}</div> : null}
              </>
            ) : null}
            <div className={`backend-status backend-status--${backendLoadState.status}`}>
              <Server size={14} />
              <span>{backendLoadState.message}</span>
            </div>
          </>
        ) : viewMode === 'projects' ? (
          <>
            <div>
              <FolderGit2 size={14} />
              <span>Project onboarding</span>
            </div>
            <div>repository import</div>
            <div>scan management</div>
          </>
        ) : (
          <>
            <div>
              <Code2 size={14} />
              <span>{sourceName}</span>
            </div>
            <div>readable source</div>
            <div>line-numbered SysML</div>
          </>
        )}
      </footer>
    </div>
  );
}

function viewTitle(viewMode: ViewMode) {
  if (viewMode === 'projects') {
    return 'Project Onboarding';
  }
  if (viewMode === 'code') {
    return 'SysML Source Viewer';
  }

  return 'SysML Interconnection Viewer';
}

function viewSubtitle(viewMode: ViewMode, sourceName: string) {
  if (viewMode === 'projects') {
    return 'create, import, monitor';
  }
  if (viewMode === 'code') {
    return 'readable SysML source';
  }

  return sourceName;
}

function runIdOf(run?: BackendRun | ProjectRun): string {
  return run?.runId ?? run?.run_id ?? '';
}

function shortId(value?: string): string {
  return value ? value.slice(0, 8) : 'unknown';
}

function formatRunOption(run: BackendRun | ProjectRun, index: number, total: number): string {
  const startedAt = run.startedAt ?? run.started_at;
  const version = Math.max(1, total - index);
  const label = index === 0 ? `v${version} latest` : `v${version}`;
  const date = startedAt ? new Date(startedAt).toLocaleString() : 'unknown date';
  const status = formatRunOutcome(run.status);
  const usage = formatRunUsage(run);
  return usage ? `${label} - ${date} - ${status} - ${usage}` : `${label} - ${date} - ${status}`;
}

function countUnresolvedItems(markdown: string) {
  return markdown.split('\n').filter((line) => line.trim().startsWith('- ')).length;
}

function canDrillInto(node: Node<DiagramNodeData>): boolean {
  if (node.data.boundary) {
    return false;
  }
  if (node.data.overview) {
    return Boolean(node.data.hasSubparts);
  }
  return (node.data.instance.definition?.parts.length ?? 0) > 0;
}

function formatRunUsage(run: BackendRun | ProjectRun): string {
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
