"""What search engines read off a public page (#1259): the shared place, title and text rules.

Import light (no models), so the class and event models and the ``seo`` template tags share it.
"""

from __future__ import annotations

from typing import Any

#: A page ``<title>`` runs at most this many characters, which is about what Google shows.
TITLE_LIMIT = 60

#: A meta description runs at most this many characters.
DESCRIPTION_LIMIT = 160

#: The makerspace as a schema.org Place, the location of every class and event's JSON-LD.
MAKERSPACE_PLACE: dict[str, Any] = {
    "@type": "Place",
    "name": "Past Lives Makerspace",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "2808 SE 9th Ave",
        "addressLocality": "Portland",
        "addressRegion": "OR",
        "postalCode": "97202",
        "addressCountry": "US",
    },
}


def truncate_at_word(text: str, limit: int) -> str:
    """``text`` unchanged when it fits in ``limit`` characters, else cut at a word and ended with ``…``."""
    if len(text) <= limit:
        return text
    window = text[: limit - 1]
    cut = window.rsplit(" ", 1)[0] if " " in window else window
    return f"{cut.rstrip(' -–—,;:.|')}…"


def page_title(title: str, brand: str) -> str:
    """The ``<title>`` text: the page's own title, then `` - <brand>`` once, never past :data:`TITLE_LIMIT`.

    The brand is added only when the title does not already name it and the result still fits;
    a title that would run long is cut at a word instead.
    """
    text = " ".join(title.split())
    suffix = f" - {brand}"
    if brand.lower() not in text.lower() and len(text) + len(suffix) <= TITLE_LIMIT:
        text += suffix
    return truncate_at_word(text, TITLE_LIMIT)
