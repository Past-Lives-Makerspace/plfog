"""Maps a guild/category name to its SVG logo file prefix in static/img/guild_logos/.

Logos ship as ``<prefix>_color.svg`` and ``<prefix>_bw.svg``. Both guilds and
class categories resolve a prefix from their name through this single map so
the same artwork represents the same craft everywhere in the app.
"""

from __future__ import annotations

import re

# Case-insensitive name substring → logo file prefix.
_NAME_TO_PREFIX: dict[str, str] = {
    "art framing": "art_framing",
    "ceramics": "ceramics",
    "emergency response": "erma",
    "events": "events",
    "food independence": "food_independence",
    "garden": "garden",
    "glass": "glass",
    "jewelry": "jewelers",
    "jeweler": "jewelers",
    "leather": "leatherwork",
    "metal": "metalworking",
    "music": "music",
    "mutual aid": "erma",
    # "printmak", not "print": a "3D Printing" name must not take the press wheel.
    "printmak": "printmaking",
    "prison": "prison_outreach",
    "tech": "tech",
    "textile": "textiles",
    "visual": "visual_arts",
    "wood": "woodworking",
    "writer": "writers",
    "writing": "writers",
}

# Whole-word name → logo file prefix, for short names a substring would misfire on:
# "ERMA" must not take "Permaculture".
_WORD_TO_PREFIX: dict[str, re.Pattern[str]] = {
    "erma": re.compile(r"\berma\b", re.IGNORECASE),
}


def logo_prefix_for(name: str | None) -> str | None:
    """Return the guild_logos file prefix matching ``name``, or None if none match."""
    if not name:
        return None
    lowered = name.lower()
    for key, prefix in _NAME_TO_PREFIX.items():
        if key in lowered:
            return prefix
    for prefix, pattern in _WORD_TO_PREFIX.items():
        if pattern.search(name):
            return prefix
    return None
