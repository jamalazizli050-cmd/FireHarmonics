import type { Place } from '../lib/geocode';

/** The selected analysis location travels in the URL (/globe?lat=..&lon=..&name=..) so it can be shared and restored. */
export interface SelectedLocation {
  latitude: number;
  longitude: number;
  name: string | null;
}

export function parseLocationParams(params: URLSearchParams): SelectedLocation | null {
  const lat = Number(params.get('lat'));
  const lon = Number(params.get('lon'));
  if (!params.has('lat') || !params.has('lon')) return null;
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  const name = params.get('name');
  return { latitude: lat, longitude: lon, name: name && name.trim() ? name.trim().slice(0, 160) : null };
}

export function locationSearch(place: Place): string {
  const params = new URLSearchParams({
    lat: place.latitude.toFixed(5),
    lon: place.longitude.toFixed(5),
  });
  const label = place.id.startsWith('coord:') ? '' : [place.name, place.detail].filter(Boolean).join(', ');
  if (label) params.set('name', label.slice(0, 160));
  return `?${params.toString()}`;
}

/** Default analysis radius. The forecast model was trained on discs of 10, 25, 50 and 100 km. */
export const ANALYSIS_RADIUS_KM = 25;
export const RADIUS_OPTIONS_KM = [10, 25, 50, 100] as const;
