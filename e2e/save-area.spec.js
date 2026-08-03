// The "save maps for offline use" button, checked end to end.
//
// This is the feature people will rely on before driving somewhere with no
// signal, and the one where a silent failure is worst: the button says saved,
// they drive out, and the maps are not there. So this does not settle for the
// button changing colour. It names every map the district should contain,
// checks each one is genuinely in the cache, then goes offline and renders one.

import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

import { isolate } from './isolation.js';

const REPO = dirname(dirname(fileURLToPath(import.meta.url)));
const data = JSON.parse(readFileSync(join(REPO, 'docs', 'houses.json'), 'utf8'));
const IMAGES = 'whereabouts-images-v2';

/** The district with the fewest maps, so the test is quick but still real. */
const district = (() => {
  const byDistrict = new Map();
  for (const [id, s] of Object.entries(data.sheets)) {
    if (!s.img) continue;
    if (!byDistrict.has(s.district)) byDistrict.set(s.district, []);
    byDistrict.get(s.district).push({ id, img: s.img });
  }
  return [...byDistrict.entries()].sort((a, b) => a[1].length - b[1].length)[0];
})();
const [districtName, sheets] = district;

async function readyOffline(page) {
  await page.waitForFunction(
    async () => {
      if (!navigator.serviceWorker.controller) return false;
      const c = await caches.open('whereabouts-data-v1');
      return (await c.keys()).some((r) => r.url.endsWith('houses.json'));
    },
    null,
    { timeout: 30000 },
  );
}

async function openCoverage(page) {
  await page.click('#info-btn');
  await page.evaluate(() => document.querySelector('details.fold').setAttribute('open', ''));
  return page.locator('.area-row', { hasText: districtName }).first();
}

test(`saving "${districtName}" really stores all ${sheets.length} of its maps`, async ({ page }) => {
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  const row = await openCoverage(page);
  const btn = row.locator('.area-btn');
  await expect(btn).toHaveText(new RegExp(`Save ${sheets.length} maps`));

  await btn.click();
  await expect(btn, 'the button must end up in the saved state').toHaveClass(/saved/, {
    timeout: 120000,
  });

  // The real assertion: every single map the district claims, by name.
  const cached = await page.evaluate(async (n) => {
    const c = await caches.open(n);
    return (await c.keys()).map((r) => r.url);
  }, IMAGES);

  const missing = sheets.filter((s) => !cached.some((u) => u.endsWith(s.img)));
  expect(missing.map((s) => s.id), 'maps the button claimed to save but did not').toEqual([]);
  expect(cached.length).toBe(sheets.length);
});

test('a saved map from that district opens offline, with its ring', async ({ page, context }) => {
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  const row = await openCoverage(page);
  await row.locator('.area-btn').click();
  await expect(row.locator('.area-btn')).toHaveClass(/saved/, { timeout: 120000 });
  await page.click('#sheet-close-btn');

  // A placed house on a sheet in the district that was just saved.
  const ids = new Set(sheets.map((s) => s.id));
  const house = data.houses.find(
    (h) => h.lat != null && ids.has(h.id.slice(0, h.id.lastIndexOf('-'))),
  );
  expect(house, 'no placed house in the saved district').toBeTruthy();

  await context.setOffline(true);
  await page.reload();

  await page.fill('#q', house.n[0]);
  const result = page.locator(`.ri[data-id="${house.id}"]`);
  await result.waitFor({ timeout: 15000 });
  await result.click();

  // Rendered, not merely present: naturalWidth proves the bytes decoded.
  const rendered = await page.waitForFunction(
    () => {
      const img = document.getElementById('map-img');
      return img && img.complete && img.naturalWidth > 0;
    },
    null,
    { timeout: 20000 },
  );
  expect(rendered, 'the saved map did not actually render offline').toBeTruthy();
  await expect(page.locator('#ring')).toBeVisible();

  await context.setOffline(false);
});

test('saving an area also secures the house list and the shell', async ({ page }) => {
  // Saving maps but not the search index would produce the cruellest failure
  // of all: every map present, and no way to look anything up.
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  await page.evaluate(async () => {
    await caches.delete('whereabouts-data-v1');
    await caches.delete('whereabouts-shell-v1');
  });

  const row = await openCoverage(page);
  await row.locator('.area-btn').click();
  await expect(row.locator('.area-btn')).toHaveClass(/saved/, { timeout: 120000 });

  const restored = await page.evaluate(async () => ({
    data: (await (await caches.open('whereabouts-data-v1')).keys()).map((r) => r.url),
    shell: (await (await caches.open('whereabouts-shell-v1')).keys()).map((r) => r.url),
  }));
  expect(restored.data.some((u) => u.endsWith('houses.json')), 'house list not re-secured').toBe(true);
  expect(restored.shell.some((u) => u.endsWith('fuse.min.js')), 'search library not re-secured').toBe(true);
});

test('a part-failed save says so instead of claiming success', async ({ page }) => {
  // The failure mode that matters: someone drives out believing they have maps.
  const net = await isolate(page);
  await page.goto('/');
  await readyOffline(page);

  // Fail exactly one map, leaving the rest to succeed.
  await page.evaluate((victim) => {
    const realOpen = caches.open.bind(caches);
    caches.open = async (n) => {
      const c = await realOpen(n);
      if (n === 'whereabouts-images-v2') {
        const realAdd = c.add.bind(c);
        c.add = async (u) => {
          if (String(u).includes(victim)) throw new Error('network');
          return realAdd(u);
        };
      }
      return c;
    };
  }, sheets[0].img);

  const row = await openCoverage(page);
  const btn = row.locator('.area-btn');
  await btn.click();

  await expect(btn, 'a part-failed save must offer a retry').toHaveText(/Retry/, {
    timeout: 120000,
  });
  await expect(btn).not.toHaveClass(/saved/);
  await expect(btn).toBeEnabled();
  await expect.poll(() => net.reports().map((r) => r.c), { timeout: 15000 }).toContain(
    'area-save-failed',
  );
});

test('a second visit shows the area as already saved', async ({ page }) => {
  await isolate(page);
  await page.goto('/');
  await readyOffline(page);
  const row = await openCoverage(page);
  await row.locator('.area-btn').click();
  await expect(row.locator('.area-btn')).toHaveClass(/saved/, { timeout: 120000 });

  await page.reload();
  await readyOffline(page);
  const again = await openCoverage(page);
  await expect(
    again.locator('.area-btn'),
    'a saved area must not invite the user to download it all over again',
  ).toHaveClass(/saved/, { timeout: 20000 });
});
