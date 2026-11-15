import type {
  AnalysisRequest,
  CsvTable,
  HarmonizationOverview,
  HarmonizedSeries,
  JobResponse,
  ModelStatus,
  ResultEnvelope,
  SourcesResponse,
} from './types';

/** Base URL of the API. Empty = same origin (Vite dev/preview proxy forwards /api to FastAPI). */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? '';

export class ApiError extends Error {
  readonly status: number;
  readonly recoverable: boolean;
  readonly code: string | null;
  readonly retryAfter: number | null;
  readonly fieldErrors: unknown;

  constructor(
    status: number,
    detail: string,
    opts: { recoverable?: boolean; code?: string | null; retryAfter?: number | null; fieldErrors?: unknown } = {},
  ) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.recoverable = opts.recoverable ?? false;
    this.code = opts.code ?? null;
    this.retryAfter = opts.retryAfter ?? null;
    this.fieldErrors = opts.fieldErrors;
  }
}

function describeNetworkFailure(err: unknown): ApiError {
  const message =
    err instanceof Error && err.name === 'AbortError'
      ? 'The request was cancelled.'
      : 'The FireHarmonics backend could not be reached. Check that the API server is running.';
  return new ApiError(0, message, { recoverable: true, code: 'network' });
}

async function parseError(res: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {};
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    /* non-JSON error body (e.g. proxy error page) */
  }
  const nested = (body.error ?? null) as Record<string, unknown> | null;
  const detailRaw = nested?.detail ?? body.detail;
  let detail = typeof detailRaw === 'string' ? detailRaw : '';
  if (!detail && Array.isArray(detailRaw)) detail = 'The request was rejected by the API (validation error).';
  if (!detail) {
    detail =
      res.status === 502 || res.status === 504 || res.status === 503
        ? 'The FireHarmonics backend is unreachable or timed out.'
        : `Request failed with HTTP ${res.status}.`;
  }
  const retryAfterHeader = res.headers.get('Retry-After');
  return new ApiError(res.status, detail, {
    recoverable: Boolean(nested?.recoverable ?? body.recoverable ?? false),
    code: (nested?.code as string | undefined) ?? null,
    retryAfter: retryAfterHeader ? Number(retryAfterHeader) : null,
    fieldErrors: body.errors,
  });
}

async function request<T>(path: string, init: RequestInit = {}): Promise<{ data: T; res: Response }> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
    });
  } catch (err) {
    throw describeNetworkFailure(err);
  }
  if (!res.ok && res.status !== 202) throw await parseError(res);
  const data = (await res.json()) as T;
  return { data, res };
}

export const api = {
  health: (signal?: AbortSignal) => request<{ status: string }>('/health', { signal }).then((r) => r.data),
  model: (signal?: AbortSignal) => request<ModelStatus>('/api/v1/model', { signal }).then((r) => r.data),
  sources: (signal?: AbortSignal) => request<SourcesResponse>('/api/v1/sources', { signal }).then((r) => r.data),
  harmonization: (signal?: AbortSignal) =>
    request<HarmonizationOverview>('/api/v1/harmonization', { signal }).then((r) => r.data),
  /** `seriesUrl` comes from the API itself (satellite_analysis.views.harmonized.series_url). */
  harmonizedSeries: (seriesUrl: string, signal?: AbortSignal) => {
    if (!seriesUrl.startsWith('/api/v1/harmonization/regions/')) {
      return Promise.reject(new ApiError(0, 'Unexpected harmonized series URL from the API.'));
    }
    return request<HarmonizedSeries>(seriesUrl, { signal }).then((r) => r.data);
  },
  startAnalysis: (body: AnalysisRequest, signal?: AbortSignal) =>
    request<JobResponse>('/api/v1/analyses', { method: 'POST', body: JSON.stringify(body), signal }).then((r) => r.data),
  job: (jobId: string, signal?: AbortSignal) =>
    request<JobResponse>(`/api/v1/analyses/${encodeURIComponent(jobId)}`, { signal }).then((r) => r.data),
  /** Returns null while the job is still running (HTTP 202). Throws ApiError(409) for failed jobs. */
  result: async (jobId: string, signal?: AbortSignal) => {
    const { data, res } = await request<ResultEnvelope | { status: string; result: null }>(
      `/api/v1/analyses/${encodeURIComponent(jobId)}/result`,
      { signal },
    );
    if (res.status === 202 || !('result' in data) || data.result === null) return null;
    return data as ResultEnvelope;
  },
  /** Cancel a queued/running job (backend stops it at its next checkpoint). Errors are ignored by callers. */
  cancel: (jobId: string, ownerToken: string) =>
    request<JobResponse>(`/api/v1/analyses/${encodeURIComponent(jobId)}/cancel`, {
      method: 'POST',
      headers: { 'X-Job-Token': ownerToken },
    }).then((r) => r.data),
  /** Only the client that started the job holds its owner token; the backend answers 403 to anyone else. */
  retry: (jobId: string, ownerToken: string, signal?: AbortSignal) =>
    request<JobResponse>(`/api/v1/analyses/${encodeURIComponent(jobId)}/retry`, {
      method: 'POST',
      headers: { 'X-Job-Token': ownerToken },
      signal,
    }).then((r) => r.data),
  eventsUrl: (jobId: string) => `${API_BASE}/api/v1/analyses/${encodeURIComponent(jobId)}/events`,
  exportUrl: (jobId: string, format: 'json' | 'csv', table?: CsvTable) => {
    const params = new URLSearchParams({ format });
    if (format === 'csv' && table) params.set('table', table);
    return `${API_BASE}/api/v1/analyses/${encodeURIComponent(jobId)}/export?${params.toString()}`;
  },
};

/** Downloads a backend export as a file. Surfaces 409 (table unavailable / unfinished job) as ApiError. */
export async function downloadExport(jobId: string, format: 'json' | 'csv', table?: CsvTable): Promise<string> {
  let res: Response;
  try {
    res = await fetch(api.exportUrl(jobId, format, table));
  } catch (err) {
    throw describeNetworkFailure(err);
  }
  if (!res.ok) throw await parseError(res);
  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition') ?? '';
  const match = /filename="([^"]+)"/.exec(disposition);
  const filename = match?.[1] ?? `fireharmonics-${jobId}${table ? `-${table}` : ''}.${format}`;
  saveBlob(blob, filename);
  return filename;
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
