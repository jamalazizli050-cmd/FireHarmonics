import type { AnalysisResult } from '../../api/types';
import { fmtNumber, fmtSignedPercent, fmtYearMonth, humanize, monthName, NA } from '../../lib/format';
import { sourceLabel } from '../../lib/sources';
import { Metric, Notice, StatusText } from '../ui/primitives';
import { BaselineCard, ExperimentalXgbCard, methodLabel } from './ForecastCards';
import { CurrentConditionsCard, SeasonCard } from './sections/CalendarExtras';

/** Brief analytical overview in the side panel. Every value is read from result.overview / result.prediction. */
export default function PanelSummary({ result }: { result: AnalysisResult }) {
  const o = result.overview;
  const p = result.prediction;
  return (
    <div className="panel-summary" data-testid="panel-summary">
      <div className="panel-summary__status">
        <span>Result</span>
        <StatusText status={result.status} />
      </div>
      {result.status === 'partial' && (
        <Notice tone="warn" title="Partial result">
          Some components are unavailable; descriptive statistics are still genuine. See the report for details.
        </Notice>
      )}
      <CurrentConditionsCard now={result.ml_analysis.current_conditions} compact />
      <SeasonCard season={result.ml_analysis.unified_calendar?.season} compact />
      <ExperimentalXgbCard prediction={p} compact />
      <BaselineCard prediction={p} compact radiusKm={result.location.radius_km} />
      <p className="panel-summary__method">
        Backend-selected method: <strong>{methodLabel(p.method)}</strong>
        {p.target_month ? ` · target ${fmtYearMonth(p.target_month)}` : ''}
      </p>
      <dl className="metric-grid metric-grid--panel">
        <Metric
          label={`Detections (${sourceLabel(o?.descriptive.source ?? result.source)})`}
          value={fmtNumber(o?.descriptive.detection_count ?? result.detection_count)}
          sub={o?.descriptive.period ? `${o.descriptive.period.start_date ?? NA} → ${o.descriptive.period.end_date ?? NA}` : undefined}
        />
        <Metric
          label="Detections, ML record (S-NPP VIIRS)"
          value={fmtNumber(o?.ml.total_detections_observed_months)}
          sub={o?.ml.period ? `${o.ml.period.start} → ${o.ml.period.end}` : humanize(o?.ml.status)}
        />
        <Metric label="Peak fire month" value={o?.ml.peak_month ? monthName(o.ml.peak_month) : NA} />
        <Metric
          label="Trend"
          value={o?.ml.trend_direction ? humanize(o.ml.trend_direction) : NA}
          sub={
            o?.ml.trend_pct_per_year != null
              ? `${fmtSignedPercent(o.ml.trend_pct_per_year)} / yr${o.ml.trend_significant === false ? ' · not significant' : ''}`
              : undefined
          }
        />
      </dl>
      <p className="panel-summary__harm">
        Harmonization: <StatusText status={o?.harmonization_status ?? result.components?.harmonization} />
      </p>
    </div>
  );
}
