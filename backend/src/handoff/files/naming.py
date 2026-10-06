"""Display-name collision handling (ADR-020, FR-028): photo.jpg -> photo(1).jpg -> photo(2).jpg."""

from __future__ import annotations

import os
from collections.abc import Iterable


def unique_display_name(name: str, existing: Iterable[str]) -> str:
    taken = {e.casefold() for e in existing}
    if name.casefold() not in taken:
        return name
    stem, ext = os.path.splitext(name)
    n = 1
    while f"{stem}({n}){ext}".casefold() in taken:
        n += 1
    return f"{stem}({n}){ext}"
