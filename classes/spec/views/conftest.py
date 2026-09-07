"""Shared helpers for the roster-actions-menu specs.

The roster page renders "Mark as Paid" in the Paid-column help bubble on every
row, and roster_modals.html renders confirm titles plus a "Remove" confirm
button on the same page; "Remove Student" contains "Remove" as a substring. So a
bare full-page substring assertion cannot tell a menu item apart from that noise.
``menu_region`` slices out just one row's ``.pl-row-menu`` markup (up to its
trailing ``<script>``) so presence/absence assertions mean what they say.
"""

from __future__ import annotations

import pytest


def _menu_region(html: str, row_dom_id: str) -> str:
    """Return the .pl-row-menu markup for the row whose ``<tr>`` id is ``row_dom_id``.

    Args:
        html: The full rendered page (or HTMX row-swap fragment).
        row_dom_id: The row's DOM id, e.g. ``reg-row-42`` or ``wl-row-42``.

    Returns:
        The kebab wrapper's HTML — trigger + menu items — excluding the component's
        Alpine factory ``<script>`` (which contains the literal ``role="menuitem"``).
    """
    anchor = f'id="{row_dom_id}"'
    start = html.index(anchor)
    row = html[start : html.index("</tr>", start)]
    menu_start = row.index('class="pl-row-menu"')
    script_at = row.find("<script", menu_start)
    end = script_at if script_at != -1 else len(row)
    return row[menu_start:end]


@pytest.fixture
def menu_region():
    """The row-menu slicer as a callable: ``menu_region(page_html, row_dom_id)``."""
    return _menu_region
