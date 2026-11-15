/** Formatting helpers. `null`/`undefined` always render as an explicit "not available" marker, never as 0. */
export const NA = '—';

export function fmtNumber(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NA;
  return v.toLocaleString('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPercent(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NA;
  return `${(v * 100).toFixed(digits)} %`;
}

export function fmtSignedPercent(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NA;
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)} %`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

export const MONTH_ABBR = MONTHS;

export function monthName(m: number | string | null | undefined): string {
  if (m === null || m === undefined) return NA;
  if (typeof m === 'number') return MONTHS_LONG[m - 1] ?? NA;
  const idx = MONTHS.indexOf(m);
  return idx >= 0 ? MONTHS_LONG[idx] : m;
}

/** "2026-12" -> "December 2026" */
export function fmtYearMonth(ym: string | null | undefined): string {
  if (!ym) return NA;
  const [y, m] = ym.split('-');
  const idx = Number(m) - 1;
  return MONTHS_LONG[idx] ? `${MONTHS_LONG[idx]} ${y}` : ym;
}

export function fmtDate(d: string | null | undefined): string {
  if (!d) return NA;
  const t = Date.parse(d.length === 10 ? `${d}T00:00:00Z` : d);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}

export function fmtDateTime(d: string | null | undefined): string {
  if (!d) return NA;
  const t = Date.parse(d);
  if (Number.isNaN(t)) return d;
  return new Date(t).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
    timeZoneName: 'short',
  });
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return NA;
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${s % 60} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

/** Turns machine identifiers such as "insufficient_data" into readable text. */
export function humanize(v: string | null | undefined): string {
  if (!v) return NA;
  return v.replace(/_/g, ' ').replace(/\b0\.05\b/, '0.05');
}

const STAGES: Record<string, string> = {
  queued: 'Queued',
  checking_availability: 'Checking NASA FIRMS data availability',
  downloading: 'Downloading FIRMS detections',
  waiting_for_nasa: 'Waiting for NASA rate limit',
  parsing: 'Parsing detections',
  analyzing: 'Computing descriptive statistics',
  loading_archive: 'Loading imported archive',
  ml_starting: 'Starting ML engine',
  ml_plan: 'Planning S-NPP VIIRS history download',
  ml_wait_download_slot: 'Waiting for a download slot',
  ml_download: 'Downloading S-NPP VIIRS history',
  ml_process: 'Processing satellite history',
  ml_load: 'Loading history',
  ml_patterns: 'Discovering patterns (seasonality, trends, anomalies, hotspots)',
  ml_predict: 'Computing forecast',
  ml_finalize: 'Finalising ML analysis',
  assembling: 'Assembling report',
  completed: 'Completed',
};

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return NA;
  return STAGES[stage] ?? humanize(stage);
}
