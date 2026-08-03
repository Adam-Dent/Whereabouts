// Accessibility, checked rather than claimed.
//
// This app is used one-handed, outdoors, by people who are lost. Some of them
// will be using a screen reader, or a very large font, or have the contrast
// turned up. The README claims a Lighthouse accessibility score of 100, and
// this is what stops that claim quietly becoming false.
//
// axe-core runs entirely in the page, so it needs no network and does not
// interfere with the isolation the other tests rely on.

import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

import { isolate } from './isolation.js';

const REPO = dirname(dirname(fileURLToPath(import.meta.url)));
const data = JSON.parse(readFileSync(join(REPO, 'docs', 'houses.json'), 'utf8'));
const placed = data.houses.find((h) => h.lat != null && h.n[0].length > 4);

const scan = (page) => new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']);

const report = (r) =>
  r.violations.map((v) => `${v.id} (${v.impact}): ${v.help}\n    ${v.nodes[0].html.slice(0, 120)}`).join('\n');

test('the search screen has no accessibility violations', async ({ page }) => {
  await isolate(page);
  await page.goto('/');
  await page.fill('#q', placed.n[0]);
  await page.locator('.ri').first().waitFor();

  const r = await scan(page).analyze();
  expect(r.violations, report(r)).toEqual([]);
});

test('the house detail screen has no accessibility violations', async ({ page }) => {
  await isolate(page);
  await page.goto('/');
  await page.fill('#q', placed.n[0]);
  await page.locator('.ri', { hasText: placed.n[0] }).first().click();
  await page.locator('#d-name').waitFor();

  const r = await scan(page).analyze();
  expect(r.violations, report(r)).toEqual([]);
});

test('the about sheet has no accessibility violations', async ({ page }) => {
  await isolate(page);
  await page.goto('/');
  await page.click('#info-btn');
  await page.locator('#about-sheet').waitFor();
  await page.evaluate(() => document.querySelector('details.fold').setAttribute('open', ''));

  const r = await scan(page).analyze();
  expect(r.violations, report(r)).toEqual([]);
});

test('how-it-works and privacy have no accessibility violations', async ({ page }) => {
  await isolate(page);
  for (const path of ['/how-it-works.html', '/privacy.html']) {
    await page.goto(path);
    const r = await scan(page).analyze();
    expect(r.violations, `${path}\n${report(r)}`).toEqual([]);
  }
});

test('a house can be found and opened by keyboard alone', async ({ page }) => {
  // Result rows are divs with role=button, so keyboard support is hand-written
  // and can regress without anything looking wrong on screen.
  await isolate(page);
  await page.goto('/');

  await page.locator('#q').focus();
  await page.keyboard.type(placed.n[0]);
  await page.locator('.ri', { hasText: placed.n[0] }).first().waitFor();

  await page.locator('.ri', { hasText: placed.n[0] }).first().focus();
  await page.keyboard.press('Enter');

  await expect(page.locator('#d-name')).toHaveText(placed.n[0]);
});

test('the search field keeps a label even when empty', async ({ page }) => {
  // The placeholder is not a label: it disappears the moment anyone types, and
  // a screen reader user then has nothing telling them what the field is.
  await isolate(page);
  await page.goto('/');
  const label = await page.locator('#q').getAttribute('aria-label');
  expect(label && label.trim().length).toBeGreaterThan(0);
});
