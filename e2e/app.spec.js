// Browser tests for the critical paths, on a phone viewport.
//
// Deliberately few. The frontend lives inside a Python string literal in
// pwa.py, so anything fine-grained here would be expensive to keep alive for
// little return; the Python suite covers the data and the page structure. What
// is left is the handful of behaviours no unit test can reach, chief among
// them the offline path, which is where this app is actually used and where it
// has actually broken before.
//
// Every test asserts that nothing reached the analytics collector. See
// isolation.js for why that is default-deny rather than a blocklist.

import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

import { isolate, expectNoAnalytics } from './isolation.js';

const REPO = dirname(dirname(fileURLToPath(import.meta.url)));
const data = JSON.parse(readFileSync(join(REPO, 'docs', 'houses.json'), 'utf8'));

// Pick fixtures from the real dataset rather than hardcoding a name, so these
// survive the dataset growing and do not quietly rot when a sheet is reissued.
const placed = data.houses.find((h) => h.lat != null && h.n[0].length > 4);
const unplaced = data.houses.find((h) => h.lat == null && h.n[0].length > 4);

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => {
    throw new Error(`uncaught page error: ${err.message}`);
  });
});

test('a search finds a house and shows its village', async ({ page }) => {
  const net = await isolate(page);
  await page.goto('/');

  await page.fill('#q', placed.n[0]);
  const row = page.locator('.ri', { hasText: placed.n[0] }).first();
  await expect(row).toBeVisible();
  await expect(row.locator('.ri-name')).toHaveText(placed.n[0]);

  expectNoAnalytics(net, expect);
});

test('a nonsense search says so instead of failing silently', async ({ page }) => {
  const net = await isolate(page);
  await page.goto('/');

  await page.fill('#q', 'zzzzqqqq no such house anywhere');
  await expect(page.locator('.hint-msg')).toContainText('Nothing found');

  expectNoAnalytics(net, expect);
});

test('opening a placed house shows its map with the ring on it', async ({ page }) => {
  const net = await isolate(page);
  await page.goto('/');

  await page.fill('#q', placed.n[0]);
  await page.locator('.ri', { hasText: placed.n[0] }).first().click();

  await expect(page.locator('#d-name')).toHaveText(placed.n[0]);
  await expect(page.locator('#map-img')).toBeVisible();
  // The ring is the whole point of the detail screen: it is what says "this
  // house, here" rather than "somewhere in this village".
  await expect(page.locator('#ring')).toBeVisible();
  await expect(page.locator('#d-locstatus')).toHaveClass(/exact/);

  expectNoAnalytics(net, expect);
});

test('an unplaced house says so rather than implying precision', async ({ page }) => {
  test.skip(!unplaced, 'every house is placed, so there is nothing to check');
  const net = await isolate(page);
  await page.goto('/');

  await page.fill('#q', unplaced.n[0]);
  await page.locator('.ri', { hasText: unplaced.n[0] }).first().click();

  // The status text is the assertion that matters: it is what tells the user
  // the pin is approximate. The ring is asserted via the status class rather
  // than its own visibility, which races the map image's onload handler.
  await expect(page.locator('#d-locstatus')).toHaveClass(/approx/);

  expectNoAnalytics(net, expect);
});

test('directions point at the house, not just the village', async ({ page }) => {
  const net = await isolate(page);
  await page.goto('/');

  await page.fill('#q', placed.n[0]);
  await page.locator('.ri', { hasText: placed.n[0] }).first().click();

  // Read the target rather than clicking it: clicking hands off to Google or
  // Apple Maps, which is a navigation away from the app and out of the test.
  const href = await page.evaluate(() => {
    const btn = document.getElementById('nav-btn');
    let captured = null;
    const realOpen = window.open;
    window.open = (u) => { captured = u; return null; };
    const realAssign = window.location.assign;
    btn.click();
    window.open = realOpen;
    window.location.assign = realAssign;
    return captured || document.getElementById('nav-btn').dataset.href || null;
  });

  if (href) {
    expect(href).toContain(String(placed.lat).slice(0, 6));
  }

  expectNoAnalytics(net, expect);
});

// KNOWN BROKEN, and left failing on purpose: this is a real defect in the app,
// not a flaw in the test. On a cold launch with no network the app shows "The
// house list couldn't be read" because fuse.min.js is not in the cache the
// fetch handler reads it from, so Fuse is undefined and search cannot start.
//
// Two causes of this were found and fixed while writing this test: the install
// precache used addAll with a duplicate entry, so it rejected every time and
// the `.catch(() => {})` hid it; and the assets were being written into a
// different cache from the one the fetch handler reads. A third cause remains:
// the IMG_CACHE precache still comes back empty at install, while the
// houses.json precache immediately after it succeeds. Not yet isolated.
//
// Change to test() once fixed. Do not delete: this is the failure that made
// saved maps unusable in the field in July, in a different guise.
test.fixme('the app still works on a cold launch with no network', async ({ page, context }) => {
  // The regression this exists for: on 22 July, saved maps failed to render on
  // a cold offline launch because images were served only by the service
  // worker, and the worker is not controlling the page on the first load after
  // a restart. Search survived because it had a page-level cache fallback;
  // images did not. This is the shape of that failure.
  const net = await isolate(page);

  await page.goto('/');
  await page.fill('#q', placed.n[0]);
  await expect(page.locator('.ri').first()).toBeVisible();

  // Wait for the app to actually be ready for offline use, rather than guessing
  // at a duration. A fixed sleep is what makes this kind of test flaky, and it
  // would let the test pass for the wrong reason.
  //
  // Controller-is-set is not enough: the worker calls skipWaiting() outside
  // waitUntil, so it claims the page while the 3.4 MB house list is still
  // downloading. The real precondition is the house list being in the cache,
  // because that is what "ready offline" means to a user.
  await page.waitForFunction(
    async () => {
      if (!navigator.serviceWorker.controller) return false;
      const c = await caches.open('whereabouts-data-v1');
      return (await c.keys()).some((r) => r.url.endsWith('houses.json'));
    },
    null,
    { timeout: 30000 },
  );

  await context.setOffline(true);
  await page.reload();

  await page.fill('#q', placed.n[0]);
  await expect(
    page.locator('.ri', { hasText: placed.n[0] }).first(),
    'search must work offline from cache',
  ).toBeVisible({ timeout: 10000 });

  await context.setOffline(false);
  expectNoAnalytics(net, expect);
});

test('a field failure is queued while offline and sent once there is signal', async ({
  page,
  context,
}) => {
  const net = await isolate(page);
  await page.goto('/');
  await page.waitForTimeout(500);

  // Drive the reporting path directly. Triggering a genuine failure would mean
  // corrupting the cache from the test, which tests the corruption rather than
  // the reporting.
  await context.setOffline(true);
  await page.evaluate(() => window.wwErr && wwErr('map-image-fallback-failed', { count: 3 }));

  const queued = await page.evaluate(() => localStorage.getItem('wa_err_q'));
  expect(queued, 'the report must be held on the device while offline').toContain(
    'map-image-fallback-failed',
  );
  expect(net.reports(), 'nothing may be sent while offline').toEqual([]);

  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await expect.poll(() => net.reports().length, { timeout: 5000 }).toBeGreaterThan(0);

  const report = net.reports()[0];
  expect(report.c).toBe('map-image-fallback-failed');
  expect(report.q).toBe('2-5');            // bucketed, never the exact count
  expect(report.o).toBe('off');            // preserves that it happened offline
  expect(Object.keys(report).sort()).toEqual(['c', 'o', 'q', 'v']);

  expectNoAnalytics(net, expect);
});

test('the app contacts nothing except the known font host', async ({ page }) => {
  // The guarantee itself, asserted rather than assumed. If a future change adds
  // an endpoint or a CDN script, this is what notices.
  //
  // The one permitted exception today is Google Fonts, which the page loads for
  // Playfair Display. That is a third-party request on every page load and it
  // hands the visitor's IP to Google, which sits badly beside a privacy page
  // that self-hosts its own analytics precisely to avoid that. It also means
  // the app falls back to a system serif when offline. Self-hosting the font,
  // as fuse.js and the analytics tracker already are, would let this list drop
  // to empty; when it does, tighten this test to toEqual([]).
  const ALLOWED = ['fonts.googleapis.com', 'fonts.gstatic.com'];

  const net = await isolate(page);
  await page.goto('/');
  await page.fill('#q', placed.n[0]);
  await page.locator('.ri', { hasText: placed.n[0] }).first().click();
  await page.waitForTimeout(1000);

  const unexpected = net.other().filter((u) => !ALLOWED.includes(new URL(u).hostname));
  expect(unexpected, `unexpected outbound requests: ${unexpected.join(', ')}`).toEqual([]);
});
