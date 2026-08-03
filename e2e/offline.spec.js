// Offline behaviour: the part of this app that actually matters.
//
// Whereabouts exists to be used in a lane in the Dales with no signal, holding
// a phone, looking for a house called Sunnyside. Everything else is a nicety.
// Every bug this project has had in the field has been an offline bug, and each
// one was invisible until someone was standing in a field.
//
// So these cover the whole storage story: what gets cached without asking, what
// only gets cached when asked, what survives a restart, what happens when a map
// was never saved, and what happens when the browser refuses to store more.
//
// Terminology, matching the service worker:
//   shell cache  whereabouts-shell-v1   the pages and the libraries
//   data cache   whereabouts-data-v1    houses.json, the searchable list
//   image cache  whereabouts-images-v2  the village maps

import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

import { isolate } from './isolation.js';

const REPO = dirname(dirname(fileURLToPath(import.meta.url)));
const data = JSON.parse(readFileSync(join(REPO, 'docs', 'houses.json'), 'utf8'));

const SHELL = 'whereabouts-shell-v1';
const DATA = 'whereabouts-data-v1';
const IMAGES = 'whereabouts-images-v2';

const placed = data.houses.find((h) => h.lat != null && h.n[0].length > 4);
const sheetOf = (h) => h.id.slice(0, h.id.lastIndexOf('-'));

/** Wait until the app is genuinely usable offline, not merely registered. */
async function readyOffline(page) {
  await page.waitForFunction(
    async (names) => {
      if (!navigator.serviceWorker.controller) return false;
      const shell = await caches.open(names.SHELL);
      const dataC = await caches.open(names.DATA);
      const shellUrls = (await shell.keys()).map((r) => r.url);
      const hasData = (await dataC.keys()).some((r) => r.url.endsWith('houses.json'));
      return hasData && shellUrls.some((u) => u.endsWith('fuse.min.js'));
    },
    { SHELL, DATA },
    { timeout: 30000 },
  );
}

const cacheUrls = (page, name) =>
  page.evaluate(async (n) => {
    const c = await caches.open(n);
    return (await c.keys()).map((r) => r.url);
  }, name);

async function search(page, name) {
  await page.fill('#q', name);
  return page.locator('.ri', { hasText: name }).first();
}

/** The exact house, by id. House names repeat across villages, so matching on
 *  text alone lands on whichever village sorts first. */
async function openExact(page, house) {
  await page.fill('#q', house.n[0]);
  const row = page.locator(`.ri[data-id="${house.id}"]`);
  await row.waitFor({ timeout: 10000 });
  await row.click();
  return row;
}

// ── What is cached without anyone asking ────────────────────────────────────

test('a first online visit leaves the app usable offline', async ({ page, context }) => {
  const net = await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  await context.setOffline(true);
  await page.reload();
  await expect(await search(page, placed.n[0])).toBeVisible({ timeout: 10000 });

  await context.setOffline(false);
  expect(net.all().filter((u) => u.includes('collect'))).toEqual([]);
});

test('the shell holds the pages and the libraries, not the maps', async ({ page }) => {
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  const urls = await cacheUrls(page, SHELL);
  for (const want of ['index.html', 'fuse.min.js', 'counterscale.min.js', '.woff2']) {
    expect(urls.some((u) => u.includes(want)), `${want} should be precached`).toBe(true);
  }
  // 865 maps is about 129 MB. Precaching them would be an unforgivable thing to
  // do to someone's data allowance, so it must never happen by accident.
  expect(urls.some((u) => u.includes('/images/')), 'maps must never be precached').toBe(false);
});

test('no map is downloaded until it is actually looked at', async ({ page }) => {
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  expect(await cacheUrls(page, IMAGES)).toEqual([]);
});

test('opening a house caches that one map, and only that one', async ({ page }) => {
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  await openExact(page, placed);
  await expect(page.locator('#map-img')).toBeVisible();
  await page.waitForFunction(
    async (n) => (await (await caches.open(n)).keys()).length > 0,
    IMAGES,
    { timeout: 15000 },
  );

  const urls = await cacheUrls(page, IMAGES);
  expect(urls.length, 'viewing one house must not pull down a whole district').toBe(1);
  expect(urls[0]).toContain(data.sheets[sheetOf(placed)].img);
});

// ── The other pages ─────────────────────────────────────────────────────────

test('how-it-works and privacy are readable offline without being visited first', async ({
  page,
  context,
}) => {
  // The moment someone wants to check what the app does with their data is
  // exactly the moment they may have no signal. A privacy page that is only
  // available online is not much of a privacy page.
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  await context.setOffline(true);
  for (const [path, heading] of [
    ['/how-it-works.html', 'Finding a house that has a name but no number'],
    ['/privacy.html', 'Nothing you search for leaves your phone'],
  ]) {
    const resp = await page.goto(path);
    expect(resp.status(), `${path} offline`).toBe(200);
    await expect(page.locator('h1'), `${path} served a shell, not the page`).toHaveText(heading);
  }
  await context.setOffline(false);
});

test('the pages link to each other and back, offline', async ({ page, context }) => {
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);
  await context.setOffline(true);

  await page.goto('/privacy.html');
  await page.click('a[href="./how-it-works.html"]');
  await expect(page.locator('h1')).toBeVisible();
  await page.click('a[href="./"]');
  await expect(page.locator('#q')).toBeVisible();

  await context.setOffline(false);
});

// ── Saving an area on purpose ───────────────────────────────────────────────

test('saving an area stores all of its maps and remembers it', async ({ page, context }) => {
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  await page.click('#info-btn');
  await page.evaluate(() => document.querySelector('details.fold').setAttribute('open', ''));

  // Pick the district with the fewest maps, so the test stays quick.
  const target = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('.area-row')]
      .map((r) => ({
        name: r.querySelector('b').textContent,
        n: parseInt((r.querySelector('.area-btn')?.textContent || '').match(/(\d+)\s+maps/)?.[1] || '0', 10),
      }))
      .filter((r) => r.n > 0);
    rows.sort((a, b) => a.n - b.n);
    return rows[0];
  });
  expect(target, 'no saveable district found').toBeTruthy();

  const row = page.locator('.area-row', { hasText: target.name }).first();
  await row.locator('.area-btn').click();
  await expect(row.locator('.area-btn')).toHaveClass(/saved/, { timeout: 60000 });

  expect((await cacheUrls(page, IMAGES)).length).toBeGreaterThanOrEqual(target.n);

  // The choice itself has to survive a restart, or the button lies after relaunch.
  const saved = await page.evaluate(() => localStorage.getItem('whereabouts_saved_areas'));
  expect(saved).toContain(target.name);

  await context.setOffline(true);
  await page.reload();
  await page.click('#info-btn');
  await page.evaluate(() => document.querySelector('details.fold').setAttribute('open', ''));
  await expect(
    page.locator('.area-row', { hasText: target.name }).first().locator('.area-btn'),
    'a saved area must still read as saved after an offline restart',
  ).toHaveClass(/saved/, { timeout: 20000 });
  await context.setOffline(false);
});

// ── Offline, with and without the map ───────────────────────────────────────

test('a saved map still renders offline after a cold restart', async ({ page, context }) => {
  // The July bug: images were served only by the service worker, so on a cold
  // launch where the worker was not yet controlling the page, the <img> hit a
  // dead network and failed even though the map was sitting in the cache.
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  (await search(page, placed.n[0])).click();
  await expect(page.locator('#map-img')).toBeVisible();
  await page.waitForFunction(
    async (n) => (await (await caches.open(n)).keys()).length > 0, IMAGES, { timeout: 15000 });

  await context.setOffline(true);
  await page.reload();
  (await search(page, placed.n[0])).click();

  await expect(page.locator('#map-img')).toBeVisible();
  const ok = await page.waitForFunction(() => {
    const img = document.getElementById('map-img');
    return img && img.complete && img.naturalWidth > 0;
  }, null, { timeout: 15000 });
  expect(ok).toBeTruthy();
  await expect(page.locator('#ring')).toBeVisible();

  await context.setOffline(false);
});

test('an unsaved map fails honestly offline instead of hanging or crashing', async ({
  page,
  context,
}) => {
  // Being told the map is not here is a usable answer. A blank screen, a broken
  // image icon, or a spinner forever is not.
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));

  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  await context.setOffline(true);
  await page.reload();

  // Search still works: the house list is cached even though this map is not.
  const row = await search(page, placed.n[0]);
  await expect(row, 'search must survive without the map').toBeVisible({ timeout: 10000 });
  await row.click();

  // The details that do not need the image must still be there, and directions
  // must still work, because the coordinates are in the house list.
  await expect(page.locator('#d-name')).toHaveText(placed.n[0]);
  await expect(page.locator('#nav-btn')).toBeEnabled();
  expect(errors, `offline navigation threw: ${errors.join(', ')}`).toEqual([]);

  await context.setOffline(false);
});

test('directions still work offline, because the coordinates are local', async ({
  page,
  context,
}) => {
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);
  await context.setOffline(true);
  await page.reload();

  (await search(page, placed.n[0])).click();
  await expect(page.locator('#nav-btn')).toBeEnabled();
  await expect(page.locator('#d-locstatus')).toHaveClass(/exact/);

  await context.setOffline(false);
});

// ── Storage going wrong ─────────────────────────────────────────────────────

test('a browser refusing to store more is reported, not swallowed', async ({ page }) => {
  // Phones evict caches under storage pressure, and iOS is especially keen. A
  // quota refusal used to pass silently, so a half-saved area looked saved.
  const net = await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  await page.evaluate(() => {
    const realOpen = caches.open.bind(caches);
    caches.open = async (n) => {
      const c = await realOpen(n);
      if (n === 'whereabouts-images-v2') {
        c.add = async () => {
          const e = new Error('quota');
          e.name = 'QuotaExceededError';
          throw e;
        };
      }
      return c;
    };
  });

  await page.click('#info-btn');
  await page.evaluate(() => document.querySelector('details.fold').setAttribute('open', ''));
  const btn = page.locator('.area-row .area-btn').first();
  await btn.click();

  await expect(btn, 'a failed save must not present itself as done').not.toHaveClass(/saved/, {
    timeout: 30000,
  });
  await expect.poll(() => net.reports().map((r) => r.c), { timeout: 15000 }).toContain(
    'quota-exceeded',
  );
});

test('the caches the worker writes are exactly the ones it cleans up', async ({ page }) => {
  // The worker deletes any cache not on its own list on activate. A cache
  // written under a name missing from that list is deleted on the next deploy,
  // taking someone's saved maps with it.
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  const names = await page.evaluate(() => caches.keys());
  const sw = readFileSync(join(REPO, 'docs', 'sw.js'), 'utf8');
  const known = [...sw.matchAll(/'(whereabouts-[a-z]+-v\d+)'/g)].map((m) => m[1]);
  for (const n of names) {
    expect(known, `${n} is written but the worker would delete it`).toContain(n);
  }
});

// ── Taking over from an older version ───────────────────────────────────────

test('a new version takes over from an old one without stranding the phone', async ({
  page,
  context,
}) => {
  // The remaining risk once people have the app installed: a deploy lands, the
  // phone is already running an older worker, and it either keeps serving stale
  // code forever or breaks. The worker calls skipWaiting and claim, so a new
  // one should take control on the next load rather than waiting for every tab
  // to close. This checks that, and that the caches survive the handover.
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  const before = await page.evaluate(async () => ({
    controller: !!navigator.serviceWorker.controller,
    shell: (await (await caches.open('whereabouts-shell-v1')).keys()).length,
  }));
  expect(before.controller).toBe(true);

  // Force the worker to be re-fetched and re-installed, which is what a deploy
  // looks like from the page's point of view.
  await page.evaluate(async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    await reg.update();
  });
  await page.reload();
  await readyOffline(page);

  const after = await page.evaluate(async () => ({
    controller: !!navigator.serviceWorker.controller,
    shell: (await (await caches.open('whereabouts-shell-v1')).keys()).length,
    caches: await caches.keys(),
  }));
  expect(after.controller, 'the page must still be controlled after an update').toBe(true);
  expect(after.shell, 'the shell must not be emptied by an update').toBeGreaterThanOrEqual(
    before.shell,
  );
  // The activate handler deletes caches it does not recognise. A rename without
  // a migration would silently bin every map the user had saved.
  expect(after.caches.sort()).toEqual(
    ['whereabouts-data-v1', 'whereabouts-images-v2', 'whereabouts-shell-v1'].filter((c) =>
      after.caches.includes(c),
    ).sort(),
  );

  // And it must still work offline afterwards.
  await context.setOffline(true);
  await page.reload();
  await expect(await search(page, placed.n[0])).toBeVisible({ timeout: 15000 });
  await context.setOffline(false);
});
