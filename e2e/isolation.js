// Network isolation for the browser tests.
//
// The app under test is the real deployed artefact: it carries the live
// Counterscale snippet and the live field-failure collector URL. A test run
// must never appear in either dataset, so this is a DEFAULT DENY rather than a
// blocklist of the two known endpoints. Anything added later is caught with no
// changes here, and the assertion below turns "nothing left the machine" from
// an assumption into something each test proves.
//
// Counterscale happens to skip localhost of its own accord, but that guard
// reads window.location.hostname, so it would not survive the test server
// being bound to anything else, and the error collector has no such guard at
// all. Neither is relied on.

const ALLOWED_HOST = '127.0.0.1';

/**
 * Block every request leaving the test server's origin, and record attempts.
 * Returns the recorder so a test can assert on what the app *would* have sent.
 */
export async function isolate(page) {
  const attempted = [];

  // Routed on the CONTEXT, not the page. A page-level route does not see
  // requests re-issued by a service worker, and this app's worker handles every
  // fetch, so page.route() silently misses exactly the traffic that matters.
  //
  // Matched by predicate so that same-origin requests are never routed at all:
  // routing them and calling continue() puts Playwright in the path of the
  // service worker, and an offline continue() then fails at the network instead
  // of falling through to the worker's cache.
  await page.context().route(
    (url) => url.hostname !== ALLOWED_HOST,
    async (route) => {
    const url = new URL(route.request().url());

    attempted.push(url.href);

    // The field-failure collector answers 204 with no body. Fulfilling it
    // locally lets the queue-and-flush behaviour be tested for real, against
    // the real production URL in the real client code, without a packet
    // leaving the machine.
    if (url.pathname === '/report') {
      return route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*' } });
    }

      return route.abort();
    },
  );

  return {
    /** Everything the page tried to send outside the test server. */
    all: () => attempted.slice(),
    /** Field-failure reports the app tried to send, parsed into their fields. */
    reports: () =>
      attempted
        .filter((u) => u.includes('/report?'))
        .map((u) => Object.fromEntries(new URL(u).searchParams)),
    /** Anything outbound that is not a field-failure report. */
    other: () => attempted.filter((u) => !u.includes('/report?')),
  };
}

/**
 * Assert the app sent nothing to the analytics collector.
 * Every test calls this: it is the guarantee, not a nicety.
 */
export function expectNoAnalytics(net, expect) {
  const analytics = net.all().filter((u) => u.includes('counterscale') || u.includes('/collect'));
  expect(analytics, `analytics hits attempted: ${analytics.join(', ')}`).toEqual([]);
}
