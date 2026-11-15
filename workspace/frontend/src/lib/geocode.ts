/**
 * Location search. Uses the Open-Meteo geocoding API (GeoNames-based, no key, designed for
 * search-as-you-type). Coordinates typed directly ("37.7, -119.5") are accepted without a lookup.
 * Only the typed search text is sent to the geocoder.
 */
export interface Place {
  id: string;
  name: string;
  detail: string;
  latitude: number;
  longitude: number;
}

const GEOCODER_URL =
  (import.meta.env.VITE_GEOCODER_URL as string | undefined) || 'https://geocoding-api.open-meteo.com/v1/search';

export function parseCoordinates(text: string): Place | null {
  const m = /^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$/.exec(text);
  if (!m) return null;
  const latitude = Number(m[1]);
  const longitude = Number(m[2]);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) return null;
  return {
    id: `coord:${latitude},${longitude}`,
    name: formatCoordinates(latitude, longitude),
    detail: 'Coordinates (WGS 84)',
    latitude,
    longitude,
  };
}

export function formatCoordinates(lat: number, lon: number, digits = 4): string {
  const ns = lat >= 0 ? 'N' : 'S';
  const ew = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(digits)}° ${ns}, ${Math.abs(lon).toFixed(digits)}° ${ew}`;
}

interface OpenMeteoResult {
  id: number;
  name: string;
  latitude: number;
  longitude: number;
  country?: string;
  admin1?: string;
  admin2?: string;
  feature_code?: string;
}

export class GeocodeError extends Error {}

export async function searchPlaces(query: string, signal?: AbortSignal): Promise<Place[]> {
  const direct = parseCoordinates(query);
  if (direct) return [direct];
  const q = query.trim();
  if (q.length < 2) return [];
  const url = new URL(GEOCODER_URL);
  url.searchParams.set('name', q);
  url.searchParams.set('count', '7');
  url.searchParams.set('language', 'en');
  url.searchParams.set('format', 'json');
  let res: Response;
  try {
    res = await fetch(url, { signal });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new GeocodeError('The place search service could not be reached.');
  }
  if (!res.ok) throw new GeocodeError(`The place search service returned HTTP ${res.status}.`);
  const body = (await res.json()) as { results?: OpenMeteoResult[] };
  return (body.results ?? []).map((r) => ({
    id: `om:${r.id}`,
    name: r.name,
    detail: [r.admin1, r.country].filter(Boolean).join(', '),
    latitude: r.latitude,
    longitude: r.longitude,
  }));
}
