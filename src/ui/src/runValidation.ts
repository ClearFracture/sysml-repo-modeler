export type RunEventLike = {
  phase?: string;
  level?: string;
  message?: string;
  reasoningSummary?: string | null;
  reasoning_summary?: string | null;
};

export type ValidationReview = {
  status?: string;
  summary?: string;
  warnings: string[];
  metrics?: Record<string, number>;
};

export type ProjectRunLike = {
  runId?: string;
  run_id?: string;
  status?: string;
  startedAt?: string;
  started_at?: string;
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
};

type RawValidationReview = {
  status?: unknown;
  summary?: unknown;
  warnings?: unknown;
  metrics?: unknown;
};

// A run is "active" from the moment it is queued until the event stream reports
// it finished. Active runs exist only in the browser: the backend persists a run
// once the cycle completes, so the dropdown entry is synthesized client-side.
export function isActiveRunStatus(status?: string): boolean {
  return status === 'running' || status === 'queued';
}

export function formatRunOutcome(status?: string): string {
  if (status === 'needs_attention') return 'Complete, review notes';
  if (status === 'completed') return 'Complete';
  if (status === 'failed') return 'Failed';
  if (isActiveRunStatus(status)) return 'Running';
  return status?.replace(/_/g, ' ') || 'Unknown';
}

export function runIdOfRun(run?: ProjectRunLike): string {
  return run?.runId ?? run?.run_id ?? '';
}

export function mergeActiveProjectRun<T extends ProjectRunLike>(
  runs: T[],
  activeRun: T | undefined,
): T[] {
  if (!activeRun) {
    return runs;
  }
  const activeId = runIdOfRun(activeRun);
  if (!activeId || runs.some((run) => runIdOfRun(run) === activeId)) {
    return runs;
  }
  return [activeRun, ...runs];
}

export function formatRunOptionLabel(run: ProjectRunLike, runs: ProjectRunLike[]): string {
  const startedAt = run.startedAt ?? run.started_at;
  const date = startedAt ? new Date(startedAt).toLocaleString() : 'unknown date';
  const status = formatRunOutcome(run.status);
  if (isActiveRunStatus(run.status)) {
    return `In progress - ${date} - ${status}`;
  }
  const savedRuns = runs.filter((candidate) => !isActiveRunStatus(candidate.status));
  const savedIndex = Math.max(0, savedRuns.indexOf(run));
  const version = Math.max(1, savedRuns.length - savedIndex);
  const label = savedIndex === 0 ? `v${version} latest` : `v${version}`;
  const session = run.opencodeSessionId ?? run.opencode_session_id;
  const parts = [`${label} - ${date} - ${status}`];
  const usage = formatRunUsageLabel(run);
  if (usage) parts.push(usage);
  if (session) parts.push(`OC ${shortRunId(session)}`);
  return parts.join(' - ');
}

function formatRunUsageLabel(run: ProjectRunLike): string {
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

function shortRunId(value?: string): string {
  return value ? value.slice(0, 8) : 'unknown';
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

export function validationReviewFromEvents(events: RunEventLike[]): ValidationReview | undefined {
  const validationEvents = events.filter((event) => event.phase === 'validation');
  for (const event of validationEvents.reverse()) {
    const review = validationReviewFromEvent(event);
    if (review) return review;
  }
  return undefined;
}

export function validationReviewFromEvent(event: RunEventLike): ValidationReview | undefined {
  const raw = event.reasoningSummary ?? event.reasoning_summary;
  if (!raw) return undefined;
  try {
    const parsed = JSON.parse(raw) as RawValidationReview;
    const warnings = Array.isArray(parsed.warnings)
      ? parsed.warnings.filter((warning): warning is string => typeof warning === 'string' && warning.trim().length > 0)
      : [];
    const metrics = parseMetrics(parsed.metrics);
    return {
      status: typeof parsed.status === 'string' ? parsed.status : undefined,
      summary: typeof parsed.summary === 'string' ? parsed.summary : event.message,
      warnings,
      metrics,
    };
  } catch {
    return undefined;
  }
}

function parseMetrics(value: unknown): Record<string, number> | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const metrics: Record<string, number> = {};
  for (const [key, metric] of Object.entries(value)) {
    if (typeof metric === 'number' && Number.isFinite(metric)) {
      metrics[key] = metric;
    }
  }
  return Object.keys(metrics).length ? metrics : undefined;
}
