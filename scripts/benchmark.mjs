#!/usr/bin/env node
/**
 * Measure dashboard interaction latency against a running CursusTrace server.
 *
 * Start the app against any database (the demo one is convenient):
 *
 *   uv run python scripts/seed_demo.py --db data/demo.db
 *   CURSUS_BACKUP_DIR=backups/demo uv run cursustrace --db data/demo.db run --port 8090
 *
 * Then run:
 *
 *   NODE_PATH="$(npm root -g)/@playwright/test/node_modules" \
 *     node scripts/benchmark.mjs
 *
 * CURSUS_URL overrides the target (default http://127.0.0.1:8090) and `--json`
 * prints a machine-readable result. The script reports wall time, DOM growth,
 * and the WebSocket frames/bytes each interaction causes.
 */
import { createRequire } from 'node:module';
import process from 'node:process';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const BASE_URL = process.env.CURSUS_URL ?? 'http://127.0.0.1:8090';
const AS_JSON = process.argv.includes('--json');

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');

  const ws = { sent: 0, recv: 0, bytes: 0 };
  cdp.on('Network.webSocketFrameSent', () => {
    ws.sent += 1;
  });
  cdp.on('Network.webSocketFrameReceived', (event) => {
    ws.recv += 1;
    ws.bytes += event.response?.payloadData?.length ?? 0;
  });

  const nodes = () => page.evaluate(() => document.querySelectorAll('*').length);

  async function measure(label, action) {
    const before = { ...ws };
    const beforeNodes = await nodes();
    const start = Date.now();
    await action();
    const deadline = Date.now() + 15000;
    while (ws.recv === before.recv && Date.now() < deadline) {
      await page.waitForTimeout(10);
    }
    await page.waitForTimeout(250);
    return {
      label,
      ms: Date.now() - start,
      nodesBefore: beforeNodes,
      nodesAfter: await nodes(),
      framesReceived: ws.recv - before.recv,
      framesSent: ws.sent - before.sent,
      bytesReceived: ws.bytes - before.bytes,
    };
  }

  const results = [];
  const started = Date.now();
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('text=Add Job', { timeout: 60000 });
  await page.waitForTimeout(800);
  results.push({
    label: 'initial load',
    ms: Date.now() - started,
    nodesBefore: 0,
    nodesAfter: await nodes(),
    framesReceived: ws.recv,
    framesSent: ws.sent,
    bytesReceived: ws.bytes,
  });

  const tab = (pattern) => page.locator('.q-tab', { hasText: pattern }).first();
  const button = (name) => page.getByRole('button', { name, exact: true });

  results.push(await measure('tab: applied', () => tab(/Applied \(/).click()));
  results.push(await measure('tab: interview', () => tab(/Interview \(/).click()));
  results.push(await measure('tab: unapplied', () => tab(/Unapplied \(/).click()));
  results.push(await measure('view: board', () => button('Board').click()));
  results.push(await measure('view: list', () => button('List').click()));
  results.push(await measure('section: statistics', () => tab('Statistics').click()));
  results.push(await measure('section: dashboard', () => tab('Dashboard').click()));

  const search = page.getByPlaceholder('e.g. Adapty');
  results.push(
    await measure('search "acme"', async () => {
      await search.fill('acme');
      await page.waitForTimeout(320);
    }),
  );
  results.push(
    await measure('clear search', async () => {
      await search.fill('');
      await page.waitForTimeout(320);
    }),
  );

  await page.getByRole('button', { name: /Filters/ }).click();
  await page.waitForTimeout(400);
  results.push(
    await measure('sort: company A-Z', async () => {
      await page.locator('.q-select').filter({ hasText: 'Newest first' }).click();
      await page.locator('.q-item', { hasText: 'Company A-Z' }).first().click();
    }),
  );

  await browser.close();

  if (AS_JSON) {
    console.log(JSON.stringify({ url: BASE_URL, results }, null, 2));
    return;
  }
  for (const result of results) {
    console.log(
      `${result.label.padEnd(24)} ${String(result.ms).padStart(5)} ms  ` +
        `nodes ${result.nodesBefore}->${result.nodesAfter}  ` +
        `frames +${result.framesReceived} recv / +${result.framesSent} sent  ` +
        `+${result.bytesReceived} B`,
    );
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
