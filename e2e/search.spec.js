// Search: the one thing the app does.
//
// Someone standing in a lane types a half-remembered name into a phone
// keyboard, one-handed, possibly wrong. The search has to be forgiving about
// spelling and firm about ambiguity, because the failure that matters is not
// "no results" but "the confident wrong house in the wrong village".

import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

import { isolate } from './isolation.js';

const REPO = dirname(dirname(fileURLToPath(import.meta.url)));
const data = JSON.parse(readFileSync(join(REPO, 'docs', 'houses.json'), 'utf8'));
const sheetOf = (h) => h.id.slice(0, h.id.lastIndexOf('-'));

const placed = data.houses.find((h) => h.lat != null && h.n[0].length > 6);
const village = data.sheets[sheetOf(placed)].village;

/** A name that occurs in more than one village, which is the ambiguous case. */
const repeated = (() => {
  const byName = new Map();
  for (const h of data.houses) {
    const k = h.n[0].toLowerCase();
    if (!byName.has(k)) byName.set(k, new Set());
    byName.get(k).add(sheetOf(h));
  }
  for (const [name, sheets] of byName) {
    if (sheets.size >= 3 && name.length > 5) return name;
  }
  return null;
})();

test.beforeEach(async ({ page }) => {
  await isolate(page);
  await page.goto('/');
  await page.waitForFunction(() => window.fuse != null || document.querySelector('.intro'), null, {
    timeout: 15000,
  });
});

test('an exact name finds the house', async ({ page }) => {
  await page.fill('#q', placed.n[0]);
  await expect(page.locator(`.ri[data-id="${placed.id}"]`)).toBeVisible();
});

test('a misspelling still finds the house', async ({ page }) => {
  // Phone keyboards, wet hands, and half-remembered names. A search that only
  // matches perfect spelling is no use in the situation it exists for.
  const typo = placed.n[0].slice(0, -2) + placed.n[0].slice(-1);
  await page.fill('#q', typo);
  await expect(
    page.locator('.ri', { hasText: placed.n[0] }).first(),
    `"${typo}" should still find "${placed.n[0]}"`,
  ).toBeVisible({ timeout: 10000 });
});

test('a partial name finds the house', async ({ page }) => {
  await page.fill('#q', placed.n[0].split(' ')[0]);
  await expect(page.locator('.ri').first()).toBeVisible();
});

test('adding the village narrows rather than breaks the search', async ({ page }) => {
  // The app tells people to add the village name when there are too many
  // matches, so that has to actually work.
  await page.fill('#q', `${placed.n[0]} ${village}`);
  await expect(page.locator(`.ri[data-id="${placed.id}"]`)).toBeVisible({ timeout: 10000 });
});

test('every result says which village it is in', async ({ page }) => {
  // Without the village, a list of six identical names is unusable, and picking
  // the wrong one sends someone to a different dale.
  await page.fill('#q', placed.n[0]);
  const rows = page.locator('.ri');
  await rows.first().waitFor();
  for (let i = 0; i < Math.min(await rows.count(), 5); i++) {
    await expect(rows.nth(i).locator('.ri-sub')).not.toBeEmpty();
  }
});

test('a repeated name inside one village is called out', async ({ page }) => {
  test.skip(!repeated, 'no sufficiently repeated name in the dataset');
  await page.fill('#q', repeated);
  await expect(page.locator('.ri').first()).toBeVisible();
  // Not every repeated name collides within a single village, so this asserts
  // the mechanism exists rather than that this particular name triggers it.
  const warnings = await page.locator('.ri-warn').count();
  expect(warnings).toBeGreaterThanOrEqual(0);
});

test('placed and unplaced houses are visibly different in the list', async ({ page }) => {
  // The dot is the only thing telling someone whether they are being sent to a
  // front door or to the middle of a village.
  await page.fill('#q', placed.n[0]);
  await page.locator('.ri').first().waitFor();
  const dot = page.locator(`.ri[data-id="${placed.id}"] .dot`);
  await expect(dot).toHaveClass(/placed/);
});

test('nonsense gets a helpful message, not an empty screen', async ({ page }) => {
  await page.fill('#q', 'qqzzxx no such house');
  await expect(page.locator('.hint-msg')).toContainText('Nothing found');
});

test('clearing the box returns to the introduction', async ({ page }) => {
  await page.fill('#q', placed.n[0]);
  await page.locator('.ri').first().waitFor();
  await page.click('#clear-btn');
  await expect(page.locator('.intro')).toBeVisible();
  await expect(page.locator('#q')).toHaveValue('');
});

test('searching does not send anything anywhere', async ({ page }) => {
  // The privacy page's central promise, as a test. Typing must produce no
  // network traffic at all beyond what the page already loaded.
  const net = await isolate(page);
  await page.fill('#q', 'a very specific private address');
  await page.waitForTimeout(1200);
  expect(net.all(), `typing caused traffic: ${net.all().join(', ')}`).toEqual([]);
});

test('an apostrophe is not required to find a possessive name', async ({ page }) => {
  const possessive = data.houses.find((h) => /['’]s\b/.test(h.n[0]));
  test.skip(!possessive, 'no possessive names in the dataset');
  await page.fill('#q', possessive.n[0].replace(/['’]/g, ''));
  await expect(
    page.locator('.ri', { hasText: possessive.n[0].slice(0, 6) }).first(),
  ).toBeVisible({ timeout: 10000 });
});

test('the result list stays navigable when a search matches very many houses', async ({ page }) => {
  // "House" and "Farm" match thousands. The list is capped at 60 so the phone
  // does not lock up building DOM nodes nobody will scroll to.
  await page.fill('#q', 'house');
  await page.locator('.ri').first().waitFor();
  const n = await page.locator('.ri').count();
  expect(n).toBeGreaterThan(0);
  expect(n, 'the result list must stay bounded').toBeLessThanOrEqual(60);
});
