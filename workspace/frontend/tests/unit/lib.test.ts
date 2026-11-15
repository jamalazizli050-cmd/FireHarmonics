import { describe, expect, it } from 'vitest';
import { daysBetween, presetRange, validatePeriod } from '../../src/lib/period';
import { formatCoordinates, parseCoordinates } from '../../src/lib/geocode';
import { fmtNumber, fmtPercent, fmtYearMonth, monthName, NA, stageLabel } from '../../src/lib/format';
import { groupSources, sourceLabel } from '../../src/lib/sources';
import { contactConfigured, sendContactMessage } from '../../src/lib/contact';
import { parseLocationParams } from '../../src/state/location';
import { FIRMS_SOURCES } from '../../src/api/types';

const NOW = new Date('2026-11-15T12:00:00Z');

describe('period presets', () => {
  it('builds inclusive UTC ranges', () => {
    expect(presetRange('last_30', NOW)).toEqual({ start: '2026-10-17', end: '2026-11-15' });
    expect(daysBetween('2026-10-17', '2026-11-15')).toBe(30);
    expect(presetRange('custom', NOW)).toBeNull();
  });
  it('validates custom periods against the API limits', () => {
    expect(validatePeriod('2026-01-01', '2026-02-01', NOW)).toBeNull();
    expect(validatePeriod('2026-02-01', '2026-01-01', NOW)).toMatch(/on or before/);
    expect(validatePeriod('2026-01-01', '2026-12-01', NOW)).toMatch(/future/);
    expect(validatePeriod('2010-01-01', '2026-01-01', NOW)).toMatch(/3650/);
    expect(validatePeriod('', '2026-01-01', NOW)).toMatch(/both dates/);
  });
});

describe('coordinates', () => {
  it('parses typed coordinates and rejects invalid ones', () => {
    expect(parseCoordinates('37.7, -119.5')).toMatchObject({ latitude: 37.7, longitude: -119.5 });
    expect(parseCoordinates('-12.5 30')).toMatchObject({ latitude: -12.5, longitude: 30 });
    expect(parseCoordinates('95, 10')).toBeNull();
    expect(parseCoordinates('10, 190')).toBeNull();
    expect(parseCoordinates('Yosemite')).toBeNull();
  });
  it('formats hemispheres', () => {
    expect(formatCoordinates(-33.9, 151.2, 1)).toBe('33.9° S, 151.2° E');
  });
  it('reads the location from the URL', () => {
    expect(parseLocationParams(new URLSearchParams('lat=45&lon=10&name=X'))).toEqual({ latitude: 45, longitude: 10, name: 'X' });
    expect(parseLocationParams(new URLSearchParams('lat=91&lon=10'))).toBeNull();
    expect(parseLocationParams(new URLSearchParams('name=X'))).toBeNull();
  });
});

describe('formatting never turns missing values into zero', () => {
  it('renders null as the NA marker', () => {
    expect(fmtNumber(null)).toBe(NA);
    expect(fmtPercent(undefined)).toBe(NA);
    expect(fmtPercent(0)).toBe('0.0 %');
    expect(fmtPercent(0.0412)).toBe('4.1 %');
  });
  it('formats months and stages', () => {
    expect(fmtYearMonth('2026-12')).toBe('December 2026');
    expect(monthName('Jul')).toBe('July');
    expect(monthName(9)).toBe('September');
    expect(stageLabel('ml_download')).toMatch(/S-NPP/);
    expect(stageLabel('something_new')).toBe('something new');
  });
});

describe('sources', () => {
  it('keeps instruments separate', () => {
    const groups = groupSources(FIRMS_SOURCES);
    expect(groups.map((g) => g.label)).toEqual(['Raw VIIRS (375 m)', 'Raw MODIS (1 km)', 'Landsat OLI (30 m, US/Canada)']);
    expect(groups[1].items).toEqual(['MODIS_NRT', 'MODIS_SP']);
    expect(sourceLabel('VIIRS_NOAA20_NRT')).toBe('VIIRS · NOAA-20 · NRT');
  });
});

describe('contact delivery', () => {
  const payload = { firstName: 'A', lastName: 'B', email: 'a@example.com', message: 'Hello there team' };
  it('never reports success when the service is not configured', async () => {
    expect(contactConfigured()).toBe(false);
    const r = await sendContactMessage(payload);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe('unconfigured');
  });
  it('drops honeypot submissions without claiming success', async () => {
    const r = await sendContactMessage({ ...payload, website: 'http://spam.example' });
    expect(r).toMatchObject({ ok: false, reason: 'spam' });
  });
});
