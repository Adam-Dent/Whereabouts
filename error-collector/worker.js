// Whereabouts field-failure collector.
//
// The app runs offline on phones in rural areas, where the failures that matter
// most (a saved map that will not render, a cache that has been evicted) are
// exactly the ones nobody ever reports. This endpoint receives a fixed set of
// named events so those failures are visible.
//
// It accepts NOTHING free-form. Every field is checked against a closed list
// below and the request is dropped otherwise, so no search term, house name,
// URL or stack trace can reach the dataset even by accident. No IP is stored.
// The platform is coarsened to ios / android / other and the raw user agent is
// discarded. That is what the app's privacy page promises, enforced here rather
// than merely intended in the client.

// Every event the app is allowed to report. Anything else is rejected.
const CODES = new Set([
  "map-image-recovered", // direct load failed, the cache had it: worker was not controlling
  "map-image-fallback-failed", // cached copy existed but could not be decoded
  "map-image-missing-online", // online, not cached, still failed: a real 404 or network fault
  "area-save-failed", // one or more maps failed while saving an area
  "quota-exceeded", // the browser refused to store more
  "houses-json-unavailable", // house list neither online nor in the cache
  "houses-json-parse-failed", // house list present but unreadable
  "sw-register-failed", // no service worker, so no offline at all
]);

// Counts are bucketed, never exact, so nothing here can single anyone out.
const QUANTITIES = new Set(["", "1", "2-5", "6-20", "21-100", "100+"]);
const ONLINE = new Set(["", "on", "off"]);

const CORS = {
  "Access-Control-Allow-Origin": "https://whereabouts.adamdent.uk",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Max-Age": "86400",
};

// Coarse platform only. Enough to tell an iOS storage-eviction pattern from an
// Android one, not enough to identify a device.
function platformOf(ua) {
  if (/iPhone|iPad|iPod/i.test(ua)) return "ios";
  if (/Android/i.test(ua)) return "android";
  return "other";
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    if (request.method !== "GET") return new Response("Method not allowed", { status: 405, headers: CORS });

    const url = new URL(request.url);
    if (url.pathname !== "/report") return new Response("Not found", { status: 404, headers: CORS });

    const p = url.searchParams;
    const code = p.get("c") || "";
    const quantity = p.get("q") || "";
    const online = p.get("o") || "";
    const version = p.get("v") || "";
    const internal = p.get("i") === "1";

    // Reject rather than coerce: a malformed report is a bug worth seeing as a
    // 400 in the Worker logs, not a silently mangled row in the dataset.
    if (!CODES.has(code)) return new Response("Unknown code", { status: 400, headers: CORS });
    if (!QUANTITIES.has(quantity)) return new Response("Bad quantity", { status: 400, headers: CORS });
    if (!ONLINE.has(online)) return new Response("Bad online flag", { status: 400, headers: CORS });
    if (version && !/^[0-9.]{1,12}$/.test(version)) return new Response("Bad version", { status: 400, headers: CORS });

    if (env.ERRORS) {
      env.ERRORS.writeDataPoint({
        blobs: [
          code,
          version,
          quantity,
          online,
          platformOf(request.headers.get("User-Agent") || ""),
          (request.cf && request.cf.country) || "",
          internal ? "internal" : "public",
        ],
        doubles: [1],
        indexes: [code],
      });
    }
    // 204 with no body: the client never needs to read anything back.
    return new Response(null, { status: 204, headers: { ...CORS, "Cache-Control": "no-store" } });
  },
};
