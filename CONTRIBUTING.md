# Contributing to Whereabouts

Thanks for your interest. Whereabouts turns Dr A Colin Day's free North Yorkshire
village maps into a searchable, offline finder for houses that have a name but no
number. See the [README](README.md) for how it all fits together.

## Ways to help

- **Report a wrong or missing location.** Open an issue with the house name and
  village. Please do not include anyone's personal details.

This is a one-person project, and I maintain it solo, including all the house
placements. I'm not looking for code contributions or collaborators, so please
don't open a pull request or get in touch offering to help with code or placement
work; I won't be able to take it up. Location reports (above) are the one thing
that's genuinely useful from anyone else.

## Running it locally

Prerequisites: Python 3.12, [uv](https://astral.sh/uv), and Node 18+.

```bash
cd etl
uv sync
uv run whereabouts-etl                          # the discover/parse/render/emit pipeline
uv run uvicorn etl.place_tool:app --reload      # the placement tool at http://localhost:8000
uv run whereabouts-build-pwa                     # build the static app into docs/
```

## House rules

- British English.
- **No em dashes** in the user-facing pages (`index.html`, `how-it-works.html`,
  `privacy.html`, `sw.js`). The build fails if one appears. Use commas, colons,
  semicolons, parentheses, or plain hyphens.
- The user-facing pages are generated from strings in `etl/src/etl/pwa.py`, not
  edited directly in `docs/`. Change the source, not the build output.
- Keep it accessible (WCAG 2.1 AA): controls keyboard-operable and labelled, images
  with meaningful alt text, sufficient colour contrast.

## Licensing

By contributing you agree that your contributions are licensed under the project's
existing terms:

- **Code**: MIT (see [LICENSE](LICENSE)).
- **House location data** (`docs/houses.json`, `data/placements/`): CC BY-SA 4.0.
- **Colin Day's map drawings** are his own work, made freely available for copying,
  and are not covered by the above. Each map is shown in full with its own
  attribution.

## Privacy

Whereabouts holds no personal data, and it should stay that way. Do not add
attributable, house-level search logging; keep any analytics anonymous and
aggregated. A log of which houses someone searched can be sensitive.
