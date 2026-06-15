"""Maps a guild/category name to its SVG logo file prefix in static/img/guild_logos/.

Logos ship as ``<prefix>_color.svg`` and ``<prefix>_bw.svg``. Both guilds and
class categories resolve a prefix from their name through this single map so
the same artwork represents the same craft everywhere in the app.
"""

from __future__ import annotations

# Case-insensitive name substring → logo file prefix.
_NAME_TO_PREFIX: dict[str, str] = {
    "art framing": "art_framing",
    "ceramics": "ceramics",
    "events": "events",
    "food independence": "food_independence",
    "garden": "garden",
    "glass": "glass",
    "jewelry": "jewelers",
    "jeweler": "jewelers",
    "leather": "leatherwork",
    "metal": "metalworking",
    "prison": "prison_outreach",
    "tech": "tech",
    "textile": "textiles",
    "visual": "visual_arts",
    "wood": "woodworking",
    "writer": "writers",
    "writing": "writers",
}


def logo_prefix_for(name: str | None) -> str | None:
    """Return the guild_logos file prefix matching ``name``, or None if none match."""
    if not name:
        return None
    lowered = name.lower()
    for key, prefix in _NAME_TO_PREFIX.items():
        if key in lowered:
            return prefix
    return None
