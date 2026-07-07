"""Slug/ID generation utilities (spec §6.1)."""

from __future__ import annotations

import re
import unicodedata


def slugify(text: str) -> str:
    """Lowercase, ASCII, spaces+punctuation to hyphens, collapse repeats."""
    text = text.lower().strip()
    # Normalize unicode (e.g. accented chars)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    # Replace non-alphanumeric with hyphens
    text = re.sub(r"[^a-z0-9]+", "-", text)
    # Collapse and strip leading/trailing hyphens
    text = text.strip("-")
    return text


def house_id(sheet_id: str, map_number: int) -> str:
    return f"{sheet_id}-{map_number}"


def normalize_name(name: str) -> str:
    """Normalize for search indexing (spec §6.5)."""
    # Normalize unicode: curly apostrophes -> straight, NFKD
    name = unicodedata.normalize("NFKD", name)
    name = name.replace("’", "'").replace("‘", "'")
    name = name.encode("ascii", "ignore").decode()
    name = name.lower()
    # Strip punctuation except alphanumerics and spaces; keep digits
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name
