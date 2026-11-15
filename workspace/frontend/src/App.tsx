import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'motion/react';
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useSearchParams,
} from 'react-router';
import AccessibilityPanel from './components/layout/AccessibilityPanel';
import NavBar from './components/layout/NavBar';
import Starfield from './components/layout/Starfield';
import { A11yProvider, useA11y } from './state/a11y';
import { locationSearch, parseLocationParams } from './state/location';
import HomePage from './pages/HomePage';
import AboutPage from './pages/AboutPage';
import ContactPage from './pages/ContactPage';
import FaqPage from './pages/FaqPage';
import type { GlobeMode, GlobePick } from './components/globe/GlobeStage';

const GlobeStage = lazy(() => import('./components/globe/GlobeStage'));
const GlobePage = lazy(() => import('./pages/GlobePage'));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5 * 60_000 } },
});

/** Width of the right-hand analysis panel on desktop (mockup 02: 410 px of 1600). Mirrors --panel-width in CSS. */
export function panelWidthFor(viewportWidth: number) {
  if (viewportWidth < 820) return 0;
  return Math.min(Math.max(viewportWidth * 0.256, 340), 470);
}

function useViewportWidth() {
  const [w, setW] = useState(() => window.innerWidth);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return w;
}

function Shell() {
  const location = useLocation();
  const [params] = useSearchParams();
  const { reduceMotion } = useA11y();
  const width = useViewportWidth();
  const navigate = useNavigate();
  const path = location.pathname;
  // Clicking a country on the globe analyses the clicked point, labelled with the country's name.
  const onPick = useCallback(
    (p: GlobePick) =>
      navigate(
        '/globe' +
          locationSearch({
            id: 'globe:' + p.lat + ',' + p.lng,
            name: p.country,
            detail: '',
            latitude: p.lat,
            longitude: p.lng,
          }),
      ),
    [navigate],
  );
  const mode: GlobeMode = path === '/' ? 'home' : path === '/globe' ? 'globe' : 'hidden';
  const selected = mode === 'globe' ? parseLocationParams(params) : null;
  const marker = useMemo(
    () => (selected ? { lat: selected.latitude, lng: selected.longitude } : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selected?.latitude, selected?.longitude],
  );

  useEffect(() => {
    const titles: Record<string, string> = {
      '/': 'FireHarmonics — Harmonization of MODIS and VIIRS Hot Spots',
      '/globe': 'Interactive Globe — FireHarmonics',
      '/about': 'About — FireHarmonics',
      '/contact': 'Contact — FireHarmonics',
      '/faq': 'FAQ — FireHarmonics',
    };
    document.title = titles[path] ?? 'FireHarmonics';
  }, [path]);

  return (
    <MotionConfig reducedMotion={reduceMotion ? 'always' : 'never'}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Starfield variant={mode === 'globe' ? 'deep' : mode === 'home' ? 'home' : 'navy'} />
      <Suspense fallback={null}>
        <GlobeStage mode={mode} marker={marker} panelWidth={panelWidthFor(width)} onPick={onPick} />
      </Suspense>
      <NavBar variant={mode === 'globe' ? 'globe' : 'default'} />
      <main
        id="main"
        tabIndex={-1}
        className={`page-main page-main--${mode === 'hidden' ? 'content' : mode}`}
      >
        <Suspense fallback={<p className="page-loading">Loading…</p>}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/globe" element={<GlobePage />} />
            <Route path="/about" element={<AboutPage />} />
            <Route path="/contact" element={<ContactPage />} />
            <Route path="/faq" element={<FaqPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </main>
      <AccessibilityPanel />
    </MotionConfig>
  );
}

export default function App() {
  return (
    <A11yProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Shell />
        </BrowserRouter>
      </QueryClientProvider>
    </A11yProvider>
  );
}
