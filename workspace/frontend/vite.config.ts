/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';

// The FastAPI backend has no CORS middleware, so the dev/preview servers proxy
// API calls to it. The backend is never modified.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.FIREHARMONICS_API_TARGET || 'http://127.0.0.1:8000';
  const proxy = {
    '/api': { target, changeOrigin: true },
    '/health': { target, changeOrigin: true },
  };
  return {
    plugins: [react()],
    resolve: {
      // Original supplied artwork is read in place from /design (never copied or modified).
      alias: { '@design': fileURLToPath(new URL('../../design', import.meta.url)) },
      dedupe: ['three'],
    },
    server: { port: 5173, strictPort: true, proxy, fs: { allow: ['.', '../../design'] } },
    preview: { port: 4173, strictPort: true, proxy },
    build: {
      chunkSizeWarningLimit: 1600,
      rollupOptions: {
        output: {
          manualChunks(id: string) {
            if (id.includes('node_modules/three') || id.includes('three-globe') || id.includes('@react-three'))
              return 'globe';
            if (id.includes('recharts') || id.includes('d3-')) return 'charts';
            if (id.includes('jspdf') || id.includes('html-to-image')) return 'pdf';
            return undefined;
          },
        },
      },
    },
    test: {
      include: ['tests/unit/**/*.test.ts'],
      environment: 'node',
    },
  };
});
