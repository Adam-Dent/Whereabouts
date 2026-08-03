"""Write a tiny stand-in for every map image the dataset references.

The rendered maps are 129 MB and deliberately not in the repository, so a fresh
checkout has houses.json listing 865 images that do not exist. That is fine for
the Python tests, which skip, but it disables exactly the browser tests worth
having: caching a map, saving a district, rendering offline.

These placeholders are real, valid images at the exact paths the dataset names,
so those tests run against the real code path. They are 1x1 pixels: the tests
assert that an image decoded and that the right file was cached, never what the
map looks like. Refuses to overwrite anything, so it can never damage a real
build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image


def main() -> None:
    docs = Path(sys.argv[1] if len(sys.argv) > 1 else "docs")
    data = json.loads((docs / "houses.json").read_text())
    out = docs / "images"
    out.mkdir(parents=True, exist_ok=True)

    written = skipped = 0
    for sheet in data["sheets"].values():
        name = sheet.get("img")
        if not name:
            continue
        path = out / name
        if path.exists():
            skipped += 1
            continue
        Image.new("RGB", (1, 1), (255, 255, 255)).save(path, "WEBP")
        written += 1
    print(f"placeholder maps: {written} written, {skipped} already present")


if __name__ == "__main__":
    main()
