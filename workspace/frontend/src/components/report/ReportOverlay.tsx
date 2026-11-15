import { X } from 'lucide-react';
import { motion } from 'motion/react';
import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { FirmsSource, AnalysisResult } from '../../api/types';
import type { AnalysisJobState } from '../../api/useAnalysisJob';
import { formatCoordinates } from '../../lib/geocode';
import { fmtDate, fmtDateTime, NA } from '../../lib/format';
import { sourceLongLabel } from '../../lib/sources';
import DataControls, { type AnalysisSettings, type HarmonizedAvailability } from '../ui/DataControls';
import { Notice, StatusText } from '../ui/primitives';
import JobStatus from './JobStatus';
import { CalendarSection, OverviewSection, PatternsSection, TrendsSection } from './sections/DescriptiveSections';
import { ExportSection, ForecastSection, SatelliteSection } from './sections/ModelSections';

interface Props {
  result: AnalysisResult;
  stale: boolean;
  jobId: string | null;
  settings: AnalysisSettings;
  onSettingsChange: (s: AnalysisSettings) => void;
  sources: readonly FirmsSource[];
  harmonized: HarmonizedAvailability;
  busy: boolean;
  jobState: AnalysisJobState;
  onRetry: () => void;
  onClose: () => void;
}

const SECTIONS = [
  ['r-overview', 'Overview'],
  ['r-calendar', 'Activity calendar'],
  ['r-trends', 'Historical trends'],
  ['r-patterns', 'AI pattern discovery'],
  ['r-forecast', 'AI forecast'],
  ['r-satellite', 'Satellite analysis'],
  ['r-export', 'Export'],
] as const;

export default function ReportOverlay(props: Props) {
  const { result, stale, settings, onSettingsChange, sources, harmonized, busy, jobState, onRetry, onClose } = props;
  const dialogRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const loc = result.location;

  // Keep the latest close handler without re-running the mount effect (which moves focus) on every render.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.querySelector<HTMLElement>('.report__close')?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab' || !dialog) return;
      const focusable = [
        ...dialog.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ].filter((el) => el.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    // capture phase: native <select> elements can swallow Escape before it bubbles
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  const jump = (id: string) => {
    const el = bodyRef.current?.querySelector<HTMLElement>(`#${id}`);
    el?.scrollIntoView({ behavior: document.documentElement.dataset.motion === 'reduce' ? 'auto' : 'smooth', block: 'start' });
    el?.focus({ preventScroll: true });
  };

  // Rendered into <body> so the dialog sits above the site navigation and page stacking contexts.
  return createPortal(
    <motion.div
      className="report-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25 }}
    >
      <motion.div
        ref={dialogRef}
        className="report"
        role="dialog"
        aria-modal="true"
        aria-labelledby="report-title"
        data-testid="report"
        initial={{ opacity: 0, scale: 0.92, x: 120, borderRadius: 32 }}
        animate={{ opacity: 1, scale: 1, x: 0, borderRadius: 24 }}
        exit={{ opacity: 0, scale: 0.94, x: 80 }}
        transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        style={{ transformOrigin: '85% 40%' }}
      >
        <header className="report__header">
          <div className="report__title-block">
            <p className="report__eyebrow">Location Analysis Report</p>
            <h2 id="report-title">{loc.name ?? formatCoordinates(loc.latitude, loc.longitude)}</h2>
            <p className="report__meta">
              {formatCoordinates(loc.latitude, loc.longitude)} · radius {loc.radius_km} km · {loc.crs ?? 'WGS 84'}
            </p>
            <p className="report__meta">
              Coverage ({result.source}): {fmtDate(result.coverage.start_date)} → {fmtDate(result.coverage.end_date)}
              {result.coverage.complete ? ' · complete' : ' · incomplete coverage'}
              {result.ml_analysis.data_coverage?.window
                ? ` · ML record (S-NPP VIIRS): ${fmtDate(result.ml_analysis.data_coverage.window.start)} → ${fmtDate(result.ml_analysis.data_coverage.window.end)}`
                : ''}
            </p>
            <p className="report__meta">
              Result <StatusText status={result.status} /> · downloaded {fmtDateTime(result.coverage.downloaded_at ?? null)} · schema{' '}
              {result.schema_version ?? NA}
            </p>
          </div>
          <button type="button" className="icon-button report__close" onClick={onClose}>
            <X aria-hidden="true" size={22} />
            <span className="visually-hidden">Close report</span>
          </button>
        </header>

        <div className="report__controls">
          <DataControls
            layout="report"
            settings={settings}
            onChange={onSettingsChange}
            sources={sources}
            harmonized={harmonized}
          />
          <div className="report__job">
            {stale && (
              <Notice tone="info" title="Updating">
                This report still shows the previous result ({sourceLongLabel(result.source)}). It will refresh when the
                new analysis completes.
              </Notice>
            )}
            {(busy || jobState.phase === 'failed' || jobState.phase === 'error') && <JobStatus state={jobState} onRetry={onRetry} />}
          </div>
        </div>

        <nav className="report__toc" aria-label="Report sections">
          {SECTIONS.map(([id, label]) => (
            <button key={id} type="button" className="chip" onClick={() => jump(id)}>
              {label}
            </button>
          ))}
          <button type="button" className="icon-button report__toc-close" onClick={onClose}>
            <X aria-hidden="true" size={18} />
            <span className="visually-hidden">Close report</span>
          </button>
        </nav>

        <div className="report__body" ref={bodyRef} data-pdf-root>
          {(result.warnings?.length > 0 || (result.ml_analysis.limitations?.length ?? 0) > 0) && (
            <section className="report-section report-section--warnings" data-pdf-section aria-label="Warnings and limitations">
              <h3>Warnings &amp; limitations</h3>
              <ul className="warn-list">
                {result.warnings?.map((w) => (
                  <li key={`w-${w}`}>{w}</li>
                ))}
                {result.ml_analysis.limitations?.map((l) => (
                  <li key={`l-${l}`} className="warn-list__limitation">
                    {l}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <OverviewSection result={result} />
          <CalendarSection result={result} />
          <TrendsSection result={result} />
          <PatternsSection result={result} />
          <ForecastSection result={result} />
          <SatelliteSection result={result} settings={settings} onSettingsChange={onSettingsChange} harmonized={harmonized} />
          <ExportSection result={result} jobId={props.jobId} bodyRef={bodyRef} />
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  );
}
