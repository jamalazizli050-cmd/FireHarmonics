import { useId, useState } from 'react';
import type { FirmsSource } from '../../api/types';
import { PERIOD_PRESETS, presetRange, todayUtc, validatePeriod, type PeriodPreset, type PeriodSelection } from '../../lib/period';
import { groupSources, SOURCE_INFO, sourceLongLabel } from '../../lib/sources';
import { RADIUS_OPTIONS_KM } from '../../state/location';

export type DataView = 'raw' | 'harmonized' | 'normalized';

export interface AnalysisSettings {
  source: FirmsSource;
  view: DataView;
  period: PeriodSelection;
  radiusKm: number;
}

export interface HarmonizedAvailability {
  available: boolean;
  reason: string;
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
const NORMALIZED = '__normalized__';

/**
 * Satellite-source and time-period controls. The same component (bound to the same state) is rendered
 * in the globe side panel and in the expanded report, so both stay synchronised.
 */
function CustomPeriod({
  settings,
  onChange,
  disabled,
}: Pick<Props, 'settings' | 'onChange' | 'disabled'>) {
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
        <input type="date" value={customEnd} max={todayUtc()} disabled={disabled} onChange={(e) => setCustomEnd(e.target.value)} />
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

  const selectValue = settings.view === 'harmonized' ? HARMONIZED : settings.view === 'normalized' ? NORMALIZED : settings.source;
  const info = SOURCE_INFO[settings.source];

  const onSource = (value: string) => {
    if (value === HARMONIZED) onChange({ ...settings, view: 'harmonized' });
    else if (value === NORMALIZED) onChange({ ...settings, view: 'normalized' });
    else onChange({ ...settings, view: 'raw', source: value as FirmsSource });
  };

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
        <label htmlFor={`${id}-source`}>Satellite data</label>
        <select
          id={`${id}-source`}
          value={selectValue}
          disabled={disabled}
          onChange={(e) => onSource(e.target.value)}
          aria-describedby={`${id}-source-help`}
        >
          {groupSources(sources).map((g) => (
            <optgroup key={g.label} label={g.label}>
              {g.items.map((s) => (
                <option key={s} value={s}>
                  {SOURCE_INFO[s].instrument} · {SOURCE_INFO[s].platforms} ·{' '}
                  {SOURCE_INFO[s].processing === 'near real-time' ? 'Near real-time' : 'Standard archive'}
                </option>
              ))}
            </optgroup>
          ))}
          <optgroup label="Derived product">
            <option value={HARMONIZED} disabled={!harmonized.available}>
              Harmonized MODIS-equivalent (regional only){harmonized.available ? '' : ' — not available here'}
            </option>
            <option value={NORMALIZED} disabled={!harmonized.normalizedAvailable}>
              Normalized index — not harmonized{harmonized.normalizedAvailable ? '' : ' (needs 12 observed months)'}
            </option>
          </optgroup>
        </select>
        <p id={`${id}-source-help`} className="field__help">
          {settings.view === 'harmonized' ? (
            <>Regional MODIS-equivalent monthly series (2012–2021). Not validated at the local scale. The descriptive statistics still use {settings.source}.</>
          ) : settings.view === 'normalized' ? (
            <>Relative activity index (each month ÷ the series’ average month). Mathematical normalization, not a harmonization and not validated. The descriptive statistics still use {settings.source}.</>
          ) : (
            <>
              {sourceLongLabel(settings.source)}.{info?.note ? ` ${info.note}` : ''}
            </>
          )}
          {!harmonized.available && settings.view === 'raw' && layout === 'report' ? ` Harmonized: ${harmonized.reason}` : ''}
        </p>
      </div>

      <div className="field">
        <label htmlFor={`${id}-radius`}>Area of interest</label>
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
        <p className="field__help">Circle around the selected location. The forecast model was trained on 10–100 km circles.</p>
      </div>

      <div className="field">
        <label htmlFor={`${id}-period`}>Time period</label>
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
        ) : (
          <p className="field__help">
            {settings.period.start} → {settings.period.end} (UTC). Near real-time products hold recent data and standard
            products the archive; the backend checks NASA availability before downloading.
          </p>
        )}
      </div>
    </div>
  );
}
