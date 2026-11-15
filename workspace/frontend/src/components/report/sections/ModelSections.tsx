import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { useEffect, useRef, useState, type RefObject } from 'react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api, ApiError, downloadExport } from '../../../api/client';
import { CSV_TABLES, type AnalysisResult, type CsvTable, type NormalizedView } from '../../../api/types';
import { fmtNumber, fmtPercent, fmtYearMonth, humanize, NA } from '../../../lib/format';
import { sourceLabel } from '../../../lib/sources';
import type { AnalysisSettings, HarmonizedAvailability } from '../../ui/DataControls';
import InfoTip from '../../ui/InfoTip';
import { Badge, Metric, Notice, StatusText } from '../../ui/primitives';
import { BaselineCard, ExperimentalXgbCard, methodLabel } from '../ForecastCards';
import { Section, usePalette } from './DescriptiveSections';
import { CalibrationValidation, HarmonizedSeriesChart } from './CalendarExtras';

/* ------------------------------------------------------------------ 6. Forecast */
export function ForecastSection({ result }: { result: AnalysisResult }) {
  const p = result.prediction;
  const ev = p.validation_evidence;
  const bss = ev?.xgboost_vs_best_baseline_brier_skill;
  return (
    <Section
      id="r-forecast"
      title="AI Forecast"
      series={`Series: S-NPP VIIRS (ML record) · target = satellite hotspot detection, not wildfire ignition.${
        p.model
          ? ` Model: ${p.model.name ?? NA} (${p.model.design ?? NA}); trained regions: ${(p.model.trained_regions ?? []).map(humanize).join(', ') || NA}${p.model.training_period?.length === 2 ? `; training period ${p.model.training_period[0]} → ${p.model.training_period[1]}` : ''}.`
          : ''
      }${ev?.setting ? ` Validation: ${ev.setting}.` : ''}`}
    >
      <div className="forecast-hero">
        <ExperimentalXgbCard prediction={p} />
        <div className="forecast-hero__side">
          <BaselineCard prediction={p} radiusKm={result.location.radius_km} />
          <div className="method-box">
            <p className="method-box__label">Method selected by the backend</p>
            <p className="method-box__value">
              <Badge tone={p.method === 'unavailable' ? 'danger' : 'ok'}>{methodLabel(p.method)}</Badge>
            </p>
            <p className="method-box__reason">{p.method_reason ?? p.explanation ?? NA}</p>
          </div>
        </div>
      </div>

      <p className="forecast-meaning">
        {p.target_definition ??
          'Probability of at least one usable satellite hotspot detection within the radius during the target month.'}
        {/ignition/i.test(p.target_definition ?? '')
          ? ''
          : ' It is a satellite-detection probability, not the probability of an individual wildfire ignition.'}
      </p>

      {p.probability === null && (
        <Notice tone="warn" title="No forecast probability issued">
          <p>{p.explanation ?? 'The backend did not issue a probability for this location.'}</p>
          {p.reason && <p>Reason: {humanize(p.reason)}</p>}
          {p.observed_rate_upper_bound_95 != null && (
            <p>95 % upper bound on the monthly rate: {fmtNumber(p.observed_rate_upper_bound_95, 3)}</p>
          )}
        </Notice>
      )}

      <dl className="metric-grid metric-grid--compact">
        <Metric
          label="Target month"
          value={fmtYearMonth(p.target_month)}
          sub={p.lead_months != null ? `Lead ${p.lead_months} month(s)` : undefined}
        />
        <Metric
          label="Compared with normal"
          value={humanize(p.relative_activity?.category)}
          sub={
            p.relative_activity?.ratio_to_seasonal_norm != null
              ? `Ratio to seasonal norm ${fmtNumber(p.relative_activity.ratio_to_seasonal_norm, 2)}`
              : undefined
          }
        />
        <Metric
          label={`Seasonal norm${p.seasonal_norm?.calendar_month ? ` (${p.seasonal_norm.calendar_month})` : ''}`}
          value={fmtPercent(p.seasonal_norm?.frequency ?? null)}
          sub={
            p.seasonal_norm
              ? `${fmtNumber(p.seasonal_norm.years_with_detections ?? null)} of ${fmtNumber(p.seasonal_norm.years_observed ?? null)} observed years had detections`
              : undefined
          }
        />
        <Metric
          label="XGBoost vs best baseline (Brier skill)"
          value={bss?.bss != null ? fmtNumber(bss.bss, 3) : NA}
          sub={
            bss?.ci95?.length === 2
              ? `95 % CI ${fmtNumber(bss.ci95[0], 3)} to ${fmtNumber(bss.ci95[1], 3)}${bss.bss != null && bss.bss <= 0 ? ' · no skill over baseline' : ''}`
              : undefined
          }
        />
      </dl>
    </Section>
  );
}

/* ------------------------------------------------------------------ 7. Satellite */
function HarmonizedChart({ seriesUrl }: { seriesUrl: string }) {
  const pal = usePalette();
  const q = useQuery({
    queryKey: ['harmonized', seriesUrl],
    queryFn: ({ signal }) => api.harmonizedSeries(seriesUrl, signal),
  });
  if (q.isPending)
    return (
      <p className="muted">
        <Loader2 className="spin" size={14} aria-hidden="true" /> Loading harmonized series…
      </p>
    );
  if (q.isError)
    return (
      <Notice tone="error" title="Harmonized series unavailable">
        {(q.error as Error).message}
      </Notice>
    );
  const s = q.data;
  const data = s.data.map((d) => ({
    month: d.month,
    viirs: d.viirs_raw?.count ?? null,
    modisEq: d.modis_equivalent?.count ?? null,
    band:
      d.modis_equivalent?.count_interval_90?.length === 2 &&
      d.modis_equivalent.count_interval_90[0] != null &&
      d.modis_equivalent.count_interval_90[1] != null
        ? [d.modis_equivalent.count_interval_90[0], d.modis_equivalent.count_interval_90[1]]
        : null,
    aqua: d.modis_aqua_observed?.count ?? null,
  }));
  const axis = { stroke: pal.axis, tick: { fill: pal.axis, fontSize: 12 } };
  return (
    <figure className="chart-card" data-testid="harmonized-chart">
      <figcaption>
        Regional harmonized series · {humanize(s.region_id)} · count validation{' '}
        <StatusText status={s.validation?.count?.status ?? null} /> · FRP validation{' '}
        <StatusText status={s.validation?.frp?.status ?? null} />
      </figcaption>
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={pal.grid} vertical={false} />
          <XAxis dataKey="month" {...axis} minTickGap={30} />
          <YAxis {...axis} width={48} />
          <Tooltip
            contentStyle={{
              background: 'var(--surface-strong)',
              border: '1px solid var(--line-strong)',
              borderRadius: 10,
            }}
          />
          <Legend wrapperStyle={{ color: pal.axis, fontSize: 12 }} />
          <Area
            dataKey="band"
            name="MODIS-equivalent 90 % interval"
            stroke="none"
            fill={pal.primary}
            fillOpacity={0.18}
            isAnimationActive={false}
          />
          <Line
            dataKey="modisEq"
            name="MODIS-equivalent count"
            stroke={pal.primary}
            dot={false}
            strokeWidth={2}
            isAnimationActive={false}
          />
          <Line
            dataKey="aqua"
            name="Aqua-MODIS observed"
            stroke={pal.tertiary}
            dot={false}
            strokeWidth={1.5}
            strokeDasharray="4 3"
            isAnimationActive={false}
          />
          <Line
            dataKey="viirs"
            name="Raw VIIRS count"
            stroke={pal.secondary}
            dot={false}
            strokeWidth={1.2}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="fineprint">
        {s.domain} · {s.prediction_type} · intervals: {s.interval_method ?? NA}. Regional scale only; not
        validated at the local scale.
      </p>
    </figure>
  );
}

function NormalizedChart({ view }: { view: NormalizedView }) {
  const pal = usePalette();
  const usable = view.series.filter((s) => s.status === 'available');
  const months = [...new Set(usable.flatMap((s) => s.monthly.map((m) => m.month)))].sort();
  const data = months.map((month) => {
    const row: Record<string, string | number | null> = { month };
    for (const s of usable) row[s.series] = s.monthly.find((m) => m.month === month)?.index ?? null;
    return row;
  });
  const colours = [pal.primary, pal.secondary];
  const axis = { stroke: pal.axis, tick: { fill: pal.axis, fontSize: 12 } };
  return (
    <figure className="chart-card" data-testid="normalized-chart">
      <figcaption>
        Relative activity index <Badge tone="warn">{view.label}</Badge>
      </figcaption>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={pal.grid} vertical={false} />
          <XAxis dataKey="month" {...axis} minTickGap={30} />
          <YAxis {...axis} width={44} />
          <Tooltip
            contentStyle={{
              background: 'var(--surface-strong)',
              border: '1px solid var(--line-strong)',
              borderRadius: 10,
            }}
          />
          <Legend wrapperStyle={{ color: pal.axis, fontSize: 12 }} />
          <ReferenceLine
            y={1}
            stroke={pal.axis}
            strokeDasharray="4 4"
            label={{ value: '1.0 = average month', fill: pal.axis, fontSize: 11, position: 'insideTopRight' }}
          />
          {usable.map((s, i) => (
            <Line
              key={s.series}
              dataKey={s.series}
              name={`${s.instrument} · ${s.product}`}
              stroke={colours[i % colours.length]}
              dot={false}
              strokeWidth={2}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <p className="fineprint">{view.definition}</p>
      {usable.map((s) => (
        <p key={s.series} className="fineprint">
          {s.instrument} ({s.product}): reference {s.reference_period.start} → {s.reference_period.end},{' '}
          {s.reference_period.months} observed months, mean{' '}
          {fmtNumber(s.reference_mean_monthly_detections, 2)} detections per month.
        </p>
      ))}
      <ul className="warn-list">
        {view.limitations.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </figure>
  );
}

export function SatelliteSection({
  result,
  settings,
  onSettingsChange,
  harmonized,
}: {
  result: AnalysisResult;
  settings: AnalysisSettings;
  onSettingsChange: (s: AnalysisSettings) => void;
  harmonized: HarmonizedAvailability;
}) {
  const sa = result.satellite_analysis;
  if (!sa) {
    return (
      <Section id="r-satellite" title="Satellite Analysis">
        <p className="muted">The backend returned no satellite analysis block.</p>
      </Section>
    );
  }
  const hv = sa.views.harmonized;
  return (
    <Section id="r-satellite" title="Satellite Analysis">
      <p className="sat-line">
        {sa.provider.name} — {sa.provider.role}. This period:{' '}
        {sourceLabel(sa.descriptive_series.product ?? result.source)} (
        {fmtNumber(sa.descriptive_series.nominal_resolution_m ?? null)} m). History and forecast:{' '}
        {sa.forecast_series.instrument} {sa.forecast_series.platforms.join(', ')}.
      </p>
      {sa.views.normalized?.status === 'available' && (
        <div className="sat-switch" data-testid="normalized-card">
          {settings.view === 'normalized' ? (
            <button
              type="button"
              className="report-button"
              onClick={() => onSettingsChange({ ...settings, view: 'raw' })}
            >
              Hide normalized index
            </button>
          ) : (
            harmonized.normalizedAvailable && (
              <button
                type="button"
                className="report-button"
                onClick={() => onSettingsChange({ ...settings, view: 'normalized' })}
              >
                Show normalized index
              </button>
            )
          )}
          <InfoTip text="Relative activity index (1.0 = average month). A mathematical normalization of one sensor: not a harmonization and not validated." />
        </div>
      )}

      {settings.view === 'harmonized' && result.ml_analysis.unified_calendar?.status === 'ok' && (
        <HarmonizedSeriesChart cal={result.ml_analysis.unified_calendar} />
      )}
      {result.ml_analysis.unified_calendar && (
        <CalibrationValidation cal={result.ml_analysis.unified_calendar} />
      )}
      {settings.view === 'harmonized' && hv.status === 'regional_only' && hv.series_url && (
        <HarmonizedChart seriesUrl={hv.series_url} />
      )}
      {settings.view === 'normalized' && sa.views.normalized?.status === 'available' && (
        <NormalizedChart view={sa.views.normalized} />
      )}
    </Section>
  );
}

/* ------------------------------------------------------------------ 8. Export */
export const ML_TABLES = new Set<CsvTable>([
  'ml_monthly',
  'ml_seasonal',
  'ml_anomalies',
  'ml_hotspots',
  'calendar',
]);
export const TABLE_LABELS: Record<CsvTable, string> = {
  daily: 'Daily counts (selected period)',
  monthly: 'Monthly counts (selected period)',
  annual: 'Annual counts (selected period)',
  hotspots: 'Detection cells (selected period)',
  ml_monthly: 'ML monthly record (S-NPP VIIRS)',
  ml_seasonal: 'ML seasonal climatology',
  ml_anomalies: 'ML anomalous months',
  ml_hotspots: 'ML Gi* hotspot cells',
  calendar: 'Harmonized MODIS+VIIRS calendar (2002–now)',
};

export function ExportSection({
  result,
  jobId,
  bodyRef,
  autoPdf,
  onAutoPdfDone,
}: {
  result: AnalysisResult;
  jobId: string | null;
  bodyRef: RefObject<HTMLDivElement | null>;
  /** Start the PDF as soon as the report has rendered (requested from the side panel). */
  autoPdf?: boolean;
  onAutoPdfDone?: () => void;
}) {
  const pdfButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!autoPdf) return;
    // give the charts a moment to lay out before the PDF snapshot
    const t = setTimeout(() => {
      pdfButton.current?.click();
      onAutoPdfDone?.();
    }, 900);
    return () => clearTimeout(t);
  }, [autoPdf, onAutoPdfDone]);
  const mlRan = ['ok', 'partial', 'insufficient_data'].includes(
    result.components?.ml_analysis ?? result.ml_analysis.status,
  );
  const [table, setTable] = useState<CsvTable>('monthly');
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: 'info' | 'error'; text: string } | null>(null);

  const run = async (kind: string, fn: () => Promise<string>) => {
    setBusy(kind);
    setMessage(null);
    try {
      const name = await fn();
      setMessage({ tone: 'info', text: `Downloaded ${name}.` });
    } catch (err) {
      const text =
        err instanceof ApiError
          ? `${err.message}${err.status ? ` (HTTP ${err.status})` : ''}`
          : (err as Error).message;
      setMessage({ tone: 'error', text });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Section id="r-export" title="Export" className="report-export">
      <div className="export-grid">
        <div className="export-item">
          <p className="export-item__label">
            PDF report <InfoTip text="Rendered in your browser from this report." />
          </p>
          <button
            ref={pdfButton}
            type="button"
            className="report-button"
            disabled={busy !== null}
            onClick={() =>
              run('pdf', async () => {
                const { exportReportPdf } = await import('../../../lib/pdf');
                if (!bodyRef.current) throw new Error('Report content is not available.');
                return exportReportPdf(bodyRef.current, result);
              })
            }
          >
            {busy === 'pdf' && <Loader2 className="spin" size={14} aria-hidden="true" />} Download PDF
          </button>
        </div>
        <div className="export-item">
          <p className="export-item__label">
            JSON (full result) <InfoTip text="The backend export: job id, request and the complete result." />
          </p>
          <button
            type="button"
            className="report-button"
            disabled={!jobId || busy !== null}
            onClick={() => run('json', () => downloadExport(jobId!, 'json'))}
          >
            {busy === 'json' && <Loader2 className="spin" size={14} aria-hidden="true" />} Download JSON
          </button>
        </div>
        <div className="export-item">
          <p className="export-item__label">
            <label htmlFor="csv-table">CSV table</label> <InfoTip text="One table of this result as CSV." />
          </p>
          <select id="csv-table" value={table} onChange={(e) => setTable(e.target.value as CsvTable)}>
            {CSV_TABLES.map((t) => (
              <option key={t} value={t} disabled={ML_TABLES.has(t) && !mlRan}>
                {TABLE_LABELS[t]}
                {ML_TABLES.has(t) && !mlRan ? ' (ML stage did not run)' : ''}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="report-button"
            disabled={!jobId || busy !== null}
            onClick={() => run('csv', () => downloadExport(jobId!, 'csv', table))}
          >
            {busy === 'csv' && <Loader2 className="spin" size={14} aria-hidden="true" />} Download CSV
          </button>
        </div>
      </div>
      {!jobId && (
        <p className="fineprint">JSON/CSV exports become available when the current analysis completes.</p>
      )}
      {message && (
        <p
          className={`export-message export-message--${message.tone}`}
          role={message.tone === 'error' ? 'alert' : 'status'}
        >
          {message.text}
        </p>
      )}
    </Section>
  );
}
