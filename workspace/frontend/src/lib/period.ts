/** Time-period presets for the descriptive block (backend limit: 3650 days, UTC dates). */
export type PeriodPreset = 'last_7' | 'last_30' | 'last_90' | 'last_365' | 'last_1825' | 'last_3650' | 'custom';

export const PERIOD_PRESETS: { id: PeriodPreset; label: string; days?: number }[] = [
  { id: 'last_7', label: 'Last 7 days', days: 7 },
  { id: 'last_30', label: 'Last 30 days', days: 30 },
  { id: 'last_90', label: 'Last 90 days', days: 90 },
  { id: 'last_365', label: 'Last 12 months', days: 365 },
  { id: 'last_1825', label: 'Last 5 years', days: 1825 },
  { id: 'last_3650', label: 'Last 10 years', days: 3650 },
  { id: 'custom', label: 'Custom range' },
];

export const MAX_PERIOD_DAYS = 3650;

export interface PeriodSelection {
  preset: PeriodPreset;
  start: string; // YYYY-MM-DD
  end: string; // YYYY-MM-DD
}

export function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function todayUtc(now = new Date()): string {
  return isoDay(now);
}

export function presetRange(preset: PeriodPreset, now = new Date()): { start: string; end: string } | null {
  const def = PERIOD_PRESETS.find((p) => p.id === preset);
  if (!def?.days) return null;
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - (def.days - 1));
  return { start: isoDay(start), end: isoDay(end) };
}

export function daysBetween(start: string, end: string): number {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000) + 1;
}

/** Returns an error message, or null when the custom period is acceptable for the API. */
export function validatePeriod(start: string, end: string, now = new Date()): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) return 'Enter both dates.';
  if (start > end) return 'The start date must be on or before the end date.';
  if (end > todayUtc(now)) return 'The end date cannot be in the future.';
  if (daysBetween(start, end) > MAX_PERIOD_DAYS) return 'The period can be at most 3650 days (about 10 years).';
  return null;
}

export function defaultPeriod(now = new Date()): PeriodSelection {
  const r = presetRange('last_30', now)!;
  return { preset: 'last_30', ...r };
}
