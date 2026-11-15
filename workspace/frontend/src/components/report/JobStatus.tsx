import { Loader2, RefreshCw } from 'lucide-react';
import type { AnalysisJobState } from '../../api/useAnalysisJob';
import { fmtDuration, stageLabel } from '../../lib/format';
import { Notice, ProgressBar } from '../ui/primitives';

interface Props {
  state: AnalysisJobState;
  onRetry: () => void;
}

/** Live job progress (SSE or polling), failures and transport errors — all from the real API. */
export default function JobStatus({ state, onRetry }: Props) {
  const { phase, job, jobError, apiError, transport } = state;

  if (phase === 'idle') return null;

  if (phase === 'error' && apiError) {
    const unconfigured = /NASA_FIRMS_MAP_KEY/i.test(apiError.message);
    return (
      <Notice
        tone="error"
        role="alert"
        title={
          apiError.status === 0
            ? 'Backend unreachable'
            : apiError.status === 429
              ? 'The analysis queue is busy'
              : apiError.status === 422
                ? 'The request was rejected'
                : unconfigured
                  ? 'Backend is not configured for NASA downloads'
                  : 'The analysis could not be started'
        }
        action={
          apiError.recoverable || apiError.status === 0 || apiError.status === 429 || apiError.status >= 500 ? (
            <button type="button" className="button button--small" onClick={onRetry}>
              <RefreshCw aria-hidden="true" size={14} /> Try again
            </button>
          ) : undefined
        }
      >
        <p>{apiError.message}</p>
        {apiError.status > 0 && <p className="muted">HTTP {apiError.status}{apiError.retryAfter ? ` · retry after ${apiError.retryAfter} s` : ''}</p>}
        {unconfigured && (
          <p className="muted">
            No analysis was run, so no statistics or forecast are shown. The operator must set the NASA FIRMS map
            key on the server.
          </p>
        )}
      </Notice>
    );
  }

  if (phase === 'failed') {
    return (
      <Notice
        tone="error"
        role="alert"
        title="The analysis failed"
        action={
          jobError?.recoverable ? (
            <button type="button" className="button button--small" onClick={onRetry}>
              <RefreshCw aria-hidden="true" size={14} /> Retry analysis
            </button>
          ) : undefined
        }
      >
        <p>{jobError?.detail ?? 'No error description was returned.'}</p>
        {jobError?.code && <p className="muted">Error code: {jobError.code}</p>}
      </Notice>
    );
  }

  if (phase === 'completed') return null;

  const progress = job?.progress ?? 0;
  const paused = job?.status === 'paused';
  return (
    <div className="job-status" aria-live="polite" data-testid="job-status">
      <div className="job-status__row">
        <Loader2 aria-hidden="true" size={16} className="spin" />
        <span className="job-status__stage">
          {phase === 'submitting'
            ? 'Submitting analysis…'
            : phase === 'loading_result'
              ? 'Loading result…'
              : paused
                ? 'Paused — waiting for NASA rate limit'
                : stageLabel(job?.stage)}
        </span>
        <span className="job-status__pct">{phase === 'submitting' ? '' : `${progress} %`}</span>
      </div>
      <ProgressBar value={phase === 'submitting' ? 0 : progress} label="Analysis progress" />
      {job?.message && <p className="job-status__message">{job.message}</p>}
      <p className="job-status__meta">
        {job?.nasa_pause_seconds != null && <span>Waiting for NASA ({fmtDuration(job.nasa_pause_seconds)}) · </span>}
        {job?.eta_seconds != null && <span>About {fmtDuration(job.eta_seconds)} remaining · </span>}
        {transport && <span>Live updates via {transport === 'sse' ? 'server-sent events' : 'polling'}</span>}
      </p>
      {apiError && phase === 'running' && (
        <p className="job-status__meta job-status__meta--warn">Connection problem: {apiError.message} Still trying…</p>
      )}
      {job && !job.cache_hit && progress < 50 && (
        <p className="job-status__hint">
          A new location can take 20–30 minutes, mostly NASA downloads. Previously analysed locations load in seconds.
        </p>
      )}
    </div>
  );
}
