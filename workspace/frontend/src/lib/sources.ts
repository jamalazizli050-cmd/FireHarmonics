import type { FirmsSource } from '../api/types';

/**
 * Display metadata for the FIRMS products the backend accepts. Mirrors
 * FireDetectionAPP/satellites.py (_SOURCES). NASA FIRMS is the distribution service;
 * MODIS / VIIRS / OLI are the instruments.
 */
export interface SourceInfo {
  instrument: 'MODIS' | 'VIIRS' | 'OLI';
  platforms: string;
  processing: 'near real-time' | 'standard (archive)';
  resolution: string;
  note?: string;
}

export const SOURCE_INFO: Record<FirmsSource, SourceInfo> = {
  VIIRS_NOAA20_NRT: { instrument: 'VIIRS', platforms: 'NOAA-20', processing: 'near real-time', resolution: '375 m' },
  VIIRS_NOAA20_SP: { instrument: 'VIIRS', platforms: 'NOAA-20', processing: 'standard (archive)', resolution: '375 m' },
  VIIRS_NOAA21_NRT: { instrument: 'VIIRS', platforms: 'NOAA-21', processing: 'near real-time', resolution: '375 m' },
  VIIRS_NOAA21_SP: { instrument: 'VIIRS', platforms: 'NOAA-21', processing: 'standard (archive)', resolution: '375 m' },
  VIIRS_SNPP_NRT: {
    instrument: 'VIIRS',
    platforms: 'Suomi NPP',
    processing: 'near real-time',
    resolution: '375 m',
    note: 'S-NPP observation delivery ended on 2026-11-02.',
  },
  VIIRS_SNPP_SP: {
    instrument: 'VIIRS',
    platforms: 'Suomi NPP',
    processing: 'standard (archive)',
    resolution: '375 m',
    note: 'S-NPP observation delivery ended on 2026-11-02.',
  },
  MODIS_NRT: { instrument: 'MODIS', platforms: 'Terra / Aqua', processing: 'near real-time', resolution: '1 km' },
  MODIS_SP: { instrument: 'MODIS', platforms: 'Terra / Aqua', processing: 'standard (archive)', resolution: '1 km' },
  LANDSAT_NRT: {
    instrument: 'OLI',
    platforms: 'Landsat 8 / 9',
    processing: 'near real-time',
    resolution: '30 m',
    note: 'Covers the US and Canada only.',
  },
};

export function sourceLabel(source: string): string {
  const info = SOURCE_INFO[source as FirmsSource];
  if (!info) return source;
  const proc = info.processing === 'near real-time' ? 'NRT' : 'SP';
  return `${info.instrument} · ${info.platforms} · ${proc}`;
}

export function sourceLongLabel(source: string): string {
  const info = SOURCE_INFO[source as FirmsSource];
  if (!info) return source;
  return `Raw ${info.instrument} (${info.platforms}), ${info.processing}, ${info.resolution} — via NASA FIRMS`;
}

/** Grouping used by the source selectors (raw instruments are never merged). */
export function groupSources(sources: readonly FirmsSource[]) {
  return [
    { label: 'Raw VIIRS (375 m)', items: sources.filter((s) => SOURCE_INFO[s]?.instrument === 'VIIRS') },
    { label: 'Raw MODIS (1 km)', items: sources.filter((s) => SOURCE_INFO[s]?.instrument === 'MODIS') },
    { label: 'Landsat OLI (30 m, US/Canada)', items: sources.filter((s) => SOURCE_INFO[s]?.instrument === 'OLI') },
  ].filter((g) => g.items.length > 0);
}
