import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const baseURL = process.env.WAYFINDER_DEMO_URL || 'http://127.0.0.1:8767';
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

try {
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.evaluate(async () => {
    const createdResponse = await fetch('/api/trips', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'A long weekend in London', demo: true, full_demo: true }),
    });
    if (!createdResponse.ok) throw new Error(await createdResponse.text());
    const created = await createdResponse.json();
    const delayedResponse = await fetch(`/api/trips/${created.trip.id}/replay`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes: 120, cause: 'weather', event_key: `recovery-demo-${Date.now()}` }),
    });
    if (!delayedResponse.ok) throw new Error(await delayedResponse.text());
    state.trip = await delayedResponse.json();
    state.trips = [state.trip.trip];
    state.page = 'trips';
    state.tab = 'overview';
    render();
    scrollTo({ top: 0, behavior: 'auto' });
  });

  await page.getByText('Your connection needs a decision', { exact: false }).waitFor();
  await sleep(3200);
  await click(page.getByRole('button', { name: 'Choose option' }));
  await page.getByRole('heading', { name: 'Choose how to continue' }).waitFor();
  await sleep(4200);

  await click(page.getByRole('button', { name: /Choose option A/ }));
  await page.getByRole('heading', { name: 'Confirm this choice' }).waitFor();
  await sleep(2600);
  await click(page.getByRole('button', { name: /Confirm choice/ }));

  await page.getByRole('region', { name: 'Itinerary updated' }).waitFor({ timeout: 10000 });
  await page.evaluate(() => scrollTo({ top: 0, behavior: 'smooth' }));
  await sleep(5200);
  const replacement = page.getByText('Replacement train', { exact: true });
  await replacement.scrollIntoViewIfNeeded();
  await sleep(4500);
  await click(replacement);
  await page.getByRole('complementary', { name: /Train journey details/ }).waitFor();
  await sleep(4000);
} finally {
  const video = page.video();
  await context.close();
  await browser.close();
  if (video) console.log(await video.path());
}
