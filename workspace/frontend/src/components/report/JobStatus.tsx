import { RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { AnalysisJobState } from '../../api/useAnalysisJob';
import { fmtDuration } from '../../lib/format';
import { Notice } from '../ui/primitives';

interface Props {
  state: AnalysisJobState;
  onRetry: () => void;
}

/** Live job progress (SSE or polling), failures and transport errors — all from the real API. */
export default function JobStatus({ state, onRetry }: Props) {
  const { phase, jobError, apiError } = state;

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
          apiError.recoverable ||
          apiError.status === 0 ||
          apiError.status === 429 ||
          apiError.status >= 500 ? (
            <button type="button" className="button button--small" onClick={onRetry}>
              <RefreshCw aria-hidden="true" size={14} /> Try again
            </button>
          ) : undefined
        }
      >
        <p>{apiError.message}</p>
        {apiError.status > 0 && (
          <p className="muted">
            HTTP {apiError.status}
            {apiError.retryAfter ? ` · retry after ${apiError.retryAfter} s` : ''}
          </p>
        )}
        {unconfigured && (
          <p className="muted">
            No analysis was run, so no statistics or forecast are shown. The operator must set the NASA FIRMS
            map key on the server.
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
  return <RunTrack state={state} />;
}

/** The analysis pipeline, as the user would describe it. Backend stage names map onto these steps. */
const STEPS: { id: string; label: string; stages: string[] }[] = [
  {
    id: 'recent',
    label: 'Recent detections from NASA FIRMS',
    stages: [
      'queued',
      'checking_availability',
      'downloading',
      'waiting_for_nasa',
      'parsing',
      'analyzing',
      'loading_archive',
    ],
  },
  {
    id: 'history',
    label: '10-year satellite history',
    stages: [
      'ml_starting',
      'ml_validate',
      'ml_plan',
      'ml_wait_download_slot',
      'ml_download',
      'ml_process',
      'ml_load',
    ],
  },
  { id: 'patterns', label: 'Patterns and the MODIS + VIIRS calendar', stages: ['ml_patterns'] },
  { id: 'forecast', label: 'Forecast', stages: ['ml_predict', 'ml_finalize'] },
  { id: 'report', label: 'Report', stages: ['assembling', 'completed'] },
];

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function RunTrack({ state }: { state: AnalysisJobState }) {
  const { phase, job, apiError } = state;
  const stage =
    phase === 'submitting' ? 'queued' : phase === 'loading_result' ? 'assembling' : (job?.stage ?? 'queued');
  const current = Math.max(
    0,
    STEPS.findIndex((s) => s.stages.includes(stage)),
  );
  // Step timings live in state and are updated from a timer (an external clock), never computed during render.
  const [t, setT] = useState(() => {
    const at = Date.now();
    return {
      now: at,
      started: at,
      cur: current,
      start: { [current]: at } as Record<number, number>,
      end: {} as Record<number, number>,
    };
  });
  const curRef = useRef(current);
  useEffect(() => {
    curRef.current = current;
  }, [current]);
  useEffect(() => {
    const tick = () =>
      setT((prev) => {
        const at = Date.now();
        const c = curRef.current;
        if (c === prev.cur) return { ...prev, now: at };
        const start = { ...prev.start };
        const end = { ...prev.end };
        for (let i = prev.cur; i < c; i++) {
          start[i] ??= at;
          end[i] ??= at;
        }
        start[c] ??= at;
        return { ...prev, now: at, cur: c, start, end };
      });
    const id = setInterval(tick, 500);
    return () => clearInterval(id);
  }, []);
  const paused = job?.status === 'paused';
  const queued = stage === 'queued' && phase !== 'submitting';
  return (
    <div className="run" aria-live="polite" data-testid="job-status">
      <div className="run__head">
        <p className="run__title">
          {queued ? 'Waiting to start' : paused ? 'Paused by NASA rate limit' : 'Analysing'}
        </p>
        <p className="run__clock" aria-label="Elapsed time">
          {clock((t.now - t.started) / 1000)}
        </p>
      </div>
      <ol className="run__steps">
        {STEPS.map((s, i) => {
          const st = i < current ? 'done' : i === current ? 'active' : 'todo';
          const a = t.start[i];
          const e = t.end[i] ?? (st === 'active' ? t.now : undefined);
          return (
            <li
              key={s.id}
              className={`run__step run__step--${st}`}
              aria-current={st === 'active' ? 'step' : undefined}
            >
              <span className="run__node" aria-hidden="true" />
              <span className="run__label">{s.label}</span>
              <span className="run__time">{a && e ? clock((e - a) / 1000) : ''}</span>
              {st === 'active' && job?.message && <span className="run__detail">{job.message}</span>}
            </li>
          );
        })}
      </ol>
      {job?.nasa_pause_seconds != null && (
        <p className="run__note">
          NASA asked us to wait {fmtDuration(job.nasa_pause_seconds)} before the next request.
        </p>
      )}
      {apiError && phase === 'running' && (
        <p className="run__note run__note--warn">Connection lost: {apiError.message} Retrying.</p>
      )}
      {job && !job.cache_hit && current < 2 && <p className="run__hint">Usually takes less than a minute.</p>}
    </div>
  );
}
