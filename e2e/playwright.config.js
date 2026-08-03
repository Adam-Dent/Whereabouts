import { defineConfig, devices } from '@playwright/test';

// Bound to 127.0.0.1 deliberately, not 0.0.0.0: see e2e/isolation.js. The
// tests do not depend on it, but it means Counterscale's own localhost guard
// lines up with the request blocking rather than cutting across it.
const PORT = 8788;

export default defineConfig({
  testDir: '.',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'on-first-retry',
    launchOptions: {
      // The hard backstop, and the one that actually holds.
      //
      // Request routing alone is not enough: a page-level route cannot see a
      // request re-issued by the service worker, and this app has a service
      // worker that handles every fetch. A field-failure report therefore
      // escaped routing entirely and reached the live collector during
      // development of this suite.
      //
      // This blackholes DNS for every host except the test server, inside
      // Chromium's network stack and below the service worker, so no code path
      // in the page or the worker can reach the network whatever it does.
      // Routing still works, because interception happens before resolution.
      args: ['--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
    },
  },
  projects: [
    // A phone, because that is where this app is actually used: in a field, in
    // the rain, on a handset. Desktop Chrome would test a layout almost nobody
    // sees.
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    command: `python3 -m http.server ${PORT} --bind 127.0.0.1 --directory ../docs`,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: !process.env.CI,
    stdout: 'ignore',
  },
});
