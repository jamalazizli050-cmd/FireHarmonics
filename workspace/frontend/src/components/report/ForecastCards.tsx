import { FlaskConical, ShieldCheck } from 'lucide-react';
import type { Prediction } from '../../api/types';
import { fmtPercent, fmtYearMonth, humanize } from '../../lib/format';
import { Badge } from '../ui/primitives';

/**
 * Forecast presentation contract:
 *  - XGBoost (pooled) is shown prominently but ALWAYS as experimental / not validated, using
 *    prediction.model_comparison.xgboost_pooled exactly as returned (never relabelled as the selected forecast).
 *  - The seasonal baseline is shown as the comparison, and the backend's selected method is named explicitly.
 *  - Missing probabilities render as "not issued", never 0 %.
 */
export function ExperimentalXgbCard({
  prediction,
  compact,
}: {
  prediction: Prediction | null | undefined;
  compact?: boolean;
}) {
  const xgb = prediction?.model_comparison?.xgboost_pooled ?? null;
  const has = xgb && xgb.probability !== null && xgb.probability !== undefined;
  const validated = xgb?.validated_for_this_location_and_lead === true;
  return (
    <section
      className={`forecast-card forecast-card--xgb ${compact ? 'forecast-card--compact' : ''}`}
      aria-labelledby={compact ? undefined : 'xgb-title'}
      data-testid="xgb-card"
    >
      <header className="forecast-card__head">
        <FlaskConical aria-hidden="true" size={compact ? 16 : 20} />
        <h3 id={compact ? undefined : 'xgb-title'}>Experimental XGBoost Forecast</h3>
      </header>
      <div className="forecast-card__badges">
        <Badge tone="fire">Experimental</Badge>
        {!validated && <Badge tone="danger">Not validated</Badge>}
        <Badge tone="warn">May be inaccurate</Badge>
      </div>
      {has ? (
        <>
          <p
            className="forecast-card__value"
            aria-label={`XGBoost probability ${fmtPercent(xgb!.probability)}`}
          >
            {fmtPercent(xgb!.probability)}
          </p>
          <p className="forecast-card__caption">
            Probability of ≥ 1 satellite hotspot detection
            {prediction?.target_month ? ` in ${fmtYearMonth(prediction.target_month)}` : ''}
          </p>
          <p className="forecast-card__note">
            {xgb!.used
              ? 'Selected by the backend for this location and lead.'
              : 'Not used by the backend: pooled XGBoost did not pass the held-out validation test here. Shown for comparison only.'}
          </p>
        </>
      ) : (
        <>
          <p className="forecast-card__value forecast-card__value--na">Unavailable</p>
          <p className="forecast-card__note">
            The backend did not return an XGBoost probability for this analysis
            {prediction?.explanation ? `: ${prediction.explanation}` : '.'}
          </p>
        </>
      )}
    </section>
  );
}

export function BaselineCard({
  prediction,
  compact,
  radiusKm,
}: {
  prediction: Prediction | null | undefined;
  compact?: boolean;
  radiusKm?: number;
}) {
  const base = prediction?.model_comparison?.seasonal_baseline ?? null;
  const selectedIsBaseline = prediction?.method === 'seasonal_baseline';
  const value = base?.probability ?? (selectedIsBaseline ? prediction?.probability : null);
  return (
    <section
      className={`forecast-card forecast-card--baseline ${compact ? 'forecast-card--compact' : ''}`}
      data-testid="baseline-card"
    >
      <header className="forecast-card__head">
        <ShieldCheck aria-hidden="true" size={compact ? 16 : 20} />
        <h3>Seasonal Baseline</h3>
      </header>
      <div className="forecast-card__badges">
        {selectedIsBaseline || base?.used ? (
          <Badge tone="ok">Selected by backend</Badge>
        ) : (
          <Badge>Comparison</Badge>
        )}
        {(base?.variant || prediction?.baseline_variant) && (
          <Badge tone="info">{humanize(base?.variant ?? prediction?.baseline_variant)}</Badge>
        )}
      </div>
      <p className="forecast-card__value">
        {value === null || value === undefined ? 'Not issued' : fmtPercent(value)}
      </p>
      <p className="forecast-card__caption">
        Same-calendar-month climatology of the {radiusKm ?? 25} km disc
      </p>
    </section>
  );
}

export function methodLabel(method: string | null | undefined) {
  if (method === 'seasonal_baseline') return 'Seasonal baseline';
  if (method === 'xgboost_pooled') return 'Pooled XGBoost';
  if (method === 'unavailable') return 'No forecast issued';
  return humanize(method);
}
