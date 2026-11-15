// Creates transparent PNG derivatives of the supplied JPEG illustrations so they can sit on the
// navy page backgrounds exactly as in the mockups. The originals in /design/assets are only READ,
// never modified. Method: flood-fill the near-white background from the image border (plus listed
// seed points for regions enclosed by an outline), make it transparent, then un-matte a 2 px
// anti-aliasing band so edges do not show a white fringe.
//
// Usage: node scripts/derive-assets.mjs
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import jpeg from 'jpeg-js';
import { PNG } from 'pngjs';

const here = dirname(fileURLToPath(import.meta.url));
const designDir = resolve(here, '../../../design/assets');
const outDir = resolve(here, '../src/assets/derived');

// seeds: [x, y] fractions of width/height inside regions that are white background enclosed by lines.
const JOBS = [
  { name: 'about-blue-cloud' },
  { name: 'about-white-cloud' },
  { name: 'about-seated-person' },
  { name: 'about-technology-illustration' },
  { name: 'about-blue-corner-graphic' },
  { name: 'faq-person-with-laptop' },
  { name: 'faq-question-symbol', seeds: [[0.3, 0.5], [0.72, 0.62], [0.5, 0.82]] },
  {
    name: 'contact-team-illustration',
    // inside the browser-window frame, around the three people
    seeds: [[0.22, 0.4], [0.22, 0.75], [0.8, 0.4], [0.8, 0.8], [0.5, 0.27], [0.3, 0.3], [0.7, 0.3]],
  },
];

const isBackground = (r, g, b) => Math.min(r, g, b) >= 236 && Math.max(r, g, b) - Math.min(r, g, b) <= 18;

function derive({ name, seeds = [] }) {
  const src = join(designDir, `${name}.jpeg`);
  const bytes = readFileSync(src);
  const { width: w, height: h, data } = jpeg.decode(bytes, { useTArray: true, formatAsRGBA: true });
  const bg = new Uint8Array(w * h);
  const stack = [];
  const push = (x, y) => {
    if (x < 0 || y < 0 || x >= w || y >= h) return;
    const i = y * w + x;
    if (bg[i]) return;
    const o = i * 4;
    if (!isBackground(data[o], data[o + 1], data[o + 2])) return;
    bg[i] = 1;
    stack.push(i);
  };
  for (let x = 0; x < w; x++) {
    push(x, 0);
    push(x, h - 1);
  }
  for (let y = 0; y < h; y++) {
    push(0, y);
    push(w - 1, y);
  }
  for (const [fx, fy] of seeds) push(Math.round(fx * (w - 1)), Math.round(fy * (h - 1)));
  while (stack.length) {
    const i = stack.pop();
    const x = i % w;
    const y = (i - x) / w;
    push(x + 1, y);
    push(x - 1, y);
    push(x, y + 1);
    push(x, y - 1);
  }

  const out = new PNG({ width: w, height: h });
  for (let i = 0; i < w * h; i++) {
    const o = i * 4;
    let r = data[o];
    let g = data[o + 1];
    let b = data[o + 2];
    let a = 255;
    if (bg[i]) {
      a = 0;
    } else {
      // distance (<=2 px) to background -> anti-aliased edge: un-matte against white
      const x = i % w;
      const y = (i - x) / w;
      let near = false;
      for (let dy = -2; dy <= 2 && !near; dy++)
        for (let dx = -2; dx <= 2 && !near; dx++) {
          const nx = x + dx;
          const ny = y + dy;
          if (nx >= 0 && ny >= 0 && nx < w && ny < h && bg[ny * w + nx]) near = true;
        }
      if (near) {
        const alpha = Math.min(1, Math.max(0, (255 - Math.min(r, g, b)) / 115));
        if (alpha < 1 && alpha > 0) {
          r = Math.round(Math.min(255, Math.max(0, (r - (1 - alpha) * 255) / alpha)));
          g = Math.round(Math.min(255, Math.max(0, (g - (1 - alpha) * 255) / alpha)));
          b = Math.round(Math.min(255, Math.max(0, (b - (1 - alpha) * 255) / alpha)));
        }
        a = Math.round(alpha * 255);
      }
    }
    out.data[o] = r;
    out.data[o + 1] = g;
    out.data[o + 2] = b;
    out.data[o + 3] = a;
  }
  const png = PNG.sync.write(out, { colorType: 6 });
  writeFileSync(join(outDir, `${name}.png`), png);
  return {
    derived: `src/assets/derived/${name}.png`,
    source: `design/assets/${name}.jpeg`,
    source_sha256: createHash('sha256').update(bytes).digest('hex'),
    width: w,
    height: h,
    bytes: png.length,
  };
}

mkdirSync(outDir, { recursive: true });
const manifest = JOBS.map(derive);
writeFileSync(
  join(outDir, 'DERIVED_ASSETS.json'),
  JSON.stringify(
    {
      method:
        'Edge/seed flood-fill of near-white background (min channel >= 236) to alpha 0, 2 px un-matted edge band. Originals unchanged.',
      generator: 'workspace/frontend/scripts/derive-assets.mjs',
      files: manifest,
    },
    null,
    2,
  ),
);
for (const m of manifest) console.log(`${m.derived}  ${m.width}x${m.height}  ${(m.bytes / 1024).toFixed(0)} KiB`);
