// Does the Content-Security-Policy actually let the app run?
//
// A CSP is the kind of change that looks right in the diff and silently breaks
// the site in production, because the dev server does not send it and the tests
// never see it. So this reads the real policy out of the generated _headers and
// applies it to the real page, which is as close to the deployed behaviour as
// this can get without deploying.
//
// If a script hash is wrong the page does not start, so these tests fail rather
// than the users finding out.

import { test, expect } from '@playwright/test';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

import { isolate } from './isolation.js';

const REPO = dirname(dirname(fileURLToPath(import.meta.url)));
const middleware = readFileSync(join(REPO, 'functions', '_middleware.js'), 'utf8');
// The generated middleware embeds the policies as a JSON object.
const CSP = JSON.parse(middleware.slice(middleware.indexOf('const CSP = ') + 12,
                                       middleware.indexOf('};\n\nconst COMMON') + 1));
const data = JSON.parse(readFileSync(join(REPO, 'docs', 'houses.json'), 'utf8'));
const placed = data.houses.find((h) => h.lat != null && h.n[0].length > 4);

/** The CSP the deployed site sends for a given path. */
function policyFor(path) {
  return CSP[path];
}

/** Serve the page with its production CSP attached. */
async function withPolicy(page, path, file) {
  await page.route(`**${path}`, async (route) => {
    if (route.request().resourceType() !== 'document') return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      headers: { 'Content-Security-Policy': policyFor(path) },
      body: readFileSync(join(REPO, 'docs', file), 'utf8'),
    });
  });
}

test('the app runs under its own Content-Security-Policy', async ({ page }) => {
  const violations = [];
  page.on('console', (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) violations.push(m.text());
  });
  page.on('pageerror', (e) => violations.push('pageerror: ' + e.message));

  await isolate(page);
  await withPolicy(page, '/index.html', 'index.html');
  await page.goto('/index.html');

  // If the inline script hash were wrong, the app would never define its
  // handlers and this search would find nothing.
  await page.fill('#q', placed.n[0]);
  await expect(page.locator('.ri', { hasText: placed.n[0] }).first()).toBeVisible();

  expect(violations, `CSP blocked something the app needs:\n${violations.join('\n')}`).toEqual([]);
});

test('the policy blocks an injected script, which is the point', async ({ page }) => {
  // House names are written into the results with innerHTML and escaped by
  // hand. This proves that if that escaping ever failed, the injected script
  // still would not run.
  await isolate(page);
  await withPolicy(page, '/index.html', 'index.html');
  await page.goto('/index.html');

  const ran = await page.evaluate(() => {
    window.__pwned = false;
    const s = document.createElement('script');
    s.textContent = 'window.__pwned = true;';
    document.body.appendChild(s);
    return window.__pwned;
  });
  expect(ran, 'an injected inline script executed under the CSP').toBe(false);
});

test('every generated page carries the security headers', () => {
  for (const path of ['/', '/index.html', '/how-it-works.html', '/privacy.html']) {
    const policy = policyFor(path);
    expect(policy, path).toContain("default-src 'self'");
    expect(policy, path).toContain("object-src 'none'");
    expect(policy, path).toContain("frame-ancestors 'none'");
    expect(policy, `${path} must not weaken script-src`).not.toContain("script-src 'self' 'unsafe-inline'");
  }
  expect(middleware).toContain('"X-Content-Type-Options": "nosniff"');
  expect(middleware).toContain('"Referrer-Policy": "no-referrer"');
});
