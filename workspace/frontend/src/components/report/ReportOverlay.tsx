import { AlertTriangle, Globe, ListChecks, Search, X } from 'lucide-react';
import { motion } from 'motion/react';
import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { FirmsSource, AnalysisResult } from '../../api/types';
import type { AnalysisJobState } from '../../api/useAnalysisJob';
import { formatCoordinates } from '../../lib/geocode';
import { fmtDate } from '../../lib/format';
import { sourceLongLabel } from '../../lib/sources';
import DataControls, { type AnalysisSettings, type HarmonizedAvailability } from '../ui/DataControls';
import { Notice } from '../ui/primitives';
import JobStatus from './JobStatus';
import {
  CalendarSection,
  OverviewSection,
  PatternsSection,
  TrendsSection,
} from './sections/DescriptiveSections';
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
  ['r-satellite', 'Satellite analysis'],
  ['r-forecast', 'AI forecast'],
  ['r-export', 'Export'],
] as const;

/* Optional artwork for the report (drop files named meteor / warnings / location into src/assets/report/). */
const ART = import.meta.glob<string>('../../assets/report/*.{png,webp,svg,jpg,jpeg}', {
  eager: true,
  import: 'default',
  query: '?url',
});
const art = (name: string) => Object.entries(ART).find(([path]) => path.includes(`/${name}.`))?.[1];

/** Every warning and limitation of the result in one list, without repeats (sections no longer repeat them). */
function collectWarnings(result: AnalysisResult): string[] {
  const ml = result.ml_analysis;
  const all = [
    ...(result.warnings ?? []),
    ...(ml.limitations ?? []),
    ...(ml.unified_calendar?.limitations ?? []),
    ...(ml.current_conditions?.limitations ?? []),
    ...(result.satellite_analysis?.warnings ?? []),
    ...(ml.harmonization?.note ? [ml.harmonization.note] : []),
  ];
  return [...new Set(all.map((w) => w.trim()).filter(Boolean))];
}

export default function ReportOverlay(props: Props) {
  const { result, stale, settings, onSettingsChange, sources, harmonized, busy, jobState, onRetry, onClose } =
    props;
  const dialogRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const loc = result.location;
  const warnings = collectWarnings(result);
  const meteor = art('meteor');
  const warningsArt = art('warnings');
  const locationArt = art('location');

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
    const el = dialogRef.current?.querySelector<HTMLElement>(`#${id}`);
    el?.scrollIntoView({
      behavior: document.documentElement.dataset.motion === 'reduce' ? 'auto' : 'smooth',
      block: 'start',
    });
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
        initial={{ opacity: 0, scale: 0.94, y: 24 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 16 }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      >
        <header className="report__top">
          <p className="report__kicker">Location Analysis Report</p>
          <button type="button" className="report__close" onClick={onClose}>
            <X aria-hidden="true" size={26} />
            <span className="visually-hidden">Close report</span>
          </button>
        </header>

        <div className="report-loc">
          {locationArt ? (
            <img className="report-loc__art" src={locationArt} alt="" />
          ) : (
            <span className="report-loc__icon" aria-hidden="true">
              <Globe size={44} strokeWidth={1.6} />
              <Search size={20} strokeWidth={2.4} className="report-loc__lens" />
            </span>
          )}
          <div className="report-loc__card">
            <h2 id="report-title">{loc.name ?? formatCoordinates(loc.latitude, loc.longitude)}</h2>
            <p>
              {formatCoordinates(loc.latitude, loc.longitude)} · {loc.radius_km} km radius ·{' '}
              {fmtDate(result.coverage.start_date)} → {fmtDate(result.coverage.end_date)}
            </p>
          </div>
        </div>

        <div className="report__controls">
          <DataControls
            layout="report"
            settings={settings}
            onChange={onSettingsChange}
            sources={sources}
            harmonized={harmonized}
          />
        </div>
        {(stale || busy || jobState.phase === 'failed' || jobState.phase === 'error') && (
          <div className="report__job">
            {stale && (
              <Notice tone="info" title="Updating">
                This report still shows the previous result ({sourceLongLabel(result.source)}). It will
                refresh when the new analysis completes.
              </Notice>
            )}
            {(busy || jobState.phase === 'failed' || jobState.phase === 'error') && (
              <JobStatus state={jobState} onRetry={onRetry} />
            )}
          </div>
        )}

        <nav className="report-content" aria-label="Report sections">
          {meteor && <img className="report-content__art" src={meteor} alt="" />}
          <h3 className="report-content__title">
            <ListChecks aria-hidden="true" size={30} /> Content
          </h3>
          <ul>
            {SECTIONS.map(([id, label]) => (
              <li key={id}>
                <button type="button" onClick={() => jump(id)}>
                  {label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="report__body" ref={bodyRef} data-pdf-root>
          {warnings.length > 0 && (
            <section
              className="report-warnings"
              data-pdf-section
              aria-labelledby="report-warnings-h"
              data-has-art={warningsArt ? 'true' : undefined}
            >
              <div>
                <h3 id="report-warnings-h" className="report-warnings__title">
                  <AlertTriangle aria-hidden="true" size={24} className="report-warnings__icon" /> Warnings
                  &amp; limitations
                </h3>
                <ul className="report-warnings__box" tabIndex={0} aria-label="Warnings and limitations list">
                  {warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </div>
              {warningsArt && <img className="report-warnings__art" src={warningsArt} alt="" />}
            </section>
          )}
          <OverviewSection result={result} />
          <CalendarSection result={result} />
          <TrendsSection result={result} />
          <PatternsSection result={result} />
          <SatelliteSection
            result={result}
            settings={settings}
            onSettingsChange={onSettingsChange}
            harmonized={harmonized}
          />
          <ForecastSection result={result} />
          <ExportSection result={result} jobId={props.jobId} bodyRef={bodyRef} />
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  );
}
