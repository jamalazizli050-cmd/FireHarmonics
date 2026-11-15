import { useMemo, useState, type ReactNode } from 'react';
import {
  Bar,
  BarChart,
  Brush,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import type { AnalysisResult, MlMonthly } from '../../../api/types';
import {
  fmtNumber,
  fmtPercent,
  fmtSignedPercent,
  fmtYearMonth,
  humanize,
  MONTH_ABBR,
  monthName,
  NA,
} from '../../../lib/format';
import { sourceLabel } from '../../../lib/sources';
import { chartPalette, useA11y } from '../../../state/a11y';
import InfoTip from '../../ui/InfoTip';
import { Metric, Notice, StatusText } from '../../ui/primitives';
import { CombinedHeatmap } from './CalendarExtras';

export function Section({
  id,
  title,
  series,
  children,
  className,
}: {
  id: string;
  title: string;
  series?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      id={id}
      className={`report-section ${className ?? ''}`}
      tabIndex={-1}
      aria-labelledby={`${id}-h`}
      data-pdf-section
    >
      <header className="report-section__head">
        <h3 id={`${id}-h`}>{title}</h3>
        {typeof series === 'string' && <InfoTip text={series} label={`About the data in ${title}`} />}
      </header>
      {children}
    </section>
  );
}

export function usePalette() {
  const { colorBlind, highContrast } = useA11y();
  return chartPalette(colorBlind, highContrast);
}

const tooltipStyle = {
  contentStyle: {
    background: 'var(--surface-strong)',
    border: '1px solid var(--line-strong)',
    borderRadius: 10,
    color: 'var(--text)',
    fontSize: '0.85rem',
  },
  labelStyle: { color: 'var(--text)' },
  itemStyle: { color: 'var(--text)' },
};

const mlSeriesLabel = (r: AnalysisResult) =>
  `Series: ${r.ml_analysis.series?.instrument ?? 'VIIRS'} · ${r.ml_analysis.series?.platform ?? 'Suomi NPP'} (ML record, ${(r.ml_analysis.series?.products ?? []).join(' then ') || 'S-NPP'})`;
const descSeriesLabel = (r: AnalysisResult) =>
  `Series: ${sourceLabel(r.provenance?.product ?? r.source)} via ${r.provenance?.provider ?? 'NASA FIRMS'} (selected period)`;

function MlUnavailable({ result }: { result: AnalysisResult }) {
  const ml = result.ml_analysis;
  return (
    <Notice tone="warn" title={`ML analysis ${humanize(ml.status)}`}>
      <p>{ml.explanation ?? ml.error?.detail ?? 'The ML engine produced no content for this analysis.'}</p>
      {ml.error?.code && <p className="muted">Error code: {ml.error.code}</p>}
    </Notice>
  );
}

const mlHasContent = (r: AnalysisResult) =>
  ['ok', 'partial', 'insufficient_data'].includes(r.ml_analysis.status);

/* ------------------------------------------------------------------ 3. Calendar */
function heatColor(value: number | null, max: number, heat: string[]) {
  if (value === null) return 'transparent';
  if (value === 0 || max <= 0) return heat[0];
  const t = Math.log1p(value) / Math.log1p(max);
  const idx = 1 + Math.min(heat.length - 2, Math.floor(t * (heat.length - 1)));
  return heat[idx];
}

function MonthlyHeatmap({ monthly }: { monthly: MlMonthly[] }) {
  const pal = usePalette();
  const [selected, setSelected] = useState<MlMonthly | null>(null);
  const byYear = useMemo(() => {
    const map = new Map<string, (MlMonthly | null)[]>();
    for (const m of monthly) {
      const [y, mm] = m.month.split('-');
      if (!map.has(y)) map.set(y, Array(12).fill(null));
      map.get(y)![Number(mm) - 1] = m;
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [monthly]);
  const max = Math.max(0, ...monthly.map((m) => (m.observed ? (m.detections ?? 0) : 0)));

  return (
    <div className="heatmap-wrap">
      <div className="heatmap" role="grid" aria-label="Monthly detections by year">
        <div className="heatmap__row heatmap__row--head" role="row">
          <span role="columnheader" className="heatmap__year" />
          {MONTH_ABBR.map((m) => (
            <span key={m} role="columnheader" className="heatmap__mhead">
              {m}
            </span>
          ))}
        </div>
        {byYear.map(([year, cells]) => (
          <div key={year} className="heatmap__row" role="row">
            <span role="rowheader" className="heatmap__year">
              {year}
            </span>
            {cells.map((c, i) => {
              if (!c)
                return (
                  <span
                    key={i}
                    role="gridcell"
                    className="heatmap__cell heatmap__cell--outside"
                    aria-label={`${MONTH_ABBR[i]} ${year}: outside the record`}
                  />
                );
              const unobserved = !c.observed || c.detections === null;
              const label = unobserved
                ? `${fmtYearMonth(c.month)}: not observed (coverage ${fmtPercent(c.coverage_fraction, 0)})`
                : `${fmtYearMonth(c.month)}: ${fmtNumber(c.detections)} detections`;
              return (
                <button
                  key={i}
                  type="button"
                  role="gridcell"
                  className={`heatmap__cell ${unobserved ? 'heatmap__cell--unobserved' : ''} ${selected?.month === c.month ? 'is-selected' : ''}`}
                  style={unobserved ? undefined : { background: heatColor(c.detections, max, pal.heat) }}
                  aria-label={label}
                  title={label}
                  onClick={() => setSelected(c)}
                />
              );
            })}
          </div>
        ))}
      </div>
      <div className="heatmap-side">
        <div className="heat-legend" aria-hidden="true">
          <span>0</span>
          <span
            className="heat-legend__ramp"
            style={{ background: `linear-gradient(90deg, ${pal.heat.join(',')})` }}
          />
          <span>{fmtNumber(max)}</span>
        </div>
        <p className="heat-legend__unobs">
          <span
            className="heatmap__cell heatmap__cell--unobserved heatmap__cell--legend"
            aria-hidden="true"
          />{' '}
          Not observed (no data — not zero)
        </p>
        <div className="heatmap-detail" aria-live="polite">
          {selected ? (
            <dl>
              <dt>Month</dt>
              <dd>{fmtYearMonth(selected.month)}</dd>
              <dt>Detections</dt>
              <dd>{selected.observed ? fmtNumber(selected.detections) : 'Not observed'}</dd>
              <dt>FRP sum</dt>
              <dd>{selected.frp_sum_mw != null ? `${fmtNumber(selected.frp_sum_mw, 1)} MW` : NA}</dd>
              <dt>Coverage</dt>
              <dd>{fmtPercent(selected.coverage_fraction, 0)}</dd>
              <dt>Products</dt>
              <dd>{selected.products ?? NA}</dd>
            </dl>
          ) : (
            <p className="muted">Select a month to see its details.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function DailyCalendar({ result }: { result: AnalysisResult }) {
  const pal = usePalette();
  const days = result.historical_activity?.daily_counts ?? [];
  if (!days.length) return <p className="muted">No covered days in the selected period.</p>;
  const max = Math.max(0, ...days.map((d) => d.detections ?? 0));
  const first = new Date(`${days[0].date}T00:00:00Z`);
  const offset = (first.getUTCDay() + 6) % 7; // Monday-first
  const cells: ({ date: string; detections: number | null } | null)[] = [
    ...Array(offset).fill(null),
    ...days,
  ];
  const weeks: (typeof cells)[] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return (
    <div className="daily-cal-wrap">
      <div
        className="daily-cal"
        role="img"
        aria-label={`Daily detections, ${days[0].date} to ${days.at(-1)!.date}; maximum ${max} per day.`}
      >
        {weeks.map((w, wi) => (
          <div key={wi} className="daily-cal__week">
            {w.map((d, di) =>
              d ? (
                <span
                  key={di}
                  className={`daily-cal__day ${d.detections === null ? 'heatmap__cell--unobserved' : ''}`}
                  style={
                    d.detections === null ? undefined : { background: heatColor(d.detections, max, pal.heat) }
                  }
                  title={`${d.date}: ${d.detections === null ? 'no data' : `${d.detections} detections`}`}
                />
              ) : (
                <span key={di} className="daily-cal__day daily-cal__day--pad" />
              ),
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function CalendarSection({ result }: { result: AnalysisResult }) {
  const cal = result.ml_analysis.unified_calendar;
  const calOk = cal?.status === 'ok' && (cal.monthly?.length ?? 0) > 0;
  const [view, setView] = useState<'combined' | 'ml' | 'daily'>(
    calOk ? 'combined' : mlHasContent(result) ? 'ml' : 'daily',
  );
  const monthly = result.ml_analysis.historical_activity?.monthly ?? [];
  return (
    <Section
      id="r-calendar"
      title="Burning Activity Calendar"
      series={
        view === 'combined'
          ? `Series: Aqua MODIS 2002–2011, then S-NPP and NOAA-20 VIIRS on the Aqua-MODIS scale (${cal?.unit ?? 'detections'})`
          : view === 'ml'
            ? mlSeriesLabel(result)
            : descSeriesLabel(result)
      }
    >
      <div className="segmented" role="tablist" aria-label="Calendar view">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'combined'}
          onClick={() => setView('combined')}
        >
          MODIS + VIIRS harmonized (2002–now)
        </button>
        <button type="button" role="tab" aria-selected={view === 'ml'} onClick={() => setView('ml')}>
          Monthly by year (10-year ML record)
        </button>
        <button type="button" role="tab" aria-selected={view === 'daily'} onClick={() => setView('daily')}>
          Daily (selected period)
        </button>
      </div>
      {view === 'combined' ? (
        calOk ? (
          <>
            <CombinedHeatmap cal={cal!} />
            <p className="fineprint">Outlined months are unusually high for that calendar month.</p>
          </>
        ) : (
          <Notice tone="warn" title={`Harmonized calendar ${humanize(cal?.status ?? 'unavailable')}`}>
            {cal?.explanation ?? 'The backend did not return a MODIS+VIIRS calendar for this analysis.'}
          </Notice>
        )
      ) : view === 'ml' ? (
        monthly.length ? (
          <MonthlyHeatmap monthly={monthly} />
        ) : (
          <MlUnavailable result={result} />
        )
      ) : (
        <DailyCalendar result={result} />
      )}
    </Section>
  );
}

/* ------------------------------------------------------------------ 4. Trends */
export function TrendsSection({ result }: { result: AnalysisResult }) {
  const pal = usePalette();
  const ml = result.ml_analysis;
  const annual = Object.entries(ml.historical_activity?.annual_totals_complete_years ?? {}).map(
    ([year, detections]) => ({
      year,
      detections,
    }),
  );
  const monthly = (ml.historical_activity?.monthly ?? []).map((m) => ({
    month: m.month,
    detections: m.observed ? m.detections : null,
  }));
  const trend = ml.long_term_trends;
  const desc = result.historical_activity;
  const sig = trend?.['significant_at_0.05'];
  const axis = { stroke: pal.axis, tick: { fill: pal.axis, fontSize: 12 } };

  return (
    <Section id="r-trends" title="Historical Trends" series={mlSeriesLabel(result)}>
      {mlHasContent(result) ? (
        <>
          <div className="chart-grid">
            <figure className="chart-card">
              <figcaption>Annual detections (complete years)</figcaption>
              {annual.length ? (
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={annual} maxBarSize={56} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke={pal.grid} vertical={false} />
                    <XAxis dataKey="year" {...axis} />
                    <YAxis {...axis} width={48} />
                    <Tooltip {...tooltipStyle} cursor={{ fill: 'rgba(255,255,255,0.05)' }} />
                    <Bar dataKey="detections" name="Detections" fill={pal.primary} radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p className="muted">No complete calendar years in the record.</p>
              )}
            </figure>
            <figure className="chart-card">
              <figcaption>Monthly detections (gaps = unobserved months)</figcaption>
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={monthly} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke={pal.grid} vertical={false} />
                  <XAxis dataKey="month" {...axis} minTickGap={28} />
                  <YAxis {...axis} width={48} />
                  <Tooltip {...tooltipStyle} />
                  <Line
                    type="monotone"
                    dataKey="detections"
                    name="Detections"
                    stroke={pal.primary}
                    dot={false}
                    strokeWidth={2}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                  {monthly.length > 24 && (
                    <Brush
                      dataKey="month"
                      height={22}
                      stroke={pal.secondary}
                      fill="transparent"
                      travellerWidth={8}
                    />
                  )}
                </LineChart>
              </ResponsiveContainer>
            </figure>
          </div>
          <dl className="metric-grid metric-grid--compact">
            <Metric
              label="Direction"
              value={trend?.direction ? humanize(trend.direction) : NA}
              sub={trend?.method}
            />
            <Metric
              label="Theil–Sen slope"
              value={
                trend?.theil_sen_pct_per_year != null
                  ? `${fmtSignedPercent(trend.theil_sen_pct_per_year)} / yr`
                  : NA
              }
              sub={
                trend?.ci95_pct_per_year && trend.ci95_pct_per_year.length === 2
                  ? `95 % CI ${fmtSignedPercent(trend.ci95_pct_per_year[0])} to ${fmtSignedPercent(trend.ci95_pct_per_year[1])}`
                  : undefined
              }
            />
            <Metric
              label="Mann–Kendall"
              value={trend?.p_value != null ? `p = ${trend.p_value.toFixed(3)}` : NA}
              sub={
                sig === true
                  ? 'Statistically significant at 0.05'
                  : sig === false
                    ? 'Not statistically significant'
                    : undefined
              }
            />
          </dl>
        </>
      ) : (
        <MlUnavailable result={result} />
      )}

      {desc?.monthly_counts?.length ? (
        <figure className="chart-card">
          <figcaption>Selected period · {sourceLabel(result.source)}</figcaption>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart
              data={desc.monthly_counts}
              maxBarSize={48}
              margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
            >
              <CartesianGrid stroke={pal.grid} vertical={false} />
              <XAxis dataKey="month" {...axis} minTickGap={20} />
              <YAxis {...axis} width={48} />
              <Tooltip
                {...tooltipStyle}
                formatter={(v, _n, item) => [
                  `${fmtNumber(v as number)}${(item?.payload as { complete?: boolean })?.complete ? '' : ' (partial month)'}`,
                  'Detections',
                ]}
              />
              <Bar dataKey="detections" fill={pal.secondary} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </figure>
      ) : null}
    </Section>
  );
}

/* ------------------------------------------------------------------ 5. Patterns */
export function PatternsSection({ result }: { result: AnalysisResult }) {
  const pal = usePalette();
  const ml = result.ml_analysis;
  const seasonal = ml.seasonal_patterns;
  const hotspots = ml.hotspots ?? [];
  const seasonData = (seasonal?.by_calendar_month ?? []).map((m) => ({
    ...m,
    fraction_pct:
      m.fraction_of_years_with_detections != null ? m.fraction_of_years_with_detections * 100 : null,
  }));
  const axis = { stroke: pal.axis, tick: { fill: pal.axis, fontSize: 12 } };

  if (!mlHasContent(result)) {
    return (
      <Section id="r-patterns" title="AI Pattern Discovery" series={mlSeriesLabel(result)}>
        <MlUnavailable result={result} />
      </Section>
    );
  }

  return (
    <Section id="r-patterns" title="AI Pattern Discovery" series={mlSeriesLabel(result)}>
      <div className="chart-grid">
        <figure className="chart-card">
          <figcaption>
            Seasonal pattern · peak {seasonal?.peak_month ? monthName(seasonal.peak_month) : NA}{' '}
            <StatusText status={seasonal?.status} />
          </figcaption>
          {seasonData.length ? (
            <ResponsiveContainer width="100%" height={250}>
              <ComposedChart
                data={seasonData}
                maxBarSize={40}
                margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
              >
                <CartesianGrid stroke={pal.grid} vertical={false} />
                <XAxis dataKey="month" {...axis} />
                <YAxis yAxisId="d" {...axis} width={44} />
                <YAxis yAxisId="f" orientation="right" domain={[0, 100]} unit="%" {...axis} width={44} />
                <Tooltip {...tooltipStyle} />
                <Legend wrapperStyle={{ color: pal.axis, fontSize: 12 }} />
                <Bar
                  yAxisId="d"
                  dataKey="median_detections"
                  name="Median detections"
                  fill={pal.primary}
                  radius={[4, 4, 0, 0]}
                />
                <Line
                  yAxisId="f"
                  type="monotone"
                  dataKey="fraction_pct"
                  name="% of years with detections"
                  stroke={pal.secondary}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          ) : (
            <p className="muted">{seasonal?.note ?? 'No seasonal pattern available.'}</p>
          )}
          <p className="fineprint">
            Core season (80 % of detections): {seasonal?.core_season_months_80pct?.join(', ') || NA} · lowest
            activity: {seasonal?.lowest_activity_months?.join(', ') || NA}
          </p>
        </figure>

        <figure className="chart-card">
          <figcaption>
            Recurring geographic hotspots (Getis-Ord Gi*) · {fmtNumber(hotspots.length)} cells with detections
          </figcaption>
          {hotspots.length ? (
            <ResponsiveContainer width="100%" height={250}>
              <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={pal.grid} />
                <XAxis
                  type="number"
                  dataKey="cell_lon"
                  name="Longitude"
                  domain={['auto', 'auto']}
                  {...axis}
                  tickFormatter={(v) => `${(v as number).toFixed(2)}°`}
                />
                <YAxis
                  type="number"
                  dataKey="cell_lat"
                  name="Latitude"
                  domain={['auto', 'auto']}
                  {...axis}
                  width={56}
                  tickFormatter={(v) => `${(v as number).toFixed(2)}°`}
                />
                <ZAxis type="number" dataKey="total_detections" range={[30, 400]} name="Detections" />
                <Tooltip {...tooltipStyle} cursor={{ strokeDasharray: '3 3' }} />
                <Legend wrapperStyle={{ color: pal.axis, fontSize: 12 }} />
                <Scatter
                  name="Gi* hotspot (99 %)"
                  data={hotspots.filter((h) => h.gi_star_hotspot_99)}
                  fill={pal.accent}
                />
                <Scatter
                  name="Other cells"
                  data={hotspots.filter((h) => !h.gi_star_hotspot_99)}
                  fill={pal.secondary}
                />
              </ScatterChart>
            </ResponsiveContainer>
          ) : (
            <p className="muted">No hotspot cells with detections.</p>
          )}
        </figure>
      </div>

      <h4 className="subhead">Anomalous months (robust z-score against the same month in other years)</h4>
      {ml.anomalies && ml.anomalies.length ? (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Scrollable table">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Month</th>
                <th scope="col">Detections</th>
                <th scope="col">Median, other years</th>
                <th scope="col">Robust z</th>
                <th scope="col">Direction</th>
              </tr>
            </thead>
            <tbody>
              {ml.anomalies.slice(0, 8).map((a) => (
                <tr key={a.month}>
                  <td>{fmtYearMonth(a.month)}</td>
                  <td>{fmtNumber(a.detections)}</td>
                  <td>{fmtNumber(a.same_month_median_other_years, 1)}</td>
                  <td>{fmtNumber(a.robust_z, 2)}</td>
                  <td>{humanize(a.direction)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">
          No anomalous months
          {ml.anomaly_detection
            ? ` (${fmtNumber(ml.anomaly_detection.n_months_tested ?? null)} months tested, threshold |z| ≥ ${ml.anomaly_detection.z_threshold ?? NA})`
            : ''}
          .
        </p>
      )}
    </Section>
  );
}
