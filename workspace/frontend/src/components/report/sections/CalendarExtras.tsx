import { Activity, CalendarRange } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { CalendarMonth, CurrentConditions, SeasonTiming, UnifiedCalendar } from '../../../api/types';
import { fmtDate, fmtNumber, fmtPercent, fmtYearMonth, humanize, MONTH_ABBR, NA } from '../../../lib/format';
import { Badge, Notice } from '../../ui/primitives';
import { usePalette } from './DescriptiveSections';

/* Everything here renders values from ml_analysis.unified_calendar / current_conditions as returned by the API. */

function heat(value: number | null, max: number, ramp: string[]) {
  if (value === null) return undefined;
  if (value <= 0 || max <= 0) return ramp[0];
  const t = Math.log1p(value) / Math.log1p(max);
  return ramp[1 + Math.min(ramp.length - 2, Math.floor(t * (ramp.length - 1)))];
}

/** 2002–present MODIS+VIIRS calendar (Aqua-MODIS scale). MODIS months carry a marker; the 2012 sensor seam is drawn. */
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
  return (
    <div className="heatmap-wrap">
      <div className="heatmap" role="grid" aria-label="Harmonized monthly detections by year, 2002 to present" data-testid="combined-heatmap">
        <div className="heatmap__row heatmap__row--head" role="row">
          <span role="columnheader" className="heatmap__year" />
          {MONTH_ABBR.map((m) => (
            <span key={m} role="columnheader" className="heatmap__mhead">
              {m}
            </span>
          ))}
        </div>
        {byYear.map(([year, cells]) => (
          <div key={year} className={`heatmap__row ${year === '2012' ? 'heatmap__row--seam' : ''}`} role="row">
            <span role="rowheader" className="heatmap__year">
              {year}
            </span>
            {cells.map((c, i) => {
              if (!c || c.value === null) {
                const label = `${MONTH_ABBR[i]} ${year}: no data`;
                return (
                  <span key={i} role="gridcell" className="heatmap__cell heatmap__cell--unobserved" aria-label={label} title={label} />
                );
              }
              const sensor = c.source === 'modis_aqua' ? 'Aqua MODIS' : 'S-NPP VIIRS scaled to MODIS';
              const label = `${fmtYearMonth(c.month)}: ${fmtNumber(c.value, c.value < 10 ? 1 : 0)} (${sensor})`;
              return (
                <button
                  key={i}
                  type="button"
                  role="gridcell"
                  className={`heatmap__cell ${c.source === 'modis_aqua' ? 'heatmap__cell--modis' : ''} ${selected?.month === c.month ? 'is-selected' : ''}`}
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
          <span className="heat-legend__ramp" style={{ background: `linear-gradient(90deg, ${pal.heat.join(',')})` }} />
          <span>{fmtNumber(max, max < 10 ? 1 : 0)}</span>
        </div>
        <p className="heat-legend__unobs">
          <span className="heatmap__cell heatmap__cell--modis heatmap__cell--legend" aria-hidden="true" /> Aqua MODIS (2002–2011)
        </p>
        <p className="heat-legend__unobs">
          <span className="heatmap__cell heatmap__cell--legend" style={{ background: pal.heat[4] }} aria-hidden="true" /> S-NPP VIIRS ×{' '}
          {fmtNumber(cal.ratio ?? null, 3)} (from 2012)
        </p>
        <p className="heat-legend__unobs">
          <span className="heatmap__cell heatmap__cell--unobserved heatmap__cell--legend" aria-hidden="true" /> No data (not zero)
        </p>
        <div className="heatmap-detail" aria-live="polite">
          {selected ? (
            <dl>
              <dt>Month</dt>
              <dd>{fmtYearMonth(selected.month)}</dd>
              <dt>Harmonized</dt>
              <dd>
                {fmtNumber(selected.value, 2)}
                {selected.interval_95 ? ` (95 %: ${fmtNumber(selected.interval_95[0], 2)}–${fmtNumber(selected.interval_95[1], 2)})` : ''}
              </dd>
              <dt>Source</dt>
              <dd>{selected.source === 'modis_aqua' ? 'Aqua MODIS' : 'S-NPP VIIRS × ratio'}</dd>
              <dt>Aqua MODIS</dt>
              <dd>{fmtNumber(selected.modis_aqua)}</dd>
              <dt>S-NPP VIIRS</dt>
              <dd>{fmtNumber(selected.viirs_snpp)}</dd>
            </dl>
          ) : (
            <p className="muted">Select a month to see both sensors’ raw counts.</p>
          )}
        </div>
      </div>
    </div>
  );
}

/** Leave-one-year-out check of the local MODIS/VIIRS ratio (annual totals on 2012–2021). */
export function CalibrationValidation({ cal }: { cal: UnifiedCalendar }) {
  const pal = usePalette();
  const v = cal.validation;
  if (cal.status !== 'ok' || !v) return null;
  const axis = { stroke: pal.axis, tick: { fill: pal.axis, fontSize: 12 } };
  return (
    <div className="calibration" data-testid="calibration-validation">
      <h4 className="subhead">MODIS–VIIRS harmonization for this circle</h4>
      <div className="forecast-card__badges">
        <Badge tone="warn">Local calibration · experimental</Badge>
        {v.improves_on_raw_viirs === true && <Badge tone="ok">Beats raw VIIRS in validation</Badge>}
        {v.improves_on_raw_viirs === false && <Badge tone="danger">No better than raw VIIRS</Badge>}
        {v.monthly_correlation_modis_vs_viirs !== null && v.monthly_correlation_modis_vs_viirs < 0.5 && (
          <Badge tone="warn">Weak month-to-month agreement — use with care</Badge>
        )}
        {(cal.overlap?.modis_aqua_detections ?? 0) < 100 && <Badge tone="warn">Few overlap detections</Badge>}
      </div>
      <dl className="metric-grid metric-grid--compact">
        <div className="metric">
          <dt className="metric__label">MODIS / VIIRS ratio</dt>
          <dd className="metric__value">{fmtNumber(cal.ratio ?? null, 3)}</dd>
          <dd className="metric__sub">
            95 % CI {cal.ratio_ci95 ? `${fmtNumber(cal.ratio_ci95[0], 3)}–${fmtNumber(cal.ratio_ci95[1], 3)}` : NA} (year bootstrap)
          </dd>
        </div>
        <div className="metric">
          <dt className="metric__label">Overlap {cal.overlap?.start} → {cal.overlap?.end}</dt>
          <dd className="metric__value">{fmtNumber(cal.overlap?.modis_aqua_detections ?? null)} / {fmtNumber(cal.overlap?.viirs_snpp_detections ?? null)}</dd>
          <dd className="metric__sub">Aqua MODIS / S-NPP VIIRS detections</dd>
        </div>
        <div className="metric">
          <dt className="metric__label">Annual error, calibrated</dt>
          <dd className="metric__value">{fmtPercent(v.median_abs_pct_error_annual, 0)}</dd>
          <dd className="metric__sub">
            vs {fmtPercent(v.median_abs_pct_error_annual_raw_viirs, 0)} for raw VIIRS (median, {v.years_tested} held-out years)
          </dd>
        </div>
        <div className="metric">
          <dt className="metric__label">Monthly correlation</dt>
          <dd className="metric__value">{fmtNumber(v.monthly_correlation_modis_vs_viirs, 2)}</dd>
          <dd className="metric__sub">MODIS vs VIIRS, overlap months</dd>
        </div>
      </dl>
      <figure className="chart-card">
        <figcaption>Held-out check: MODIS observed vs VIIRS converted with a ratio fitted without that year</figcaption>
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={v.by_year} maxBarSize={36} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={pal.grid} vertical={false} />
            <XAxis dataKey="year" {...axis} />
            <YAxis {...axis} width={48} />
            <Tooltip contentStyle={{ background: 'var(--surface-strong)', border: '1px solid var(--line-strong)', borderRadius: 10 }} />
            <Legend wrapperStyle={{ color: pal.axis, fontSize: 12 }} />
            <Bar dataKey="modis_observed" name="Aqua MODIS observed" fill={pal.secondary} radius={[4, 4, 0, 0]} />
            <Line dataKey="predicted" name="VIIRS × held-out ratio" stroke={pal.primary} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
        <p className="fineprint">{v.method}. {cal.label}</p>
      </figure>
      <ul className="warn-list">
        {(cal.limitations ?? []).map((l) => (
          <li key={l} className="warn-list__limitation">
            {l}
          </li>
        ))}
      </ul>
    </div>
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
    <section className={`season-card ${compact ? 'season-card--compact' : ''}`} data-testid="season-card" aria-label="Fire season">
      <header className="forecast-card__head">
        <CalendarRange aria-hidden="true" size={compact ? 16 : 20} />
        <h3>Fire season</h3>
      </header>
      <p className="season-card__typical">
        Typically <strong>{season.typical.onset}</strong> → <strong>{season.typical.end}</strong>, peak{' '}
        <strong>{season.typical.peak}</strong> ({season.typical.length_months} months)
      </p>
      {cur?.onset_status && (
        <p className="season-card__now">
          <Badge tone={cur.onset_status === 'as_usual' ? 'info' : 'fire'}>{ONSET_TEXT[cur.onset_status] ?? humanize(cur.onset_status)}</Badge>
        </p>
      )}
      {cur && !compact && (
        <p className="fineprint">
          This fire-year (from {fmtYearMonth(cur.fire_year_start)}): {fmtNumber(cur.detections_so_far, 1)} detections so far vs a typical{' '}
          {fmtNumber(cur.typical_detections_by_now, 1)} by now (Aqua-MODIS scale){cur.onset ? `; onset reached in ${cur.onset}` : ''}.
        </p>
      )}
      {!compact && <p className="fineprint">{season.definition}</p>}
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

export function CurrentConditionsCard({ now, compact }: { now: CurrentConditions | undefined; compact?: boolean }) {
  if (!now) return null;
  const label = now.category ? NOW_TEXT[now.category] : undefined;
  return (
    <section className={`now-card ${compact ? 'now-card--compact' : ''}`} data-testid="now-card" aria-label="Current conditions">
      <header className="forecast-card__head">
        <Activity aria-hidden="true" size={compact ? 16 : 20} />
        <h3>Now · last {now.window?.days ?? 14} days</h3>
      </header>
      {now.status === 'ok' ? (
        <>
          <p className="now-card__value">
            {fmtNumber(now.detections ?? null)} <span>{now.detections === 1 ? 'detection' : 'detections'}</span>
            {label && <Badge tone={label.tone}>{label.text}</Badge>}
          </p>
          <p className="fineprint">
            {now.explanation} Percentile {fmtNumber(now.percentile ?? null, 0)}. {now.sensor}; reference: {now.reference}.
            {now.window ? ` Window ${fmtDate(now.window.start)} – ${fmtDate(now.window.end)}.` : ''}
          </p>
          {!compact && (
            <ul className="warn-list">
              {(now.limitations ?? []).map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <p className="muted">{now.explanation ?? `Current conditions: ${humanize(now.status)}`}</p>
      )}
    </section>
  );
}
