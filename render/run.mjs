// Render the isometric map tile by tile in headless Chromium (hardware WebGL via Vulkan).
//
//   node render/run.mjs crop <x> <y> <w> <h> [--scale 3] [--out file.png]   one region, for iterating
//   node render/run.mjs all [--scale 3] [--tile 2048]                       every tile into out/tiles/
//   --kuwahara N   painterly smoothing radius in pixels (0 = off)
//   --pass objects a mask instead: white where buildings and trees stand above the ground (into out/tiles-<scale>-objects/)
//   --look 135     the compass direction the camera looks in; 45 (north-east) is the default, other views get -v<look>
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile, stat } from 'node:fs/promises';
import { extname, join, resolve } from 'node:path';
import { chromium } from 'playwright';

const ROOT = resolve(import.meta.dirname, '..');
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.png': 'image/png', '.bin': 'application/octet-stream' };

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
}

function serve() {
  const server = createServer(async (req, res) => {
    const path = join(ROOT, decodeURIComponent(new URL(req.url, 'http://x').pathname));
    if (!path.startsWith(ROOT)) return res.writeHead(403).end();
    try {
      const body = await readFile(path);
      res.writeHead(200, { 'content-type': TYPES[extname(path)] || 'application/octet-stream' }).end(body);
    } catch {
      res.writeHead(404).end();
    }
  });
  return new Promise((ok) => server.listen(0, '127.0.0.1', () => ok(server)));
}

async function openScene(scale, kuwahara, pass, look) {
  const server = await serve();
  // RENDER_GL=swiftshader renders on the CPU, for machines without a GPU (slow, same pixels); CHROME points at a
  // Chrome or Chromium to use instead of Playwright's own.
  const gl = process.env.RENDER_GL === 'swiftshader'
    ? ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--use-gl=angle']
    : ['--use-angle=vulkan', '--ignore-gpu-blocklist', '--enable-gpu-rasterization'];
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROME || undefined,
    args: gl,
  });
  const page = await browser.newPage();
  page.on('console', (m) => m.type() === 'error' && console.error('page:', m.text()));
  page.on('pageerror', (e) => console.error('page error:', e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/render/index.html?scale=${scale}&kuwahara=${kuwahara}&pass=${pass}&look=${look}`);
  await page.waitForFunction(() => window.ready || window.loadError, null, { timeout: 300_000 });
  const err = await page.evaluate(() => window.loadError);
  if (err) throw new Error(err);
  const info = await page.evaluate(() => window.sceneInfo);
  return { page, info, close: async () => { await browser.close(); server.close(); } };
}

async function renderTo(page, file, x, y, w, h) {
  const url = await page.evaluate(([x, y, w, h]) => window.renderTile(x, y, w, h), [x, y, w, h]);
  await writeFile(file, Buffer.from(url.split(',')[1], 'base64'));
}

const mode = process.argv[2];
const scale = parseFloat(arg('scale', '3'));
const kuwahara = parseInt(arg('kuwahara', '0'));
const pass = arg('pass', 'color');
const look = parseFloat(arg('look', '45'));
const t0 = Date.now();
const { page, info, close } = await openScene(scale, kuwahara, pass, look);
console.log(`scene ready in ${((Date.now() - t0) / 1000).toFixed(1)}s, full image ${info.width} x ${info.height}`);

if (mode === 'crop') {
  const [x, y, w, h] = process.argv.slice(3, 7).map(Number);
  const out = arg('out', join(ROOT, 'out', 'crop.png'));
  await mkdir(join(out, '..'), { recursive: true });
  await renderTo(page, out, x, y, w, h);
  console.log(`wrote ${out}`);
} else if (mode === 'all') {
  const tile = parseInt(arg('tile', '2048'));
  const dir = join(ROOT, 'out', `tiles-${scale}${pass === 'color' ? '' : `-${pass}`}${look === 45 ? '' : `-v${look}`}`);
  await mkdir(dir, { recursive: true });
  const cols = Math.ceil(info.width / tile), rows = Math.ceil(info.height / tile);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const w = Math.min(tile, info.width - c * tile), h = Math.min(tile, info.height - r * tile);
      await renderTo(page, join(dir, `${String(r).padStart(2, '0')}_${String(c).padStart(2, '0')}.png`), c * tile, r * tile, w, h);
    }
    console.log(`row ${r + 1}/${rows}`);
  }
  await writeFile(join(dir, 'layout.json'), JSON.stringify({ ...info, tile, cols, rows }));
  console.log(`done in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
} else {
  console.error('usage: node render/run.mjs crop x y w h | all');
}
await close();
