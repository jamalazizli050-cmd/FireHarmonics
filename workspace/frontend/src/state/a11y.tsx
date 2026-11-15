import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export type MotionPref = 'system' | 'reduce' | 'full';

export interface A11ySettings {
  textScale: number; // 1 = 100 %
  highContrast: boolean;
  motion: MotionPref;
  colorBlind: boolean;
}

interface A11yContextValue extends A11ySettings {
  /** Effective reduced-motion state (setting, or the operating-system preference when set to "system"). */
  reduceMotion: boolean;
  update: (patch: Partial<A11ySettings>) => void;
  reset: () => void;
}

export const TEXT_SCALES = [0.9, 1, 1.15, 1.3, 1.5] as const;
const DEFAULTS: A11ySettings = { textScale: 1, highContrast: false, motion: 'system', colorBlind: false };
const STORAGE_KEY = 'fireharmonics.a11y.v1';

function load(): A11ySettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<A11ySettings>;
    return {
      textScale: TEXT_SCALES.includes(parsed.textScale as (typeof TEXT_SCALES)[number])
        ? (parsed.textScale as number)
        : 1,
      highContrast: Boolean(parsed.highContrast),
      motion: parsed.motion === 'reduce' || parsed.motion === 'full' ? parsed.motion : 'system',
      colorBlind: Boolean(parsed.colorBlind),
    };
  } catch {
    return DEFAULTS;
  }
}

function useSystemReducedMotion(): boolean {
  const query = '(prefers-reduced-motion: reduce)';
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : false,
  );
  useEffect(() => {
    if (!window.matchMedia) return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);
  return matches;
}

const A11yContext = createContext<A11yContextValue | null>(null);

export function A11yProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<A11ySettings>(load);
  const systemReduce = useSystemReducedMotion();
  const reduceMotion = settings.motion === 'reduce' || (settings.motion === 'system' && systemReduce);

  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--text-scale', String(settings.textScale));
    root.dataset.contrast = settings.highContrast ? 'high' : 'normal';
    root.dataset.motion = reduceMotion ? 'reduce' : 'full';
    root.dataset.palette = settings.colorBlind ? 'cb' : 'fire';
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
      /* storage unavailable: settings still apply for this visit */
    }
  }, [settings, reduceMotion]);

  const update = useCallback((patch: Partial<A11ySettings>) => setSettings((s) => ({ ...s, ...patch })), []);
  const reset = useCallback(() => setSettings(DEFAULTS), []);
  const value = useMemo(() => ({ ...settings, reduceMotion, update, reset }), [settings, reduceMotion, update, reset]);
  return <A11yContext.Provider value={value}>{children}</A11yContext.Provider>;
}

export function useA11y(): A11yContextValue {
  const ctx = useContext(A11yContext);
  if (!ctx) throw new Error('useA11y must be used inside <A11yProvider>');
  return ctx;
}

/** Chart colours. The colour-blind palette uses Okabe–Ito hues and a cividis-like sequential ramp. */
export function chartPalette(colorBlind: boolean, highContrast: boolean) {
  if (colorBlind) {
    return {
      primary: '#E69F00',
      secondary: '#56B4E9',
      tertiary: '#009E73',
      accent: '#CC79A7',
      negative: '#D55E00',
      positive: '#0072B2',
      grid: highContrast ? '#9aa3b5' : 'rgba(255,255,255,0.08)',
      axis: highContrast ? '#ffffff' : '#9aa3b5',
      heat: ['#1f2433', '#00204d', '#31446b', '#666970', '#958f78', '#cbba69', '#ffea46'],
      unobserved: '#3a3f4c',
    };
  }
  return {
    primary: '#ff7a1a',
    secondary: '#7cb2ff',
    tertiary: '#ffd166',
    accent: '#ff3d2e',
    negative: '#ff4d3a',
    positive: '#5aa9ff',
    grid: highContrast ? '#9aa3b5' : 'rgba(255,255,255,0.08)',
    axis: highContrast ? '#ffffff' : '#9aa3b5',
    heat: ['#22262f', '#3b2a22', '#6b2f17', '#a8380f', '#e2520c', '#ff7a1a', '#ffb347'],
    unobserved: '#3a3f4c',
  };
}
