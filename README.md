# Whereabouts

A finder for named rural properties in North Yorkshire.

**Live app:** [whereabouts-app.pages.dev](https://whereabouts-app.pages.dev)

---

## The problem

A lot of rural properties in North Yorkshire are identified by name only — "Rose Cottage", "Manor House Farm", "The Old Rectory" — with no street number. Neither Google Maps nor Apple Maps knows most of these names. Properties frequently don't appear at all, and when they do, the pin is often wrong.

Dr A Colin Day publishes beautifully detailed village maps for North Yorkshire as free PDFs ([colinday.co.uk/maps/](https://colinday.co.uk/maps/)). Each PDF is a hand-drawn village plan with every named property given a number on the map and a corresponding entry in a printed legend.

Whereabouts makes that information searchable and navigable: type a house name, see it highlighted on the village drawing, and open native maps for turn-by-turn directions.

---

## What I built

### The ETL pipeline (`/etl`)

The pipeline is a Python package (Python 3.12, managed with `uv`) that turns Colin Day's PDFs into a structured dataset.

**Discovery.** The pipeline scrapes a district index page from colinday.co.uk, finds every PDF link, normalises the URLs, and downloads the PDFs — caching them locally by SHA-256 hash so re-runs are fast. `discover.py`'s `DISTRICT_INDEXES` now lists all 11 North Yorkshire districts; `whereabouts-etl --district "<name>"` scopes a run to just one, so each new district can be processed and its parse-quality checked before moving to the next, without disturbing what's already been done for the others. A `data/dist/coverage.md` report (regenerated every run) tracks discovery/parse/placement status per district.

Village names aren't unique across North Yorkshire — Newby, Carlton, Kirkby, Melmerby and others all recur in more than one district. Since sheet IDs are also placement filenames (and can't be renamed retroactively without breaking already-placed houses), a same-named village in a newly-processed district would otherwise silently overwrite an earlier district's sheet of the same name. Discovery now checks new sheet IDs against everything already emitted for other districts and appends the district name to disambiguate a collision (`newby` / `newby-craven`), logging it when it happens.

**Parsing.** This was the technically interesting part. Each PDF has three zones of integer text that all need to be told apart:

1. The *numbered legend* — a right-aligned column of numbers each followed by a house name.
2. The *alphabetical cross-reference* — names listed alphabetically with dot-leaders pointing to their map number (useful for aliases: some houses appear under two names).
3. The *on-map labels* — numbers scattered across the drawing itself, one per house, whose positions indicate where the house sits.

I used `pdfplumber` to extract characters and words, then identified the legend column by its right-aligned geometry rather than line-by-line text clustering (which kept getting confused by scattered map labels landing on the same horizontal band as a legend row). The cross-reference is identified by the presence of dot-leader characters immediately to the left of the page numbers.

Several villages required specific fixes:

- **Eppleby and East Layton** — the legend index number and the street address number were printed with only a 1.7pt gap, which fell below pdfplumber's default word-merging threshold. The parser now detects this condition (suspiciously many duplicate legend names) and retries with a tighter tolerance.
- **Croft-on-Tees** — the same gap issue, but the initial wrong parse produced all-empty names, so a second suspicious-legend check detects when all found names filter to nothing.
- **Forcett** — letter-spaced cross-reference text (like `c.h.u.r.c.h`) was bleeding into legend names on the same row; filtered by detecting short fragments with embedded dots.
- **Melsonby** — cross-reference aliases were being reconstructed as garbage like `59MoorRoad`; filtered by detecting concatenated digit-plus-capital-letter tokens.
- **Brompton-on-Swale (North)** — the legend table had two section-header rows that the parser was treating as houses; removed by detecting names that appear as a bare suffix of ten or more other entries.
- **Stapleton** — a pure raster scan with no extractable text at all. I transcribed the 56 house names manually and added them as a fixture file (`data/fixtures/stapleton.json`); the parser checks for this before attempting PDF extraction.
- **Angram, Ivelet** (Swaledale) — the legend number was printed close enough to the house name (under the default word-merge tolerance) that it never registered as its own word at all, so the sheet found 0-1 houses instead of a suspicious duplicate-looking legend. The tighter-tolerance retry now triggers on a thin/missing legend, not just on duplicate names.
- **Carlton, Ivelet** — on densely-set sheets, cross-reference alias parsing could merge two dot-leader lines into one, producing garbage like `2 Baygante Carlton Boarding Kennels`. Real aliases in this dataset are short (`Church`, `St Mary's Church`, `1 Eryholme Lane`), so aliases longer than 3 words are now rejected.

After parsing, the pipeline renders each PDF page to a PNG at 200 DPI using `pymupdf`, geocodes the village centroid via Nominatim, and emits the full dataset.

**Outcome:** 865 sheets, 42,336 houses across all 11 North Yorkshire districts, all parsed and rendered (Richmondshire was the first and most heavily hardened: 56 sheets, 3,225 houses, 44 villages).

**Running the pipeline:**

```bash
cd etl
uv sync
uv run whereabouts-etl                          # every configured district
uv run whereabouts-etl --district "Wensleydale"  # just one, repeatable for several
```

### The placement tool (`/etl/src/etl/place_tool.py`)

The original plan was to georeference the village drawings using control points and an affine transform. In practice, the maps don't have enough identifiable fixed points, and the coordinate error was too high to be useful for locating the right house in a terrace.

Instead, I built a direct placement tool. It's a FastAPI app serving a Leaflet page that overlays the village drawing as a semi-transparent image on Esri satellite imagery. I align the drawing over the satellite view by dragging it into position and scaling it with a corner handle or the scroll wheel, then click directly on each house to record its coordinates.

Each click captures:
- A latitude/longitude from the Leaflet map (for navigation)
- A pixel position within the drawing image (for drawing the highlight ring in the app)

Placements are saved to `data/placements/<sheet_id>.json` — written atomically and auto-committed to git on every Save (with a best-effort background push), so placement work is never more than one sheet away from being safely in history. The ETL merges them into `houses.json` on the next run.

**Running the placement tool:**

No terminal needed: double-click **`Start Placement Tool.command`** in the repository root. It starts the server, opens the browser by itself, and stops when the Terminal window is closed.

Or from a terminal:

```bash
cd etl
uv run uvicorn etl.place_tool:app --reload
# then open http://localhost:8000
```

With 11 districts now in scope, the tool has two dropdowns: District, then Village within it. The District list puts the three priority districts for placement first (currently Wensleydale, Swaledale and Arkengarthdale, Hambleton (West) — set in `DISTRICT_PRIORITY` in `place_tool.py`), then the rest alphabetically. Within a district, the Village dropdown is sorted by number of non-numbered houses (the ones most likely to be unlocatable by other means), putting the most useful work first. The browser remembers the last district/village you had open.

Three stat boxes above the dropdowns show percent-complete at a glance: overall (every district), the selected area, and the currently open map — the last one updates live as you click, the other two after each Save.

### The PWA (`/etl/src/etl/pwa.py`)

A static Progressive Web App built with vanilla JS. The build step generates a `docs/` directory containing `index.html`, `sw.js`, `manifest.json`, a normalised `houses.json` (~660 KB gzipped for all 42k houses), and every village drawing as a content-hashed lossless WebP (129 MB total, down from 376 MB of PNGs).

Features:
- **Fuzzy search** via Fuse.js (bundled locally), indexed over house names and village names, plain relevance order (an earlier rule that always ranked named houses above numbered addresses was removed — it was burying an exact numbered-address search like "123 Street Farm" under unrelated named houses). Unplaced houses are searchable too, shown with a grey dot; directions for those fall back to the village centre.
- **Detail view** with the village map image and a ring at the house position — solid red for a hand-placed house, amber dashed when the position comes from the parsed map label and the exact coordinates haven't been placed yet. When several houses in a village share a name (283 such groups — Dalton has four Rose Cottages), a warning shows on the result rows and the detail view.
- **Navigation** via platform-detected deep link (Apple Maps on iOS, geo: URI on Android, Google Maps otherwise)
- **Offline, selectively** — any map you view is cached automatically; whole districts can be saved from the ⓘ sheet ("Save maps for offline", ~10 MB for Wensleydale's 63 maps). Nothing bulk-downloads unasked. The ⓘ sheet also shows live per-district placement progress bars. Content-hashed image filenames mean a revised map is picked up automatically and its stale cache entry pruned.
- **Install to home screen** on both iOS and Android

**Building and deploying:**

No terminal needed: double-click **`Publish App Update.command`** in the repository root after a placement session. It rebuilds the dataset and deploys to Cloudflare Pages in one go.

Or by hand:

```bash
cd etl
uv run whereabouts-build-pwa
# then:
npx wrangler pages deploy docs/ --project-name whereabouts-app
```

---

## What I did manually

### House placements

The coordinates for each house have to be placed by hand using the placement tool. Each village takes roughly 5–15 minutes depending on size and how clearly the properties are visible on satellite imagery. Richmondshire (the first district) is fully placed and Wensleydale is ~44% done; the other 9 North Yorkshire districts are parsed and rendered but not yet started, prioritised Swaledale and Arkengarthdale → Hambleton (West) next. Live progress: `data/dist/coverage.md`, the stat boxes in the placement tool, or the progress bars in the app's ⓘ sheet.

### Stapleton

Stapleton's PDF is a scanned photograph rather than a vector drawing. I transcribed all 56 house names directly from the PDF and entered them into `data/fixtures/stapleton.json`.

### Reviewing parse quality

After running the full parse, I eyeballed the output for obvious errors and fixed the per-village issues listed above. The pipeline produces a `data/dist/report.md` listing any sheets where the house count didn't match the number of on-map labels.

---

## Repository layout

```
Start Placement Tool.command    Double-click: run the placement tool, browser opens itself
Publish App Update.command      Double-click: rebuild the app data and deploy it live

/etl                    Python pipeline + placement tool + PWA generator
  make_icons.py         Generates the compass app icons into docs/
  src/etl/
    main.py             ETL entry point (--district flag for scoped/incremental runs)
    parse.py            PDF legend and map-label extraction
    place_tool.py       FastAPI placement + browser app (atomic saves, git auto-commit)
    render.py           PDF → PNG rendering
    discover.py         PDF discovery and download; DISTRICT_INDEXES for all 11 districts
    coverage.py         Per-district discovery/parse/placement status report
    emit.py             Dataset assembly and output
    pwa.py              Static PWA generator (WebP conversion, houses.json v2, em-dash guard)

/docs                   Static PWA build (deployed to Cloudflare Pages; images/ not committed)

/data
  /.cache/pdfs/         Downloaded PDFs (not committed — keep an off-machine copy!)
  /dist/                Generated outputs (houses/sheets/villages.json, report.md, coverage.md are committed; images/ is not)
    houses.json         All houses with names, positions, coordinates
    sheets.json         Sheet metadata
    villages.json       Village list with centroids
    images/             Rendered PNGs
    report.md           Per-run parse quality report
    coverage.md         Per-district discovery/parse/placement status, regenerated every run
  /placements/          Human-placed coordinates (version controlled, auto-committed on Save)
  /fixtures/            Manual house lists for raster sheets + name_overrides.json corrections
```

---

## Languages and technologies

Two main languages, split by job:

**Python 3.12** runs everything local: the pipeline that downloads and reads Colin Day's PDFs (`pdfplumber`), the renderer that turns them into images (`PyMuPDF`, `Pillow`), the placement tool's server (`FastAPI`), and the builder that assembles the app for deployment. Dependencies are managed with `uv`.

**JavaScript, HTML, and CSS** run everything in a browser: the app itself (one HTML page — fuzzy search via `Fuse.js`, offline caching via a service worker) and the placement tool's map interface (`Leaflet` over Esri satellite imagery). Deliberately framework-free ("vanilla JS"): nothing to update, nothing to break.

The data itself is **JSON** — plain, human-readable text files. Open anything in `data/placements/` and you can read it; the dataset survives any change of technology. The double-click launcher is a small **zsh** shell script, and the docs are Markdown.

## Prerequisites

| Tool    | Version | Install |
|---------|---------|---------|
| Python  | 3.12    | homebrew or pyenv |
| uv      | latest  | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node    | 18+     | homebrew or nvm |

---

## Legal

### Code licence

The software in this repository is released under the [MIT Licence](LICENSE).

### Data

The village map drawings are the work of [Dr A Colin Day](https://colinday.co.uk/maps/) and are made freely available for copying without charge. They are not covered by the MIT licence.

The maps are shown in full, each carrying its own attribution line (Ordnance Survey, Google Maps, or OpenStreetMap as applicable — Dr Day's sources have varied over the years, and only his older maps were made under his former OS contract). Per his advice, no blanket source attribution is added on top.

The house coordinates in `docs/houses.json` were derived by the author from personal observation of Esri World Imagery satellite imagery. This coordinate dataset (the hand-placed positions in `docs/houses.json` and `data/placements/`) is licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/): reuse is free, including commercially, provided you credit "Adam Dent, Whereabouts" and keep any adapted dataset under the same licence. This is the deliberate split: MIT for the replaceable code, share-alike for the irreplaceable placements.
