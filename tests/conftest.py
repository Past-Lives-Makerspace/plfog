"""A stand-in registration for spec D's ``wiki.page_verified``, for spec B's specs only.

Brief §9.1 gives that event to **spec D** — the Trigger, the ``wiki_page_contributors``
resolver, the copy, and the period shape. Spec B *calls* it and registers nothing, because
B and D build in parallel and two registrations of one key is a collision, not a safety
net. ``core.events.registry.get_event`` raises ``KeyError`` on an unknown key (it fails
loudly on purpose), so B's Verify path cannot run until D's registration lands, and **D's
PR merges before B's**.

This module exists so B's own specs can exercise the real ``emit()`` machinery in the
meantime. It registers a MINIMAL stand-in for the duration of one test and removes it
again; nothing here ships in application code, and the moment D's registration exists this
fixture becomes a no-op that can be deleted along with its imports.

The stand-in deliberately routes to ``FOG_ADMINS`` rather than guessing at D's resolver:
B has no business asserting D's audience, only that its own call reaches the spine intact.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

WIKI_PAGE_VERIFIED = "wiki.page_verified"


@pytest.fixture
def stub_page_verified_event() -> Iterator[None]:
    """Register a stand-in ``wiki.page_verified`` for one test, then take it back out."""
    from core.events import registry

    if WIKI_PAGE_VERIFIED in registry._BY_KEY:  # pragma: no cover - true once spec D lands
        yield
        return

    stub = registry.EventType(
        key=WIKI_PAGE_VERIFIED,
        label="A page you helped write was verified",
        description="Someone with authority read a page you contributed to and stood behind it.",
        category="Guilds",
        recipient=registry.Recipients.FOG_ADMINS,
        channels=(registry.ChannelSpec(registry.Channel.IN_APP, registry.ChannelDefault.ON),),
        activity_kind=None,
    )
    registry.EVENTS.append(stub)
    registry._BY_KEY[WIKI_PAGE_VERIFIED] = stub
    try:
        yield
    finally:
        registry.EVENTS.remove(stub)
        del registry._BY_KEY[WIKI_PAGE_VERIFIED]
