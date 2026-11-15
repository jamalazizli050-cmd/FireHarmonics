import { Activity, CalendarRange } from 'lucide-react';
import { useMemo, useState } from 'react';
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type {
  CalendarMonth,
  ChainLink,
  CurrentConditions,
  ForwardTest,
  SeasonTiming,
  UnifiedCalendar,
} from '../../../api/types';
import { fmtDate, fmtNumber, fmtPercent, fmtYearMonth, humanize, MONTH_ABBR, NA } from '../../../lib/format';
import InfoTip from '../../ui/InfoTip';
import { Badge, Notice } from '../../ui/primitives';
import { usePalette } from './DescriptiveSections';

/* Everything here renders values from ml_analysis.unified_calendar / current_conditions as returned by the API. */

function heat(value: number | null, max: number, ramp: string[]) {
  if (value === null) return undefined;
  if (value <= 0 || max <= 0) return ramp[0];
  const t = Math.log1p(value) / Math.log1p(max);
  return ramp[1 + Math.min(ramp.length - 2, Math.floor(t * (ramp.length - 1)))];
}

const SOURCE_TEXT: Record<string, string> = {
  modis_aqua: 'Aqua MODIS',
  viirs_snpp_scaled: 'S-NPP VIIRS scaled to MODIS',
  viirs_noaa20_scaled: 'NOAA-20 VIIRS scaled to MODIS via S-NPP',
};

const ratioText = (l: ChainLink | undefined) =>
  l?.ratio ? `day × ${fmtNumber(l.ratio.D, 3)}, night × ${fmtNumber(l.ratio.N, 3)}` : NA;

/** 2002–present calendar on the Aqua-MODIS scale. Sensor of each month, its interval and unusual months are shown. */
export function CombinedHeatmap({ cal }: { cal: UnifiedCalendar }) {
  const pal = usePalette();
  const [selected, setSelected] = useState<CalendarMonth | null>(null);
  const rows = cal.monthly ?? [];
  const byYear = useMemo(() => {
    const map = new Map<string, (CalendarMonth | null)[]>();
    for (const m of rows) {
      const [y, mm] = m.month.split('-');
      if (!map.has(y)) map.set(y, Array(12).fill(null));
      map.get(y)![Number(mm) - 1] = m;
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [rows]);
  const max = Math.max(0, ...rows.map((m) => m.value ?? 0));
  const hasN20 = rows.some((m) => m.source === 'viirs_noaa20_scaled');
  const hasHigh = rows.some((m) => m.anomaly === 'high');
  const [l1, l2] = cal.chain ?? [];
  const base = cal.baseline_years
    ? `${cal.baseline_years[0]}–${cal.baseline_years[1]}`
    : 'the baseline years';
  return (
    <div className="heatmap-wrap">
      <div
        className="heatmap"
        role="grid"
        aria-label="Harmonized monthly burning activity by year, 2002 to present"
        data-testid="combined-heatmap"
      >
        <div className="heatmap__row heatmap__row--head" role="row">
          <span role="columnheader" className="heatmap__year" />
          {MONTH_ABBR.map((m) => (
            <span key={m} role="columnheader" className="heatmap__mhead">
              {m}
            </span>
          ))}
        </div>
        {byYear.map(([year, cells]) => (
          <div
            key={year}
            className={`heatmap__row ${year === '2012' ? 'heatmap__row--seam' : ''}`}
            role="row"
          >
            <span role="rowheader" className="heatmap__year">
              {year}
            </span>
            {cells.map((c, i) => {
              if (!c || c.value === null) {
                const label = `${MONTH_ABBR[i]} ${year}: no data`;
                return (
                  <span
                    key={i}
                    role="gridcell"
                    className="heatmap__cell heatmap__cell--unobserved"
                    aria-label={label}
                    title={label}
                  />
                );
              }
              const sensor = SOURCE_TEXT[c.source ?? ''] ?? 'unknown sensor';
              const label = `${fmtYearMonth(c.month)}: ${fmtNumber(c.value, c.value < 10 ? 1 : 0)} (${sensor})${c.anomaly === 'high' ? ', unusually high' : ''}`;
              const cls = [
                'heatmap__cell',
                c.source === 'modis_aqua' ? 'heatmap__cell--modis' : '',
                c.source === 'viirs_noaa20_scaled' ? 'heatmap__cell--n20' : '',
                c.anomaly === 'high' ? 'heatmap__cell--high' : '',
                selected?.month === c.month ? 'is-selected' : '',
              ].join(' ');
              return (
                <button
                  key={i}
                  type="button"
                  role="gridcell"
                  className={cls}
                  style={{ background: heat(c.value, max, pal.heat) }}
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
          <span>{fmtNumber(max, max < 10 ? 1 : 0)}</span>
        </div>
        <p className="fineprint">{cal.unit ?? 'Aqua-MODIS scale'}</p>
        <p className="heat-legend__unobs">
          <span className="heatmap__cell heatmap__cell--modis heatmap__cell--legend" aria-hidden="true" />{' '}
          Aqua MODIS (2002–2011)
        </p>
        <p className="heat-legend__unobs">
          <span
            className="heatmap__cell heatmap__cell--legend"
            style={{ background: pal.heat[4] }}
            aria-hidden="true"
          />{' '}
          S-NPP VIIRS, {ratioText(l1)} (from 2012)
        </p>
        {hasN20 && (
          <p className="heat-legend__unobs">
            <span
              className="heatmap__cell heatmap__cell--n20 heatmap__cell--legend"
              style={{ background: pal.heat[4] }}
              aria-hidden="true"
            />{' '}
            NOAA-20 VIIRS via S-NPP ({ratioText(l2)})
          </p>
        )}
        {hasHigh && (
          <p className="heat-legend__unobs">
            <span
              className="heatmap__cell heatmap__cell--high heatmap__cell--legend"
              style={{ background: pal.heat[3] }}
              aria-hidden="true"
            />{' '}
            Unusually high for the month
          </p>
        )}
        <p className="heat-legend__unobs">
          <span
            className="heatmap__cell heatmap__cell--unobserved heatmap__cell--legend"
            aria-hidden="true"
          />{' '}
          No data (not zero)
        </p>
        <div className="heatmap-detail" aria-live="polite">
          {selected ? (
            <dl>
              <dt>Month</dt>
              <dd>{fmtYearMonth(selected.month)}</dd>
              <dt>Harmonized</dt>
              <dd>
                {fmtNumber(selected.value, 1)}
                {selected.interval_90
                  ? ` (90 %: ${fmtNumber(selected.interval_90[0], 1)}–${fmtNumber(selected.interval_90[1], 1)})`
                  : ''}
              </dd>
              <dt>Source</dt>
              <dd>{SOURCE_TEXT[selected.source ?? ''] ?? NA}</dd>
              <dt>vs {base}</dt>
              <dd>
                {selected.percentile === null
                  ? NA
                  : `percentile ${fmtNumber(selected.percentile, 0)}, median ${fmtNumber(selected.baseline_median, 1)} (${(selected.diff_from_median ?? 0) >= 0 ? '+' : ''}${fmtNumber(selected.diff_from_median, 1)})`}
                {selected.anomaly === 'high' ? ' · unusually high' : ''}
              </dd>
              <dt>Aqua MODIS</dt>
              <dd>{fmtNumber(selected.modis_aqua)}</dd>
              <dt>S-NPP VIIRS</dt>
              <dd>{fmtNumber(selected.viirs_snpp)}</dd>
              <dt>NOAA-20 VIIRS</dt>
              <dd>{fmtNumber(selected.viirs_noaa20)}</dd>
            </dl>
          ) : (
            <p className="muted">
              Select a month to see each sensor’s raw active cells and how unusual the month is.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

const METHOD_TEXT = {
  continuity_chain: 'Continuity chain (this model)',
  pixel_ratio: 'Previous method: pixel ratio',
  raw_target: 'Raw newer sensor, unscaled',
} as const;

function TestTable({ t, title }: { t: ForwardTest; title: string }) {
  if (t.status !== 'ok' || !t.methods) {
    return (
      <p className="fineprint">
        <strong>{title}:</strong>{' '}
        {t.status === 'too_little_activity'
          ? `too little activity in ${t.test_years?.join(', ') ?? 'the test years'} to score at this location; the regional ratio carries the calibration.`
          : (t.explanation ?? humanize(t.status))}
      </p>
    );
  }
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={title}>
      <table className="data-table">
        <caption>
          {title}: fitted on {t.train_years?.[0]}–{t.train_years?.at(-1)}, scored on {t.test_years?.[0]}–
          {t.test_years?.at(-1)}
        </caption>
        <thead>
          <tr>
            <th scope="col">Method</th>
            <th scope="col">Annual error (median)</th>
            <th scope="col">Anomaly ranking (Spearman)</th>
          </tr>
        </thead>
        <tbody>
          {(Object.keys(METHOD_TEXT) as (keyof typeof METHOD_TEXT)[]).map((m) => {
            const s = t.methods?.[m];
            return (
              <tr key={m}>
                <th scope="row">{METHOD_TEXT[m]}</th>
                <td>{fmtPercent(s?.median_abs_pct_error_annual ?? null, 0)}</td>
                <td>{fmtNumber(s?.anomaly_rank_correlation ?? null, 2)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The continuity chain for this circle: coefficients, partial pooling, and held-out-in-time validation. */
export function CalibrationValidation({ cal }: { cal: UnifiedCalendar }) {
  const pal = usePalette();
  const v = cal.validation;
  if (cal.status !== 'ok' || !v) return null;
  const axis = { stroke: pal.axis, tick: { fill: pal.axis, fontSize: 12 } };
  const a = v.aqua_snpp;
  const chainErr = a.methods?.continuity_chain?.median_abs_pct_error_annual ?? null;
  const rawErr = a.methods?.raw_target?.median_abs_pct_error_annual ?? null;
  return (
    <div className="calibration" data-testid="calibration-validation">
      <h4 className="subhead">
        MODIS–VIIRS continuity chain for this circle{' '}
        {cal.unit_definition && (
          <InfoTip text={`${cal.label ?? ''}. ${cal.unit_definition}`} label="About the continuity chain" />
        )}
      </h4>
      <div className="forecast-card__badges">
        <Badge tone="warn">Continuity chain · experimental</Badge>
        {a.status === 'ok' && chainErr !== null && rawErr !== null && chainErr < rawErr && (
          <Badge tone="ok">Beats raw VIIRS on held-out years</Badge>
        )}
        {a.beats_pixel_ratio === true && <Badge tone="ok">Beats the previous pixel ratio</Badge>}
        {a.beats_pixel_ratio === false && (
          <Badge tone="info">No better than the previous pixel ratio here</Badge>
        )}
        {a.status !== 'ok' && <Badge tone="warn">Sparse activity: leans on the regional ratio</Badge>}
      </div>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Chain coefficients">
        <table className="data-table">
          <caption>Coefficients (reference ← newer sensor), fitted per day and night overpass</caption>
          <thead>
            <tr>
              <th scope="col">Link</th>
              <th scope="col">Day (90 %)</th>
              <th scope="col">Night (90 %)</th>
              <th scope="col">Local share (day / night)</th>
              <th scope="col">Regional prior</th>
            </tr>
          </thead>
          <tbody>
            {(cal.chain ?? []).map((l) => (
              <tr key={l.link}>
                <th scope="row">
                  {l.reference} ← {l.target}
                  {l.fit_years?.length ? ` (${l.fit_years[0]}–${l.fit_years.at(-1)})` : ''}
                </th>
                {l.ratio ? (
                  <>
                    <td>
                      {fmtNumber(l.ratio.D, 3)} ({fmtNumber(l.ratio_ci90?.D[0] ?? null, 3)}–
                      {fmtNumber(l.ratio_ci90?.D[1] ?? null, 3)})
                    </td>
                    <td>
                      {fmtNumber(l.ratio.N, 3)} ({fmtNumber(l.ratio_ci90?.N[0] ?? null, 3)}–
                      {fmtNumber(l.ratio_ci90?.N[1] ?? null, 3)})
                    </td>
                    <td>
                      {fmtPercent(l.local_weight?.D ?? null, 0)} / {fmtPercent(l.local_weight?.N ?? null, 0)}
                    </td>
                  </>
                ) : (
                  <td colSpan={3}>{l.explanation ?? 'Not fitted'}</td>
                )}
                <td>
                  {fmtNumber(l.prior.D, 3)} / {fmtNumber(l.prior.N, 3)} ({l.prior_scope.D})
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4 className="subhead">
        Validation held out in time <InfoTip text={`${v.method}.`} label="How the validation works" />
      </h4>
      <TestTable t={a} title="MODIS switched off: Aqua predicted from S-NPP alone" />
      {a.status === 'ok' && a.interval_90_coverage !== undefined && (
        <p className="fineprint">
          The real Aqua value fell inside the 90 % interval in {fmtPercent(a.interval_90_coverage, 0)} of the{' '}
          {a.months_tested} held-out months.
        </p>
      )}
      {a.status === 'ok' && a.by_year && (
        <figure className="chart-card">
          <figcaption>
            Held-out years: Aqua observed vs the chain’s prediction from S-NPP (active 1-km cells)
          </figcaption>
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={a.by_year} maxBarSize={36} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={pal.grid} vertical={false} />
              <XAxis dataKey="year" {...axis} />
              <YAxis {...axis} width={48} />
              <Tooltip
                contentStyle={{
                  background: 'var(--surface-strong)',
                  border: '1px solid var(--line-strong)',
                  borderRadius: 10,
                }}
              />
              <Legend wrapperStyle={{ color: pal.axis, fontSize: 12 }} />
              <Bar
                dataKey="reference"
                name="Aqua MODIS observed"
                fill={pal.secondary}
                radius={[4, 4, 0, 0]}
              />
              <Line
                dataKey="continuity_chain"
                name="Predicted from S-NPP"
                stroke={pal.primary}
                strokeWidth={2}
                dot={{ r: 3 }}
                isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </figure>
      )}
      <TestTable t={v.snpp_n20} title="Bridge: S-NPP predicted from NOAA-20 alone" />
      {v.seam_2012?.harmonized_over_aqua_2012_2013 != null && (
        <p className="fineprint">
          Seam check 2012–2013: harmonized ÷ Aqua = {fmtNumber(v.seam_2012.harmonized_over_aqua_2012_2013, 2)}
          . {v.seam_2012.explanation}
        </p>
      )}
    </div>
  );
}

/** The harmonized monthly record 2002–now (continuity chain) with its 90 % interval, as returned by the API. */
export function HarmonizedSeriesChart({ cal }: { cal: UnifiedCalendar }) {
  const pal = usePalette();
  const data = (cal.monthly ?? []).map((m) => ({
    month: m.month,
    value: m.value,
    band: m.interval_90 ?? (m.value !== null ? [m.value, m.value] : null),
  }));
  const axis = { stroke: pal.axis, tick: { fill: pal.axis, fontSize: 12 } };
  return (
    <figure className="chart-card" data-testid="harmonized-chart">
      <figcaption>
        Harmonized MODIS + VIIRS, {data[0]?.month} → {data.at(-1)?.month} ({cal.unit}); shaded: 90 % interval
      </figcaption>
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={pal.grid} vertical={false} />
          <XAxis dataKey="month" {...axis} minTickGap={40} />
          <YAxis {...axis} width={48} />
          <Tooltip
            contentStyle={{
              background: 'var(--surface-strong)',
              border: '1px solid var(--line-strong)',
              borderRadius: 10,
            }}
          />
          <Area
            dataKey="band"
            name="90 % interval"
            stroke="none"
            fill={pal.primary}
            fillOpacity={0.18}
            isAnimationActive={false}
          />
          <Line
            dataKey="value"
            name="Harmonized"
            stroke={pal.primary}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
            connectNulls={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="fineprint">
        Aqua MODIS before 2012, then S-NPP VIIRS and NOAA-20 VIIRS converted to the Aqua-MODIS scale. Gaps are
        months without data, not zeros. {cal.label}
      </p>
    </figure>
  );
}

const ONSET_TEXT: Record<string, string> = {
  earlier_than_usual: 'Season started earlier than usual',
  later_than_usual: 'Season started later than usual',
  as_usual: 'Season started as usual',
  not_started_later_than_usual: 'Season has not started yet — later than usual',
};

export function SeasonCard({ season, compact }: { season: SeasonTiming | undefined; compact?: boolean }) {
  if (!season) return null;
  if (season.status !== 'ok' || !season.typical) {
    return compact ? null : (
      <Notice tone="info" title="Fire season">
        {season.explanation ?? humanize(season.status)}
      </Notice>
    );
  }
  const cur = season.current;
  return (
    <section
      className={`season-card ${compact ? 'season-card--compact' : ''}`}
      data-testid="season-card"
      aria-label="Fire season"
    >
      <header className="forecast-card__head">
        <CalendarRange aria-hidden="true" size={compact ? 16 : 20} />
        <h3>Fire season</h3>
        {!compact && season.definition && (
          <InfoTip text={season.definition} label="How the season is defined" />
        )}
      </header>
      <p className="season-card__typical">
        Typically <strong>{season.typical.onset}</strong> → <strong>{season.typical.end}</strong>, peak{' '}
        <strong>{season.typical.peak}</strong> ({season.typical.length_months} months)
      </p>
      {cur?.onset_status && (
        <p className="season-card__now">
          <Badge tone={cur.onset_status === 'as_usual' ? 'info' : 'fire'}>
            {ONSET_TEXT[cur.onset_status] ?? humanize(cur.onset_status)}
          </Badge>
        </p>
      )}
      {cur && !compact && (
        <p className="fineprint">
          This fire-year (from {fmtYearMonth(cur.fire_year_start)}): {fmtNumber(cur.detections_so_far, 1)}{' '}
          active cells so far vs a typical {fmtNumber(cur.typical_detections_by_now, 1)} by now (Aqua-MODIS
          scale){cur.onset ? `; onset reached in ${cur.onset}` : ''}.
        </p>
      )}
    </section>
  );
}

const NOW_TEXT: Record<string, { text: string; tone: 'danger' | 'fire' | 'warn' | 'info' | 'ok' }> = {
  highest_on_record: { text: 'Highest on record for these dates', tone: 'danger' },
  well_above_normal: { text: 'Well above normal', tone: 'danger' },
  above_normal: { text: 'Above normal', tone: 'fire' },
  near_normal: { text: 'Near normal', tone: 'info' },
  below_normal: { text: 'Below normal', tone: 'ok' },
  quiet_as_usual: { text: 'Quiet, as usual', tone: 'ok' },
  isolated_detections: { text: 'Isolated detections — usually quiet here', tone: 'info' },
};

export function CurrentConditionsCard({
  now,
  compact,
}: {
  now: CurrentConditions | undefined;
  compact?: boolean;
}) {
  if (!now) return null;
  const label = now.category ? NOW_TEXT[now.category] : undefined;
  return (
    <section
      className={`now-card ${compact ? 'now-card--compact' : ''}`}
      data-testid="now-card"
      aria-label="Current conditions"
    >
      <header className="forecast-card__head">
        <Activity aria-hidden="true" size={compact ? 16 : 20} />
        <h3>Now · last {now.window?.days ?? 14} days</h3>
      </header>
      {now.status === 'ok' ? (
        <>
          <p className="now-card__value">
            {fmtNumber(now.active_cells ?? now.detections ?? null)}{' '}
            <span>
              {(now.active_cells ?? now.detections) === 1 ? 'active 1-km cell' : 'active 1-km cells'}
            </span>
            {label && <Badge tone={label.tone}>{label.text}</Badge>}
          </p>
          <p className="fineprint">
            {now.explanation} Percentile {fmtNumber(now.percentile ?? null, 0)}. {now.sensor}; reference:{' '}
            {now.reference}.
            {now.window ? ` Window ${fmtDate(now.window.start)} – ${fmtDate(now.window.end)}.` : ''}
          </p>
        </>
      ) : (
        <p className="muted">{now.explanation ?? `Current conditions: ${humanize(now.status)}`}</p>
      )}
    </section>
  );
}
