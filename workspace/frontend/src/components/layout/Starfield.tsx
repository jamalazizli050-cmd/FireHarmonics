import { useMemo } from 'react';
import starfieldUrl from '@design/assets/starfield-background.jpeg';

/** Deterministic pseudo-random generator so the star layout is stable between renders and screenshots. */
function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Irregular dense star tile (generated once) for the black Interactive Globe background. */
function denseStarTexture(): string | null {
  try {
    const size = 512;
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const rand = mulberry32(42);
    for (let i = 0; i < 900; i++) {
      const r = rand();
      const radius = r < 0.9 ? 0.35 + rand() * 0.45 : 0.8 + rand() * 0.7;
      ctx.globalAlpha = 0.25 + rand() * 0.7;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(rand() * size, rand() * size, radius, 0, Math.PI * 2);
      ctx.fill();
    }
    return canvas.toDataURL('image/png');
  } catch {
    return null;
  }
}

interface Props {
  variant: 'navy' | 'deep' | 'home';
}

/**
 * Shared page background: the supplied navy starfield texture plus a light layer of individually
 * twinkling stars (CSS animation, disabled in reduced-motion mode). The "deep" variant reproduces the
 * black, dense star field of the Interactive Globe mockup.
 */
export default function Starfield({ variant }: Props) {
  const stars = useMemo(() => {
    const rand = mulberry32(variant === 'deep' ? 7 : 3);
    const count = variant === 'deep' ? 220 : 90;
    return Array.from({ length: count }, (_, i) => ({
      id: i,
      left: rand() * 100,
      top: rand() * 100,
      size: rand() < 0.85 ? 1 + rand() * 1.2 : 2 + rand() * 1.4,
      delay: rand() * 6,
      duration: 2.8 + rand() * 4.5,
      dim: 0.25 + rand() * 0.6,
    }));
  }, [variant]);

  const dense = useMemo(() => (variant === 'deep' ? denseStarTexture() : null), [variant]);

  return (
    <div className={`starfield starfield--${variant}`} aria-hidden="true">
      <div className="starfield__texture" style={{ backgroundImage: `url(${starfieldUrl})` }} />
      <div className="starfield__glow" style={dense ? ({ ['--dense-stars' as string]: `url(${dense})` }) : undefined} />
      <div className="starfield__stars">
        {stars.map((s) => (
          <span
            key={s.id}
            className="star"
            style={{
              left: `${s.left}%`,
              top: `${s.top}%`,
              width: `${s.size}px`,
              height: `${s.size}px`,
              animationDelay: `${s.delay}s`,
              animationDuration: `${s.duration}s`,
              ['--dim' as string]: s.dim,
            }}
          />
        ))}
      </div>
    </div>
  );
}
