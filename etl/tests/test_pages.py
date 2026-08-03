"""The generated pages, and the promises they make.

Every user-facing page in this project is generated from a Python string
literal in pwa.py, with no HTML tooling and no linter that understands it. That
makes these checks the only thing standing between a typo and a broken deploy,
which is why the em-dash guard lived in CI as a shell heredoc before this file
existed. It is folded in here so it runs on every local test run too, not only
on push.

The parity checks matter more than they look. The privacy page makes specific,
checkable promises about what the app sends, and those promises are kept by two
files in two languages in two directories agreeing with each other. Nothing but
a test can hold them together.
"""

from __future__ import annotations

import re

import pytest

from etl import pwa
from etl.slugs import normalize_name

from conftest import REPO

PAGES = {
    "index.html": pwa._page_html,
    "how-it-works.html": lambda: pwa._HOW_PAGE,
    "privacy.html": lambda: pwa._PRIVACY_PAGE,
    "sw.js": lambda: pwa._SW_JS,
}


@pytest.mark.parametrize("name", sorted(PAGES))
def test_page_generates_non_empty(name: str) -> None:
    assert PAGES[name]().strip()


@pytest.mark.parametrize("name", sorted(PAGES))
def test_no_em_dashes(name: str) -> None:
    """Portfolio-wide house style, and the reason the build guard exists: the
    frontend is a Python string, so nothing else can check it."""
    page = PAGES[name]()
    for bad in ("—", "&mdash;", "&#8212;"):
        assert bad not in page, f"em dash ({bad}) in {name}"


@pytest.mark.parametrize("name", ["index.html", "how-it-works.html", "privacy.html"])
def test_tags_are_balanced_enough_to_render(name: str) -> None:
    """A crude structural check, but it catches the realistic failure: an
    unclosed div in a hand-written 1,000-line string literal."""
    page = PAGES[name]()
    for tag in ("div", "section", "p", "details"):
        opened = len(re.findall(rf"<{tag}[\s>]", page))
        closed = len(re.findall(rf"</{tag}>", page))
        assert opened == closed, f"{name}: {opened} <{tag}> vs {closed} </{tag}>"


def test_version_is_stamped_into_the_page() -> None:
    html = pwa._page_html()
    assert "__WW_VERSION__" not in html, "version placeholder left unreplaced"
    assert re.search(r"const VERSION = '\d+\.\d+\.\d+'", html)


def test_analytics_placeholders_are_all_replaced() -> None:
    html = pwa._page_html()
    for placeholder in ("__WW_SITE_ID__", "__WW_BASE__", "__WW_INTERNAL_HOST__", "__WW_ERR_URL__"):
        assert placeholder not in html, f"{placeholder} left unreplaced"


# ── The promises the privacy page makes ──────────────────────────────────────

def _collector_codes() -> set[str]:
    """The codes the deployed Worker will accept, read from its source."""
    src = (REPO / "error-collector" / "worker.js").read_text()
    block = re.search(r"const CODES = new Set\(\[(.*?)\]\)", src, re.S)
    assert block, "could not find the CODES set in error-collector/worker.js"
    return set(re.findall(r'"([a-z0-9-]+)"', block.group(1)))


def _client_codes() -> set[str]:
    """The codes the app can actually emit, read from its wwErr() call sites."""
    return set(re.findall(r"wwErr\('([a-z0-9-]+)'", pwa._PWA_PAGE))


def test_client_never_sends_a_code_the_collector_would_reject() -> None:
    """A code the app sends but the Worker does not know is a 400 and a report
    lost forever: the failure would be invisible in exactly the situation the
    reporting exists to make visible."""
    unknown = _client_codes() - _collector_codes()
    assert not unknown, f"app sends codes the collector rejects: {sorted(unknown)}"


def test_the_privacy_page_states_the_right_number_of_codes() -> None:
    """The page tells readers it reports "one of eight known problems". If the
    list grows and the sentence does not, the page is quietly lying."""
    n = len(_collector_codes())
    assert n == 8, f"the collector now accepts {n} codes; update the privacy page"
    assert "eight known problems" in pwa._PRIVACY_PAGE


def test_the_privacy_page_still_promises_no_free_text() -> None:
    """Guards the specific claims against a future edit that softens them by
    accident. If a claim is deliberately changed, this test should be changed
    with it, on purpose."""
    page = pwa._PRIVACY_PAGE
    for claim in (
        "no free text",
        "no stack trace",
        "cookieless",
        "no IP address",
    ):
        assert claim in page, f"the privacy page no longer says {claim!r}"


def test_the_error_client_sends_only_the_documented_fields() -> None:
    """The page says a report carries the code, version, a bucketed count and
    an online flag, and nothing else."""
    params = re.search(r"new URLSearchParams\(\{([^}]*)\}\)", pwa._PWA_PAGE)
    assert params, "could not find the error report's URLSearchParams"
    keys = set(re.findall(r"(\w+):", params.group(1)))
    assert keys == {"c", "v", "q", "o"}, f"unexpected fields in an error report: {keys}"


def test_no_reported_code_is_built_from_free_text() -> None:
    """Every code that can reach the wire must be a literal from the closed set.

    A template literal or a concatenation in a wwErr call is the one way a
    search term or house name could ever leak into a report, so the shape of
    the call site is checked rather than trusted. Forwarding a variable is
    allowed (`wwErr(code, ...)` inside a helper) because the literal it carries
    is checked at the call site that supplied it; a ternary between two
    literals is likewise fine.
    """
    calls = re.findall(r"wwErr\(([^;]*?)\)[;,\s]", pwa._PWA_PAGE)
    for call in calls:
        assert "`" not in call, f"template literal in a wwErr call: {call!r}"
        assert "+" not in call.split(",")[0], f"concatenation in a wwErr code: {call!r}"


def test_every_literal_code_in_the_app_is_one_the_collector_accepts() -> None:
    """Covers the codes reached through a ternary, which the plain call-site
    regex in _client_codes does not see."""
    accepted = _collector_codes()
    calls = re.findall(r"wwErr\(([^;]*?)\)[;,\s]", pwa._PWA_PAGE)
    literals = {lit for call in calls for lit in re.findall(r"'([a-z0-9-]+)'", call)}
    unknown = literals - accepted
    assert not unknown, f"app can send codes the collector rejects: {sorted(unknown)}"


# ── Cross-language agreement ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name",
    ["The Old Rectory", "St Mary's Church", "7 Manor Way", "Beck-Side Cottage", "Café"],
)
def test_js_name_normalisation_matches_the_python(name: str) -> None:
    """`norm()` in the PWA reimplements `normalize_name` so the payload need not
    ship a normalised duplicate of every name. If they drift, search stops
    matching the data it was built from. Compared by re-implementing the JS
    regex chain here, since there is no JS runtime in this suite."""
    import unicodedata

    s = unicodedata.normalize("NFKD", name)
    s = re.sub(r"[̀-ͯ]", "", s).lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    js_result = re.sub(r"\s+", " ", s).strip()
    assert js_result == normalize_name(name)


def test_service_worker_cache_names_match_the_page() -> None:
    """The page reads and writes the same Cache Storage buckets the worker does.
    A mismatch means saved maps silently stop being found."""
    for cache in ("whereabouts-images-v2", "whereabouts-data-v1", "whereabouts-shell-v1"):
        assert cache in pwa._SW_JS, f"{cache} missing from the service worker"
        assert cache in pwa._PWA_PAGE, f"{cache} missing from the page"


def test_built_docs_match_what_the_generator_produces() -> None:
    """Catches the classic mistake this project is set up to invite: editing
    docs/ by hand instead of the pwa.py string it is generated from. Such an
    edit survives until the next build silently overwrites it."""
    built = REPO / "docs" / "index.html"
    if not built.exists():
        pytest.skip("docs/index.html not built")

    # The version is derived from git history, so it moves with every commit and
    # the built file is legitimately behind between a commit and the next build.
    # Normalising it out keeps this test about hand-edits, which is its purpose.
    def strip_version(s: str) -> str:
        return re.sub(r"const VERSION = '[\d.]+'", "VERSION", s)

    assert strip_version(built.read_text()) == strip_version(pwa._page_html()), (
        "docs/index.html differs from the generator. Either it was hand-edited "
        "(edit etl/src/etl/pwa.py instead) or the build has not been re-run."
    )
