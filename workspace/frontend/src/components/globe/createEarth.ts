import * as THREE from 'three';
import ThreeGlobe from 'three-globe';
import { feature } from 'topojson-client';
import type { Topology, GeometryCollection } from 'topojson-specification';
import countriesTopo from 'world-atlas/countries-110m.json';

/** Radius of a three-globe sphere in its own units. */
export const GLOBE_RADIUS = 100;

type CountryFeature = GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon, { name?: string }>;

export interface EarthHandles {
  globe: ThreeGlobe;
  globeMaterial: THREE.MeshPhongMaterial;
  capMaterial: THREE.MeshLambertMaterial;
  sideMaterial: THREE.MeshLambertMaterial;
  /** Red cap/side of the country that contains the selected place. */
  hiCapMaterial: THREE.MeshLambertMaterial;
  hiSideMaterial: THREE.MeshLambertMaterial;
  /** Country (Natural Earth 1:110m) containing the point, or null over the sea. */
  countryAt: (lat: number, lng: number) => CountryFeature | null;
  setHighlight: (country: CountryFeature | null) => void;
  strokeMaterial: THREE.LineBasicMaterial;
  pin: THREE.Group;
  pinGlow: THREE.Sprite;
  dispose: () => void;
}

function glowTexture(): THREE.Texture {
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, 'rgba(255,225,215,1)');
  g.addColorStop(0.18, 'rgba(255,60,40,0.95)');
  g.addColorStop(0.45, 'rgba(230,20,10,0.4)');
  g.addColorStop(1, 'rgba(200,0,0,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** Glowing location pin (no radius overlay, by specification). Points along +Z (three-globe objectFacesSurface). */
function createPin(): { pin: THREE.Group; glow: THREE.Sprite } {
  const pin = new THREE.Group();
  const stemGeo = new THREE.CylinderGeometry(0.25, 0.25, 6, 12);
  stemGeo.rotateX(Math.PI / 2);
  stemGeo.translate(0, 0, 3);
  const stem = new THREE.Mesh(
    stemGeo,
    new THREE.MeshBasicMaterial({ color: '#ff6a55', transparent: true, opacity: 0.9 }),
  );
  const head = new THREE.Mesh(
    new THREE.SphereGeometry(1.4, 24, 16),
    new THREE.MeshBasicMaterial({ color: '#ffd9d2' }),
  );
  head.position.set(0, 0, 6.4);
  const glow = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: glowTexture(),
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    }),
  );
  glow.scale.set(14, 14, 1);
  glow.position.set(0, 0, 6.4);
  pin.add(stem, head, glow);
  pin.renderOrder = 10;
  return { pin, glow };
}

/** Even-odd ray casting over every ring (holes included), in longitude/latitude. */
function inRings(rings: GeoJSON.Position[][], lng: number, lat: number) {
  let inside = false;
  for (const ring of rings) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}

function contains(f: CountryFeature, lng: number, lat: number) {
  const g = f.geometry;
  return g.type === 'Polygon'
    ? inRings(g.coordinates, lng, lat)
    : g.coordinates.some((poly) => inRings(poly, lng, lat));
}

export function createEarth(onReady?: () => void): EarthHandles {
  const topo = countriesTopo as unknown as Topology<{ countries: GeometryCollection }>;
  const countries = feature(topo, topo.objects.countries).features.filter(
    (f) => (f.properties as { name?: string } | null)?.name !== 'Antarctica',
  ) as CountryFeature[];

  const capMaterial = new THREE.MeshLambertMaterial({
    color: '#3d8bff',
    transparent: true,
    opacity: 0,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const sideMaterial = new THREE.MeshLambertMaterial({
    color: '#5aa2ff',
    transparent: true,
    opacity: 0,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const strokeMaterial = new THREE.LineBasicMaterial({ color: '#06122b', transparent: true, opacity: 0 });
  const hiCapMaterial = new THREE.MeshLambertMaterial({
    color: '#ff3b2f',
    emissive: '#c81400',
    emissiveIntensity: 0.6,
    transparent: true,
    opacity: 0,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const hiSideMaterial = new THREE.MeshLambertMaterial({
    color: '#ff5a3c',
    emissive: '#a01000',
    transparent: true,
    opacity: 0,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  let highlighted: CountryFeature | null = null;

  const { pin, glow } = createPin();

  const globe = new ThreeGlobe({ animateIn: false, waitForGlobeReady: true })
    .globeImageUrl('/textures/earth-blue-marble.jpg')
    .bumpImageUrl('/textures/earth-topology.png')
    .showAtmosphere(true)
    .atmosphereColor('#3b8cff')
    .atmosphereAltitude(0.2)
    .polygonsData(countries)
    .polygonCapMaterial((d) => (d === highlighted ? hiCapMaterial : capMaterial))
    .polygonSideMaterial((d) => (d === highlighted ? hiSideMaterial : sideMaterial))
    .polygonStrokeColor(() => '#06122b')
    .polygonAltitude((d) => (d === highlighted ? 0.03 : 0.022))
    .polygonsTransitionDuration(0)
    .objectLat('lat')
    .objectLng('lng')
    .objectAltitude(0)
    .objectFacesSurface(true)
    .objectThreeObject(() => pin)
    .objectsData([]);

  if (onReady) globe.onGlobeReady(onReady);

  const globeMaterial = globe.globeMaterial() as THREE.MeshPhongMaterial;
  globeMaterial.bumpScale = 6;
  new THREE.TextureLoader().load('/textures/earth-water.png', (tex) => {
    globeMaterial.specularMap = tex;
    globeMaterial.specular = new THREE.Color('#4a6b99');
    globeMaterial.shininess = 18;
    globeMaterial.needsUpdate = true;
  });

  const countryAt = (lat: number, lng: number) => countries.find((f) => contains(f, lng, lat)) ?? null;
  const setHighlight = (country: CountryFeature | null) => {
    if (country === highlighted) return;
    highlighted = country;
    // re-setting the accessors makes three-globe re-evaluate them for every polygon
    globe
      .polygonCapMaterial((d) => (d === highlighted ? hiCapMaterial : capMaterial))
      .polygonSideMaterial((d) => (d === highlighted ? hiSideMaterial : sideMaterial))
      .polygonAltitude((d) => (d === highlighted ? 0.03 : 0.022));
  };

  // Stroke lines are created by three-globe with their own materials; tint them once they exist.
  const dispose = () => {
    capMaterial.dispose();
    sideMaterial.dispose();
    hiCapMaterial.dispose();
    hiSideMaterial.dispose();
    strokeMaterial.dispose();
    globe.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      mesh.geometry?.dispose?.();
    });
  };

  return {
    globe,
    globeMaterial,
    capMaterial,
    sideMaterial,
    hiCapMaterial,
    hiSideMaterial,
    countryAt,
    setHighlight,
    strokeMaterial,
    pin,
    pinGlow: glow,
    dispose,
  };
}
