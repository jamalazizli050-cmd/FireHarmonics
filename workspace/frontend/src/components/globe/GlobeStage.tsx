/* eslint-disable react-hooks/immutability -- three.js scene objects are mutated imperatively inside useFrame, which is the intended React Three Fiber pattern. */
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { useA11y } from '../../state/a11y';
import { createEarth, GLOBE_RADIUS } from './createEarth';

export type GlobeMode = 'home' | 'globe' | 'hidden';

export interface GlobeMarker {
  lat: number;
  lng: number;
}

/** A place picked on the globe: the clicked point and the country it falls in. */
export interface GlobePick {
  lat: number;
  lng: number;
  country: string;
}

interface Props {
  mode: GlobeMode;
  marker: GlobeMarker | null;
  /** Called when the user clicks a country (or presses Enter: the country at the centre of the view). */
  onPick?: (pick: GlobePick) => void;
  /** Width in CSS px reserved on the right for the analysis panel (globe page, desktop). */
  panelWidth: number;
}

const FOV = 20;
const CAMERA_Z = 3000;
const DEG = Math.PI / 180;
const CLICK_SLOP_PX = 6;

/** Pixel layout of the sphere (centre and radius) for each mode, measured from the mockups (1600 x 1040). */
function layoutFor(mode: GlobeMode, w: number, h: number, panelWidth: number) {
  const portrait = w < 820;
  if (mode === 'globe') {
    if (portrait) {
      const r = Math.min(w * 0.42, h * 0.24);
      return { cx: w / 2, cy: Math.max(r + 92, h * 0.31), r };
    }
    const avail = w - panelWidth;
    const r = Math.min(h * 0.385, avail * 0.36);
    return { cx: Math.min(w * 0.4, avail * 0.54), cy: h * 0.567, r };
  }
  // home (also used as the parked layout when hidden)
  if (portrait) {
    const r = w * 0.62;
    return { cx: w / 2, cy: h * 0.74, r };
  }
  const r = Math.min(w * 0.336, h * 0.517);
  return { cx: w / 2, cy: h * 0.5 + r * 0.64, r };
}

function Earth({ mode, marker, panelWidth, onPick, reduceMotion }: Props & { reduceMotion: boolean }) {
  const { size, invalidate, camera } = useThree();
  const onPickRef = useRef(onPick);
  useEffect(() => {
    onPickRef.current = onPick;
  }, [onPick]);
  const [ready, setReady] = useState(false);
  const earth = useMemo(() => createEarth(), []);
  const group = useRef<THREE.Group>(null);

  // interaction / animation state kept outside React for per-frame updates
  const anim = useRef({
    yaw: -20 * DEG,
    pitch: 18 * DEG,
    targetYaw: -20 * DEG,
    targetPitch: 18 * DEG,
    zoom: 1,
    targetZoom: 1,
    cx: 0,
    cy: 0,
    r: 0,
    borders: 0,
    tint: 1,
    initialised: false,
    dragging: false,
  });

  // The stage lives for the whole session (it persists across routes), so the earth is never disposed.
  useEffect(() => {
    earth.globe.onGlobeReady(() => {
      setReady(true);
      invalidate();
    });
  }, [earth, invalidate]);

  // marker + fly-to
  useEffect(() => {
    const a = anim.current;
    // the country containing the selected place glows red
    earth.setHighlight(marker ? earth.countryAt(marker.lat, marker.lng) : null);
    if (marker) {
      earth.globe.objectsData([{ lat: marker.lat, lng: marker.lng }]);
      a.targetYaw = -marker.lng * DEG;
      a.targetPitch = THREE.MathUtils.clamp(marker.lat, -75, 75) * DEG;
      // unwrap yaw so the globe takes the short way round
      while (a.targetYaw - a.yaw > Math.PI) a.targetYaw -= 2 * Math.PI;
      while (a.targetYaw - a.yaw < -Math.PI) a.targetYaw += 2 * Math.PI;
      a.targetZoom = 1;
    } else {
      earth.globe.objectsData([]);
      a.targetZoom = 1;
    }
    invalidate();
  }, [marker, earth, invalidate]);

  useEffect(() => {
    earth.globe.polygonStrokeColor(() => (mode === 'globe' ? '#0a1530' : false));
    if (mode !== 'globe') {
      anim.current.targetZoom = 1;
      anim.current.targetPitch = 18 * DEG;
    }
    invalidate();
  }, [mode, earth, invalidate]);

  // pointer / wheel / keyboard interaction on the canvas element (globe mode only)
  const { gl } = useThree();
  useEffect(() => {
    const el = gl.domElement;
    if (mode !== 'globe') return;
    let lastX = 0;
    let lastY = 0;
    const pointers = new Map<number, { x: number; y: number }>();
    let pinchDist = 0;
    // a click is a single-pointer press that barely moved; anything else is a drag/pinch
    let downAt: { x: number; y: number } | null = null;
    let moved = 0;
    const emit = (lat: number, lng: number) => {
      const country = earth.countryAt(lat, lng);
      const name = (country?.properties as { name?: string } | null)?.name;
      if (name && onPickRef.current) onPickRef.current({ lat, lng, country: name });
    };
    const pickAt = (clientX: number, clientY: number) => {
      const g = group.current;
      if (!g) return;
      const rect = el.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((clientX - rect.left) / rect.width) * 2 - 1,
        -((clientY - rect.top) / rect.height) * 2 + 1,
      );
      const ray = new THREE.Raycaster();
      ray.setFromCamera(ndc, camera);
      const sphere = new THREE.Sphere(g.getWorldPosition(new THREE.Vector3()), GLOBE_RADIUS * g.scale.x);
      const hit = ray.ray.intersectSphere(sphere, new THREE.Vector3());
      if (!hit) return;
      const { lat, lng } = earth.globe.toGeoCoords(g.worldToLocal(hit));
      emit(lat, lng);
    };
    const onDown = (e: PointerEvent) => {
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      downAt = pointers.size === 1 ? { x: e.clientX, y: e.clientY } : null;
      moved = 0;
      el.setPointerCapture(e.pointerId);
      lastX = e.clientX;
      lastY = e.clientY;
      anim.current.dragging = true;
      if (pointers.size === 2) {
        const [p1, p2] = [...pointers.values()];
        pinchDist = Math.hypot(p1.x - p2.x, p1.y - p2.y);
      }
    };
    const onMove = (e: PointerEvent) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (downAt) moved = Math.max(moved, Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y));
      const a = anim.current;
      if (pointers.size === 2) {
        const [p1, p2] = [...pointers.values()];
        const d = Math.hypot(p1.x - p2.x, p1.y - p2.y);
        if (pinchDist > 0) a.targetZoom = THREE.MathUtils.clamp(a.targetZoom * (d / pinchDist), 0.6, 2.6);
        pinchDist = d;
      } else {
        const k = 0.0045 / a.targetZoom;
        a.targetYaw += (e.clientX - lastX) * k;
        a.targetPitch = THREE.MathUtils.clamp(a.targetPitch + (e.clientY - lastY) * k, -80 * DEG, 80 * DEG);
      }
      lastX = e.clientX;
      lastY = e.clientY;
      invalidate();
    };
    const onUp = (e: PointerEvent) => {
      const click = e.type === 'pointerup' && downAt !== null && pointers.size === 1 && moved < CLICK_SLOP_PX;
      pointers.delete(e.pointerId);
      if (click) pickAt(e.clientX, e.clientY);
      if (pointers.size === 0) downAt = null;
      if (pointers.size < 2) pinchDist = 0;
      if (pointers.size === 0) anim.current.dragging = false;
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const a = anim.current;
      a.targetZoom = THREE.MathUtils.clamp(a.targetZoom * Math.exp(-e.deltaY * 0.0012), 0.6, 2.6);
      invalidate();
    };
    const onKey = (e: KeyboardEvent) => {
      const a = anim.current;
      const step = 8 * DEG;
      let handled = true;
      if (e.key === 'ArrowLeft') a.targetYaw -= step;
      else if (e.key === 'ArrowRight') a.targetYaw += step;
      else if (e.key === 'ArrowUp') a.targetPitch = Math.max(a.targetPitch - step, -80 * DEG);
      else if (e.key === 'ArrowDown') a.targetPitch = Math.min(a.targetPitch + step, 80 * DEG);
      else if (e.key === '+' || e.key === '=') a.targetZoom = Math.min(a.targetZoom * 1.15, 2.6);
      else if (e.key === '-' || e.key === '_') a.targetZoom = Math.max(a.targetZoom / 1.15, 0.6);
      else if (e.key === 'Enter') {
        // the point facing the viewer (inverse of the fly-to: yaw = -lng, pitch = lat)
        const lng = ((((-a.yaw / DEG + 180) % 360) + 360) % 360) - 180;
        emit(a.pitch / DEG, lng);
      } else handled = false;
      if (handled) {
        e.preventDefault();
        invalidate();
      }
    };
    el.addEventListener('pointerdown', onDown);
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('pointercancel', onUp);
    el.addEventListener('wheel', onWheel, { passive: false });
    const keyTarget = el.closest<HTMLElement>('.globe-stage') ?? el;
    keyTarget.addEventListener('keydown', onKey);
    return () => {
      el.removeEventListener('pointerdown', onDown);
      el.removeEventListener('pointermove', onMove);
      el.removeEventListener('pointerup', onUp);
      el.removeEventListener('pointercancel', onUp);
      el.removeEventListener('wheel', onWheel);
      keyTarget.removeEventListener('keydown', onKey);
    };
  }, [mode, gl, invalidate, camera, earth]);

  useFrame((state, delta) => {
    const g = group.current;
    if (!g) return;
    const a = anim.current;
    const dt = Math.min(delta, 0.05);
    const target = layoutFor(mode, size.width, size.height, panelWidth);
    const instant = reduceMotion || !a.initialised;
    const ease = instant ? 1 : 1 - Math.exp(-dt * 3.2);

    if (mode === 'home' && !reduceMotion) a.targetYaw += dt * 0.06; // slow decorative rotation
    a.cx += (target.cx - a.cx) * ease;
    a.cy += (target.cy - a.cy) * ease;
    a.r += (target.r - a.r) * ease;
    a.yaw += (a.targetYaw - a.yaw) * (instant ? 1 : 1 - Math.exp(-dt * 4));
    a.pitch += (a.targetPitch - a.pitch) * (instant ? 1 : 1 - Math.exp(-dt * 4));
    a.zoom += (a.targetZoom - a.zoom) * (instant ? 1 : 1 - Math.exp(-dt * 5));
    const bordersTarget = mode === 'globe' ? 1 : 0;
    a.borders += (bordersTarget - a.borders) * (reduceMotion ? 1 : 1 - Math.exp(-dt * 2.5));
    a.tint += ((mode === 'globe' ? 0 : 1) - a.tint) * (reduceMotion ? 1 : 1 - Math.exp(-dt * 2.5));
    a.initialised = true;

    // pixel -> world conversion at z = 0
    const worldPerPx = (2 * CAMERA_Z * Math.tan((FOV * DEG) / 2)) / size.height;
    g.position.set((a.cx - size.width / 2) * worldPerPx, -(a.cy - size.height / 2) * worldPerPx, 0);
    const scale = (a.r * a.zoom * worldPerPx) / GLOBE_RADIUS;
    g.scale.setScalar(scale);
    g.rotation.set(a.pitch, a.yaw, 0, 'XYZ');

    // Home: deep-blue photographic look; Globe: natural colours with blue country caps (mockup 02)
    const mat = earth.globeMaterial;
    // three-globe sets `color = null` once the texture has loaded; restore a colour we can tint.
    if (!mat.color) mat.color = new THREE.Color(1, 1, 1);
    mat.color.setRGB(1 - a.tint * 0.85, 1 - a.tint * 0.67, 1 - a.tint * 0.2);
    mat.emissive.setRGB(0.0, 0.012 * a.tint, 0.05 * a.tint);
    earth.capMaterial.opacity = 0.5 * a.borders;
    earth.sideMaterial.opacity = 0.85 * a.borders;
    earth.capMaterial.visible = earth.sideMaterial.visible = a.borders > 0.01;
    const pulse = reduceMotion ? 1 : 1 + 0.18 * Math.sin(state.clock.elapsedTime * 2.6);
    earth.hiCapMaterial.opacity = (0.62 + (pulse - 1) * 0.5) * a.borders;
    earth.hiSideMaterial.opacity = 0.9 * a.borders;
    earth.hiCapMaterial.visible = earth.hiSideMaterial.visible = a.borders > 0.01;
    earth.pinGlow.scale.setScalar(14 * pulse);

    const settling =
      Math.abs(target.cx - a.cx) > 0.3 ||
      Math.abs(target.r - a.r) > 0.3 ||
      Math.abs(a.targetYaw - a.yaw) > 1e-4 ||
      Math.abs(a.targetPitch - a.pitch) > 1e-4 ||
      Math.abs(a.targetZoom - a.zoom) > 1e-4 ||
      Math.abs(bordersTarget - a.borders) > 0.005;
    if (settling || (mode === 'home' && !reduceMotion) || (marker && !reduceMotion)) state.invalidate();
  });

  return (
    <group ref={group} visible={ready}>
      <primitive object={earth.globe} />
    </group>
  );
}

export default function GlobeStage({ mode, marker, panelWidth, onPick }: Props) {
  const { reduceMotion } = useA11y();
  const visible = mode !== 'hidden';
  return (
    <div
      className={`globe-stage globe-stage--${mode}`}
      aria-hidden={mode !== 'globe'}
      data-testid="globe-stage"
      tabIndex={mode === 'globe' ? 0 : -1}
      role={mode === 'globe' ? 'application' : undefined}
      aria-roledescription={mode === 'globe' ? 'interactive globe' : undefined}
      aria-label={
        mode === 'globe'
          ? 'Interactive 3D globe. Drag or use the arrow keys to rotate; scroll, pinch or use plus and minus to zoom. Click a country, or press Enter for the country in the centre, to analyse that place; the search field in the panel also chooses locations.'
          : undefined
      }
    >
      <Canvas
        frameloop={visible ? 'demand' : 'never'}
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true, preserveDrawingBuffer: false }}
        camera={{ fov: FOV, near: 10, far: 10000, position: [0, 0, CAMERA_Z] }}
        style={{ touchAction: mode === 'globe' ? 'none' : 'auto' }}
      >
        <ambientLight intensity={mode === 'home' ? 0.8 : 2.3} />
        <directionalLight
          position={mode === 'home' ? [-2200, 1900, 1500] : [-600, 900, 3000]}
          intensity={mode === 'home' ? 4.6 : 1.6}
        />
        <Earth
          mode={mode}
          marker={marker}
          panelWidth={panelWidth}
          onPick={onPick}
          reduceMotion={reduceMotion}
        />
      </Canvas>
    </div>
  );
}
