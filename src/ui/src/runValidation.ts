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

type RawValidationReview = {
  status?: unknown;
  summary?: unknown;
  warnings?: unknown;
  metrics?: unknown;
};

export function formatRunOutcome(status?: string): string {
  if (status === 'needs_attention') return 'Complete, review notes';
  if (status === 'completed') return 'Complete';
  if (status === 'failed') return 'Failed';
  return status?.replace(/_/g, ' ') || 'Unknown';
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
