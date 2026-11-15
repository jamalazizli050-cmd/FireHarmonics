import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './client';
import type { AnalysisRequest, AnalysisResult, JobError, JobResponse } from './types';

export type JobPhase = 'idle' | 'submitting' | 'running' | 'loading_result' | 'completed' | 'failed' | 'error';

export interface AnalysisJobState {
  phase: JobPhase;
  job: JobResponse | null;
  result: AnalysisResult | null;
  /** Error of a failed job (HTTP 409 on /result, or `error` in the job record). */
  jobError: JobError | null;
  /** Transport / API error (could not submit, backend unreachable, 422, 429, 500 ...). */
  apiError: ApiError | null;
  transport: 'sse' | 'polling' | null;
  request: AnalysisRequest | null;
}

const INITIAL: AnalysisJobState = {
  phase: 'idle',
  job: null,
  result: null,
  jobError: null,
  apiError: null,
  transport: null,
  request: null,
};

const POLL_MS = 1500;
const TERMINAL = new Set(['completed', 'failed']);

/**
 * Drives one analysis job: POST -> live progress over SSE (polling fallback) -> result.
 * A new `start` cancels the previous job's subscription (the backend job keeps running server side).
 */
export function useAnalysisJob() {
  const [state, setState] = useState<AnalysisJobState>(INITIAL);
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  const runRef = useRef(0);
  const cleanupRef = useRef<() => void>(() => {});

  const stopWatching = useCallback(() => {
    cleanupRef.current();
    cleanupRef.current = () => {};
  }, []);

  useEffect(() => stopWatching, [stopWatching]);

  const fetchResult = useCallback(async (run: number, jobId: string) => {
    setState((s) => (run === runRef.current ? { ...s, phase: 'loading_result' } : s));
    for (let attempt = 0; attempt < 30; attempt++) {
      try {
        const envelope = await api.result(jobId);
        if (run !== runRef.current) return;
        if (envelope) {
          setState((s) => ({ ...s, phase: 'completed', result: envelope.result }));
          return;
        }
        await new Promise((r) => setTimeout(r, 2000));
      } catch (err) {
        if (run !== runRef.current) return;
        if (err instanceof ApiError && err.status === 409) {
          setState((s) => ({
            ...s,
            phase: 'failed',
            jobError: { detail: err.message, recoverable: err.recoverable, code: err.code, status_code: 409 },
          }));
        } else {
          setState((s) => ({ ...s, phase: 'error', apiError: err as ApiError }));
        }
        return;
      }
    }
  }, []);

  const handleSnapshot = useCallback(
    (run: number, snapshot: JobResponse) => {
      if (run !== runRef.current) return;
      setState((s) => ({ ...s, job: { ...s.job, ...snapshot, links: snapshot.links ?? s.job?.links } as JobResponse }));
      if (snapshot.status === 'completed') {
        stopWatching();
        void fetchResult(run, snapshot.job_id);
      } else if (snapshot.status === 'failed') {
        stopWatching();
        setState((s) => ({
          ...s,
          phase: 'failed',
          jobError: snapshot.error ?? { detail: 'The analysis failed without an error description.' },
        }));
      }
    },
    [fetchResult, stopWatching],
  );

  const startPolling = useCallback(
    (run: number, jobId: string) => {
      setState((s) => (run === runRef.current ? { ...s, transport: 'polling' } : s));
      let timer: ReturnType<typeof setTimeout> | undefined;
      let stopped = false;
      const controller = new AbortController();
      const tick = async () => {
        try {
          const snapshot = await api.job(jobId, controller.signal);
          if (stopped) return;
          handleSnapshot(run, snapshot);
          if (!TERMINAL.has(snapshot.status)) timer = setTimeout(tick, POLL_MS);
        } catch (err) {
          if (stopped) return;
          // transient network problem: keep polling a bit slower, surface the error
          setState((s) => (run === runRef.current ? { ...s, apiError: err as ApiError } : s));
          timer = setTimeout(tick, POLL_MS * 3);
        }
      };
      void tick();
      cleanupRef.current = () => {
        stopped = true;
        controller.abort();
        if (timer) clearTimeout(timer);
      };
    },
    [handleSnapshot],
  );

  const watch = useCallback(
    (run: number, job: JobResponse) => {
      stopWatching();
      if (job.status === 'completed') {
        void fetchResult(run, job.job_id);
        return;
      }
      if (job.status === 'failed') {
        handleSnapshot(run, job);
        return;
      }
      if (typeof EventSource === 'undefined') {
        startPolling(run, job.job_id);
        return;
      }
      setState((s) => ({ ...s, transport: 'sse' }));
      const source = new EventSource(api.eventsUrl(job.job_id));
      let terminalSeen = false;
      const onData = (ev: MessageEvent<string>) => {
        if (ev.type === 'completed' || ev.type === 'failed') terminalSeen = true;
        try {
          handleSnapshot(run, JSON.parse(ev.data) as JobResponse);
        } catch {
          /* ignore malformed event */
        }
      };
      source.addEventListener('progress', onData as EventListener);
      source.addEventListener('completed', onData as EventListener);
      source.addEventListener('failed', onData as EventListener);
      source.onerror = () => {
        // The stream closes normally after the terminal event; otherwise fall back to polling.
        source.close();
        if (run !== runRef.current || terminalSeen) return;
        const current = stateRef.current.job;
        if (current && TERMINAL.has(current.status)) return;
        startPolling(run, job.job_id);
      };
      cleanupRef.current = () => source.close();
    },
    [fetchResult, handleSnapshot, startPolling, stopWatching],
  );

  /** The previous job is superseded: cancel it on the server so it does not occupy a worker. */
  const cancelPrevious = useCallback(() => {
    const prev = stateRef.current.job;
    if (prev && !TERMINAL.has(prev.status)) void api.cancel(prev.job_id).catch(() => undefined);
  }, []);

  const start = useCallback(
    async (body: AnalysisRequest) => {
      stopWatching();
      cancelPrevious();
      const run = ++runRef.current;
      setState({ ...INITIAL, phase: 'submitting', request: body });
      try {
        const job = await api.startAnalysis(body);
        if (run !== runRef.current) return;
        setState((s) => ({ ...s, phase: 'running', job }));
        watch(run, job);
      } catch (err) {
        if (run !== runRef.current) return;
        setState((s) => ({ ...s, phase: 'error', apiError: err as ApiError }));
      }
    },
    [stopWatching, watch, cancelPrevious],
  );

  const retry = useCallback(async () => {
    const { job, request } = stateRef.current;
    if (job && stateRef.current.phase === 'failed' && stateRef.current.jobError?.recoverable) {
      stopWatching();
      const run = ++runRef.current;
      setState((s) => ({ ...s, phase: 'submitting', jobError: null, apiError: null, result: null }));
      try {
        const next = await api.retry(job.job_id);
        if (run !== runRef.current) return;
        setState((s) => ({ ...s, phase: 'running', job: next }));
        watch(run, next);
      } catch (err) {
        if (run !== runRef.current) return;
        setState((s) => ({ ...s, phase: 'error', apiError: err as ApiError }));
      }
      return;
    }
    // Submission/transport errors (and non-recoverable jobs) are retried by posting the request again.
    if (request) await start(request);
  }, [start, stopWatching, watch]);

  const reset = useCallback(() => {
    stopWatching();
    runRef.current++;
    setState(INITIAL);
  }, [stopWatching]);

  return { ...state, start, retry, reset };
}
