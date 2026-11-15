import { useQuery } from '@tanstack/react-query';
import { Maximize2, Satellite } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { api } from '../api/client';
import { FIRMS_SOURCES, type FirmsSource } from '../api/types';
import { useAnalysisJob } from '../api/useAnalysisJob';
import JobStatus from '../components/report/JobStatus';
import PanelSummary from '../components/report/PanelSummary';
import DataControls, { type AnalysisSettings, type HarmonizedAvailability } from '../components/ui/DataControls';
import LocationSearch from '../components/ui/LocationSearch';
import { Notice } from '../components/ui/primitives';
import { formatCoordinates } from '../lib/geocode';
import { defaultPeriod } from '../lib/period';
import { ANALYSIS_RADIUS_KM, locationSearch, parseLocationParams } from '../state/location';

const ReportOverlay = lazy(() => import('../components/report/ReportOverlay'));

/** One-line summary of the API's relative activity index (values are read, not computed). */
function NormalizedNote({ result }: { result: NonNullable<ReturnType<typeof useAnalysisJob>['result']> }) {
  const series = result.satellite_analysis?.views.normalized?.series.find((x) => x.status === 'available');
  if (!series) return null;
  const peak = series.monthly.reduce<(typeof series.monthly)[number] | null>(
    (best, m) => (m.index !== null && (best === null || (best.index ?? -1) < m.index) ? m : best),
    null,
  );
  return (
    <p className="normalized-note" data-testid="normalized-note">
      <strong>Normalized index</strong> (not harmonized, not validated): busiest month{' '}
      {peak ? `${peak.month} at ${peak.index?.toFixed(2)}× the average month` : 'not available'} ({series.instrument},{' '}
      {series.reference_period.start} → {series.reference_period.end}). The full chart is in the report.
    </p>
  );
}

export default function GlobePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const location = parseLocationParams(params);
  const locationKey = location ? `${location.latitude.toFixed(5)},${location.longitude.toFixed(5)}` : null;

  const sourcesQ = useQuery({ queryKey: ['sources'], queryFn: ({ signal }) => api.sources(signal) });
  const modelQ = useQuery({ queryKey: ['model'], queryFn: ({ signal }) => api.model(signal) });
  const harmonizationQ = useQuery({ queryKey: ['harmonization'], queryFn: ({ signal }) => api.harmonization(signal) });

  const [settings, setSettings] = useState<AnalysisSettings>(() => ({
    source: 'VIIRS_NOAA20_NRT',
    view: 'raw',
    period: defaultPeriod(),
    radiusKm: ANALYSIS_RADIUS_KM,
  }));
  const [reportOpen, setReportOpen] = useState(false);
  const reportTrigger = useRef<HTMLButtonElement>(null);
  const job = useAnalysisJob();
  const mlDoneFor = useRef<string | null>(null);

  // Adopt the backend's default source once known (render-time adjustment, before the first analysis starts).
  const [adoptedDefault, setAdoptedDefault] = useState(false);
  if (!adoptedDefault && sourcesQ.data?.default) {
    setAdoptedDefault(true);
    if (sourcesQ.data.default !== settings.source) setSettings((s) => ({ ...s, source: sourcesQ.data!.default }));
  }

  const sources: readonly FirmsSource[] = sourcesQ.data?.sources?.length ? sourcesQ.data.sources : FIRMS_SOURCES;

  // Start a real analysis whenever the location, source or period changes.
  const { start } = job;
  useEffect(() => {
    if (!location || !sourcesQ.isFetched) return;
    // First run at a location downloads the ML history; later filter changes reuse the ML cache.
    // 'cache_only' only once a finished analysis has stored this location's history (see effect below).
    const mlMode = mlDoneFor.current === locationKey ? 'cache_only' : 'download';
    void start({
      latitude: location.latitude,
      longitude: location.longitude,
      radius_km: settings.radiusKm,
      location_name: location.name,
      source: settings.source,
      start_date: settings.period.start,
      end_date: settings.period.end,
      ml_mode: mlMode,
      target_month: null,
      history_years: 10,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationKey, settings.source, settings.period.start, settings.period.end, settings.radiusKm, sourcesQ.isFetched, start]);

  // Mark the location's ML history as downloaded only when a job actually finished it (a cancelled or failed
  // first run must not switch later runs to cache-only).
  useEffect(() => {
    const st = job.result?.ml_analysis.status;
    if (st === 'ok' || st === 'partial') mlDoneFor.current = locationKey;
  }, [job.result, locationKey]);

  // Keep the last completed result for this location visible (clearly marked) while a new filter runs.
  // (React "adjust state while rendering" pattern instead of effects.)
  const [lastResult, setLastResult] = useState<{ key: string | null; result: NonNullable<typeof job.result> } | null>(null);
  if (job.result && lastResult?.result !== job.result) setLastResult({ key: locationKey, result: job.result });
  const [reportFor, setReportFor] = useState(locationKey);
  if (reportFor !== locationKey) {
    setReportFor(locationKey);
    setReportOpen(false);
  }
  const shown = job.result ?? (lastResult?.key === locationKey ? lastResult.result : null);
  const stale = !job.result && Boolean(shown);

  const harmonized: HarmonizedAvailability = useMemo(() => {
    const normalizedAvailable = shown?.satellite_analysis?.views.normalized?.status === 'available';
    const view = shown?.satellite_analysis?.views.harmonized;
    if (view) {
      return view.status === 'regional_only' && view.series_url
        ? { available: true, reason: view.explanation ?? '', normalizedAvailable }
        : {
            available: false,
            reason: view.explanation ?? 'No harmonized product exists for this location.',
            normalizedAvailable,
          };
    }
    if (location && harmonizationQ.data?.regions?.length) {
      const inside = harmonizationQ.data.regions.some(
        (r) =>
          r.bbox &&
          location.latitude >= r.bbox.south &&
          location.latitude <= r.bbox.north &&
          location.longitude >= r.bbox.west &&
          location.longitude <= r.bbox.east,
      );
      return inside
        ? { available: true, reason: 'Inside a FireLens training region.', normalizedAvailable }
        : {
            available: false,
            reason: 'Harmonized series exist only for five training regions; this point is outside all of them.',
            normalizedAvailable,
          };
    }
    return { available: false, reason: 'Harmonization availability is not known yet.', normalizedAvailable };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, locationKey, harmonizationQ.data]);

  // The harmonized view only applies where the API says a regional series exists.
  const viewSettings: AnalysisSettings =
    (settings.view === 'harmonized' && !harmonized.available) ||
    (settings.view === 'normalized' && !harmonized.normalizedAvailable)
      ? { ...settings, view: 'raw' }
      : settings;

  const onSelect = (place: Parameters<typeof locationSearch>[0]) => navigate(`/globe${locationSearch(place)}`);
  const busy = job.phase === 'submitting' || job.phase === 'running' || job.phase === 'loading_result';

  return (
    <div className="globe-page">
      <motion.aside
        className="analysis-panel"
        aria-label="Location analysis"
        initial={{ opacity: 0, x: 40 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.6, delay: 0.25, ease: [0.22, 1, 0.36, 1] }}
      >
        <LocationSearch
          key={locationKey ?? 'none'}
          variant="panel"
          onSelect={onSelect}
          placeholder="Search another location"
          initialValue={location?.name ?? ''}
        />

        {!location ? (
          <div className="panel-empty">
            <Satellite aria-hidden="true" size={36} />
            <h1>Choose a location</h1>
            <p>
              Search for a place or type coordinates. The globe will turn to it and FireHarmonics will analyse NASA
              FIRMS satellite hotspot detections around it ({ANALYSIS_RADIUS_KM} km radius by default; 10–100 km can be chosen).
            </p>
          </div>
        ) : (
          <>
            <header className="panel-location">
              <h1 data-testid="location-title">{location.name ?? formatCoordinates(location.latitude, location.longitude)}</h1>
              <p>
                {formatCoordinates(location.latitude, location.longitude)} · {settings.radiusKm} km analysis radius
              </p>
            </header>

            {modelQ.data && !modelQ.data.available && (
              <Notice tone="warn" title="AI engine unavailable">
                Forecast sections will be unavailable; descriptive statistics are still produced.
              </Notice>
            )}
            {sourcesQ.isError && (
              <Notice tone="error" role="alert" title="Backend unreachable">
                The FireHarmonics API did not respond. Analyses cannot run until it is available.
              </Notice>
            )}

            <DataControls
              layout="panel"
              settings={viewSettings}
              onChange={setSettings}
              sources={sources}
              harmonized={harmonized}
            />

            <JobStatus state={job} onRetry={() => void job.retry()} />

            {shown && stale && (
              <p className="stale-note" role="status">
                Showing the previous result ({shown.source}, {shown.coverage.start_date} → {shown.coverage.end_date})
                until the new analysis finishes.
              </p>
            )}
            {shown && <PanelSummary result={shown} />}
            {shown && viewSettings.view === 'normalized' && <NormalizedNote result={shown} />}

            <button
              ref={reportTrigger}
              type="button"
              className="button button--primary panel-expand"
              onClick={() => setReportOpen(true)}
              disabled={!shown}
              aria-haspopup="dialog"
              data-testid="open-report"
            >
              <Maximize2 aria-hidden="true" size={16} />
              {shown ? 'Open Location Analysis Report' : busy ? 'Report available when the analysis completes' : 'Report not available'}
            </button>
          </>
        )}
      </motion.aside>

      <AnimatePresence>
        {reportOpen && shown && location && (
          <Suspense fallback={null}>
            <ReportOverlay
              key="report"
              result={shown}
              stale={stale}
              jobId={stale ? null : (job.job?.job_id ?? null)}
              settings={viewSettings}
              onSettingsChange={setSettings}
              sources={sources}
              harmonized={harmonized}
              busy={busy}
              jobState={job}
              onRetry={() => void job.retry()}
              onClose={() => {
                setReportOpen(false);
                reportTrigger.current?.focus();
              }}
            />
          </Suspense>
        )}
      </AnimatePresence>
    </div>
  );
}
