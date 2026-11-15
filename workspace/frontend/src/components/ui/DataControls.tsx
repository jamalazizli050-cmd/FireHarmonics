import { useId, useState } from 'react';
import type { FirmsSource } from '../../api/types';
import {
  PERIOD_PRESETS,
  presetRange,
  todayUtc,
  validatePeriod,
  type PeriodPreset,
  type PeriodSelection,
} from '../../lib/period';
import { SOURCE_INFO, sourceLongLabel } from '../../lib/sources';
import { RADIUS_OPTIONS_KM } from '../../state/location';
import InfoTip from './InfoTip';

export type DataView = 'raw' | 'harmonized' | 'normalized';

export interface AnalysisSettings {
  source: FirmsSource;
  view: DataView;
  period: PeriodSelection;
  radiusKm: number;
}

export interface HarmonizedAvailability {
  /** FireLens regional harmonized series (five training regions). */
  available: boolean;
  reason: string;
  /** MODIS+VIIRS continuity chain of this analysis (ml_analysis.unified_calendar): ready, not yet run, or not possible. */
  chain: 'ok' | 'pending' | 'preparing' | 'unavailable';
  /** Relative activity index from the API (`views.normalized`), offered where no harmonized series exists. */
  normalizedAvailable: boolean;
}

interface Props {
  settings: AnalysisSettings;
  onChange: (next: AnalysisSettings) => void;
  sources: readonly FirmsSource[];
  harmonized: HarmonizedAvailability;
  disabled?: boolean;
  layout: 'panel' | 'report';
}

const HARMONIZED = '__harmonized__';
const CHAIN_SUFFIX: Record<HarmonizedAvailability['chain'], string> = {
  ok: '',
  pending: ' — after the analysis',
  preparing: ' — archives downloading',
  unavailable: ' — not available here',
};
/**
 * The NASA FIRMS products offered: VIIRS on NOAA-20 (S-NPP delivery ended on 2026-11-02) and MODIS Terra/Aqua,
 * both near real-time so the default recent periods have data.
 */
export const MAIN_SOURCES: readonly FirmsSource[] = ['VIIRS_NOAA20_NRT', 'MODIS_NRT'];

/**
 * Satellite-source and time-period controls. The same component (bound to the same state) is rendered
 * in the globe side panel and in the expanded report, so both stay synchronised.
 */
function CustomPeriod({ settings, onChange, disabled }: Pick<Props, 'settings' | 'onChange' | 'disabled'>) {
  const [customStart, setCustomStart] = useState(settings.period.start);
  const [customEnd, setCustomEnd] = useState(settings.period.end);
  const [customError, setCustomError] = useState<string | null>(null);
  const applyCustom = () => {
    const err = validatePeriod(customStart, customEnd);
    setCustomError(err);
    if (!err) onChange({ ...settings, period: { preset: 'custom', start: customStart, end: customEnd } });
  };
  return (
    <div className="custom-period">
      <label>
        <span>From</span>
        <input
          type="date"
          value={customStart}
          max={todayUtc()}
          disabled={disabled}
          onChange={(e) => setCustomStart(e.target.value)}
        />
      </label>
      <label>
        <span>To</span>
        <input
          type="date"
          value={customEnd}
          max={todayUtc()}
          disabled={disabled}
          onChange={(e) => setCustomEnd(e.target.value)}
        />
      </label>
      <button type="button" className="button button--small" onClick={applyCustom} disabled={disabled}>
        Apply
      </button>
      {customError && (
        <p className="field__error" role="alert">
          {customError}
        </p>
      )}
    </div>
  );
}

export default function DataControls({ settings, onChange, sources, harmonized, disabled, layout }: Props) {
  const id = useId();

  // The main sources are always listed; any other current source (e.g. from an older link) is kept visible so the
  // select never shows a value it does not contain.
  const offered = [...new Set<FirmsSource>([...MAIN_SOURCES, settings.source])].filter(
    (s) => MAIN_SOURCES.includes(s) || sources.includes(s),
  );
  const selectValue = settings.view === 'harmonized' ? HARMONIZED : settings.source;
  const info = SOURCE_INFO[settings.source];

  const onSource = (value: string) => {
    if (value === HARMONIZED) onChange({ ...settings, view: 'harmonized' });
    else onChange({ ...settings, view: 'raw', source: value as FirmsSource });
  };

  const sourceHelp =
    settings.view === 'harmonized'
      ? `Aqua MODIS and S-NPP / NOAA-20 VIIRS on one Aqua-MODIS scale, 2002 to now (continuity chain, experimental; validated on held-out years in the report). The statistics for the selected period still use ${settings.source}.`
      : settings.view === 'normalized'
        ? `Relative activity index (each month ÷ the series’ average month). Mathematical normalization, not a harmonization and not validated. The descriptive statistics still use ${settings.source}.`
        : `${sourceLongLabel(settings.source)}.${info?.note ? ` ${info.note}` : ''}`;
  const radiusHelp =
    'Circle around the selected location. The forecast model was trained on 10–100 km circles.';
  const periodHelp = `${settings.period.start} → ${settings.period.end} (UTC). Near real-time products hold recent data and standard products the archive; the backend checks NASA availability before downloading.`;
  // In the report the explanations sit behind an (i); the side panel keeps them as text.
  const compact = layout === 'report';
  const labelRow = (htmlFor: string, text: string, help: string | null) =>
    compact ? (
      <div className="field__label-row">
        <label htmlFor={htmlFor}>{text}</label>
        {help && <InfoTip text={help} />}
      </div>
    ) : (
      <label htmlFor={htmlFor}>{text}</label>
    );

  const onPreset = (preset: PeriodPreset) => {
    if (preset === 'custom') {
      onChange({ ...settings, period: { ...settings.period, preset: 'custom' } });
      return;
    }
    const r = presetRange(preset)!;
    onChange({ ...settings, period: { preset, ...r } });
  };

  return (
    <div className={`data-controls data-controls--${layout}`}>
      <div className="field">
        {labelRow(`${id}-source`, 'Satellite data', compact ? null : sourceHelp)}
        <select
          id={`${id}-source`}
          value={selectValue}
          disabled={disabled}
          onChange={(e) => onSource(e.target.value)}
          aria-describedby={compact ? undefined : `${id}-source-help`}
        >
          {/* The main NASA FIRMS VIIRS and MODIS products, and the MODIS+VIIRS harmonized record of this analysis. */}
          {offered.map((s) => (
            <option key={s} value={s}>
              {SOURCE_INFO[s].instrument} · {SOURCE_INFO[s].platforms} ·{' '}
              {SOURCE_INFO[s].processing === 'near real-time' ? 'Near real-time' : 'Standard archive'}
            </option>
          ))}
          <option value={HARMONIZED} disabled={harmonized.chain !== 'ok'}>
            Harmonized · MODIS + VIIRS (2002–now){CHAIN_SUFFIX[harmonized.chain]}
          </option>
        </select>
        {!compact && (
          <p id={`${id}-source-help`} className="field__help">
            {sourceHelp}
          </p>
        )}
      </div>

      <div className="field">
        {labelRow(`${id}-radius`, 'Area of interest', radiusHelp)}
        <select
          id={`${id}-radius`}
          value={settings.radiusKm}
          disabled={disabled}
          onChange={(e) => onChange({ ...settings, radiusKm: Number(e.target.value) })}
        >
          {RADIUS_OPTIONS_KM.map((r) => (
            <option key={r} value={r}>
              {r} km radius around the point
            </option>
          ))}
        </select>
        {!compact && <p className="field__help">{radiusHelp}</p>}
      </div>

      <div className="field">
        {labelRow(`${id}-period`, 'Time period', periodHelp)}
        <select
          id={`${id}-period`}
          value={settings.period.preset}
          disabled={disabled}
          onChange={(e) => onPreset(e.target.value as PeriodPreset)}
        >
          {PERIOD_PRESETS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
        {settings.period.preset === 'custom' ? (
          // keyed so the draft dates reset whenever the applied period changes elsewhere (panel <-> report)
          <CustomPeriod
            key={`${settings.period.start}|${settings.period.end}`}
            settings={settings}
            onChange={onChange}
            disabled={disabled}
          />
        ) : compact ? null : (
          <p className="field__help">{periodHelp}</p>
        )}
      </div>
    </div>
  );
}
