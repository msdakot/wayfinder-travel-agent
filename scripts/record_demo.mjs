import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const baseURL = process.env.WAYFINDER_DEMO_URL || 'http://127.0.0.1:8766';
const rawDir = path.join(root, 'recordings', 'raw');
fs.mkdirSync(rawDir, { recursive: true });

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const cachedChromium = path.join(
  process.env.HOME || '', 'Library', 'Caches', 'ms-playwright',
  'chromium_headless_shell-1217', 'chrome-headless-shell-mac-arm64', 'chrome-headless-shell',
);
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ||
  (fs.existsSync(cachedChromium) ? cachedChromium : undefined);
const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
const context = await browser.newContext({
  viewport: { width: 430, height: 932 },
  deviceScaleFactor: 2,
  colorScheme: 'light',
  recordVideo: { dir: rawDir, size: { width: 430, height: 932 } },
});
const page = await context.newPage();

await page.addInitScript(() => {
  document.addEventListener('DOMContentLoaded', () => {
    const style = document.createElement('style');
    style.textContent = `
      html { scroll-behavior: smooth; }
      .demo-pointer { position: fixed; z-index: 99999; width: 18px; height: 18px;
        border: 3px solid rgba(255,255,255,.95); border-radius: 50%;
        background: rgba(23,126,104,.88); box-shadow: 0 2px 12px rgba(0,0,0,.28);
        pointer-events: none; transform: translate(-50%,-50%); transition: left .12s, top .12s; }
      .demo-ripple { position: fixed; z-index: 99998; width: 14px; height: 14px;
        border: 2px solid #177e68; border-radius: 50%; pointer-events: none;
        transform: translate(-50%,-50%); animation: demo-ripple .55s ease-out forwards; }
      @keyframes demo-ripple { to { width: 58px; height: 58px; opacity: 0; } }
    `;
    document.head.appendChild(style);
    const pointer = document.createElement('div');
    pointer.className = 'demo-pointer';
    pointer.style.left = '382px';
    pointer.style.top = '800px';
    document.body.appendChild(pointer);
    document.addEventListener('mousemove', event => {
      pointer.style.left = `${event.clientX}px`;
      pointer.style.top = `${event.clientY}px`;
    });
    document.addEventListener('click', event => {
      const ripple = document.createElement('div');
      ripple.className = 'demo-ripple';
      ripple.style.left = `${event.clientX}px`;
      ripple.style.top = `${event.clientY}px`;
      document.body.appendChild(ripple);
      setTimeout(() => ripple.remove(), 600);
    }, true);
  });
});

async function click(locator) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (box) {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 10 });
    await sleep(180);
  }
  await locator.click();
}

async function setSnapshot(operation, body) {
  await page.evaluate(async ({ operation, body }) => {
    const id = state.trip.trip.id;
    if (operation) {
      const response = await fetch(`/api/trips/${id}/${operation}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(await response.text());
    }
    state.trip = await (await fetch(`/api/trips/${id}`)).json();
    render();
  }, { operation, body });
}

try {
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: 'Where are you going?' }).waitFor();
  await sleep(3200);

  await click(page.getByRole('button', { name: /Add documents/ }));
  await page.getByRole('textbox', { name: 'Trip name' }).fill('Chile · December 2025');
  await sleep(900);
  await click(page.getByRole('button', { name: /Create and add documents/ }));
  await page.locator('#file-input').setInputFiles([
    'data/chile-documents/01-inbound-flight.txt',
    'data/chile-documents/02-flights-and-ground.txt',
    'data/chile-documents/03-stays-and-atacama.txt',
    'data/chile-documents/04-w-circuit.txt',
    'data/chile-documents/05-onward-connection.txt',
  ].map(file => path.join(root, file)));
  await page.getByText('5 of 15 added').waitFor({ timeout: 20000 });
  await sleep(1800);

  await page.evaluate(async () => {
    const id = state.trip.trip.id;
    const snapshot = await (await fetch(`/api/trips/${id}`)).json();
    for (const doc of snapshot.documents) {
      const response = await fetch(`/api/trips/${id}/review`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document_id: doc.id, kind: doc.kind, fields: doc.fields }),
      });
      if (!response.ok) throw new Error(await response.text());
    }
    await fetch(`/api/trips/${id}/replay`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes: 0, cause: 'unknown', event_key: `${id}-recording-on-time` }),
    });
    state.trip = await (await fetch(`/api/trips/${id}`)).json();
    state.tab = 'overview';
    render();
    scrollTo({ top: 0, behavior: 'auto' });
  });
  await sleep(3200);

  const wTrek = page.getByText('W Circuit day 2 · Las Torres Base hike', { exact: false });
  await wTrek.scrollIntoViewIfNeeded();
  await sleep(1700);
  await click(wTrek);
  await page.getByRole('complementary', { name: /Trip details for W Circuit day 2/ }).waitFor();
  await sleep(4500);
  await page.locator('.place-sheet-body').evaluate(element => element.scrollTo({ top: 330, behavior: 'smooth' }));
  await sleep(2200);
  await click(page.getByRole('button', { name: 'Close trip details' }));

  await page.evaluate(() => scrollTo({ top: 0, behavior: 'smooth' }));
  await sleep(1800);
  await click(page.locator('[data-action="flight-toggle"]').first());
  await page.getByRole('complementary', { name: /Flight details/ }).waitFor();
  await sleep(3500);
  await click(page.getByRole('button', { name: 'Close flight details' }));

  await setSnapshot('replay', { minutes: 71, cause: 'other', event_key: `recording-delay-${Date.now()}` });
  await page.evaluate(() => scrollTo({ top: 0, behavior: 'smooth' }));
  await sleep(3800);
  const atRisk = page.getByText(/May be affected|At risk/, { exact: false }).first();
  if (await atRisk.count()) {
    await atRisk.scrollIntoViewIfNeeded();
    await sleep(2200);
  }

  await click(page.locator('[data-action="assistant-toggle"]').last());
  // Keep the filmed interaction deterministic. The public POC also exposes the
  // Snowflake Cortex mode when the account connection is available.
  await page.evaluate(() => { state.mode = 'local'; });
  const question = page.getByRole('textbox', { name: 'Ask about your trip' });
  await question.fill('What changed, and what should I do?');
  await sleep(850);
  await click(page.getByRole('button', { name: 'Send question' }));
  await page.getByText('Based on your trip details', { exact: false }).last().waitFor({ timeout: 35000 });
  await sleep(8500);
} finally {
  const video = page.video();
  await context.close();
  await browser.close();
  if (video) console.log(await video.path());
}
