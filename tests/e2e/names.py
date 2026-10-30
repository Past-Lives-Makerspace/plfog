"""Accessible name matchers for the Playwright specs.

``exact("Reservations")`` matches the whole name the way ``exact=True`` does, and also
accepts the New! badge (#1007) that sits inside a label while an entry for its key is
unseen and within 30 days. The pill is real text, so a screen reader hears it and
Playwright folds it into the name ("Reservations New!"); ``name="Reservations",
exact=True`` therefore stops matching for a month after every release that names the
key. Use this on any label that can carry the badge: a sidebar link, a tab, a card title.
"""

from __future__ import annotations

import re


def exact(label: str) -> re.Pattern[str]:
    """The whole accessible name ``label``, with or without a trailing New! badge."""
    return re.compile(rf"^{re.escape(label)}(\s*New!)?$")
