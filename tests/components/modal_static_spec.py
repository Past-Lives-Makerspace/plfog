"""BDD specs for the components/modal.html ``modal_static`` opt-out.

The Edit Hours modal teleports a nested delete-confirm to <body>; Alpine's teleport-blind
``click.outside`` would treat clicks in that confirm as "outside" and close the editor.
``modal_static`` drops the click-outside + window-Escape dismiss handlers so the editor
only closes via the X button, an in-body Cancel, or a close-modal dispatch. This is the
blocker fix — assert both branches at the component level.
"""

from __future__ import annotations

from django.template.loader import render_to_string


def describe_modal_static():
    def it_drops_the_dismiss_handlers_when_static():
        html = render_to_string(
            "components/modal.html",
            {"modal_id": "edit-hours-modal", "modal_title": "Edit Hours", "modal_size": "lg", "modal_static": True},
        )
        assert "@click.outside" not in html
        assert "@keydown.escape.window" not in html
        # The X-button close and the open/close dispatch listeners are untouched.
        assert "@close-modal.window" in html
        assert 'aria-modal="true"' in html

    def it_keeps_the_dismiss_handlers_by_default():
        html = render_to_string(
            "components/modal.html",
            {"modal_id": "plain-modal", "modal_title": "Plain"},
        )
        assert "@click.outside" in html
        assert "@keydown.escape.window" in html
