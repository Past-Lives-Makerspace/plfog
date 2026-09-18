"""Capture the #405 review screenshots: the Features tab and the three sidebar states.

Not a release-email capture — these go to ``docs/screenshots/405/`` in the working tree for a
human to look at before the PR opens. Run explicitly:

    DATABASE_URL=postgres://plfog:plfog@127.0.0.1:5433/plfog \\
      .venv/bin/pytest tests/e2e/feature_switch_screenshots_spec.py -m e2e -q --no-cov

Postgres rather than SQLite on purpose: a browser writing through the live server flakes on
local SQLite with a table lock, which shows up as a 500 in a screenshot and wastes an hour.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from membership.models import Guild
from tests.e2e.screenshot_seed import ADMIN_EMAIL, _seed
from tests.features import coming_soon, hide, turn_on

SHOT_DIR = Path("docs/screenshots/405")
VIEWPORT = {"width": 1280, "height": 900}
# The sidebar rail plus a sliver of page, identical across every sidebar shot so the states
# can be compared without allowing for a different crop.
SIDEBAR_CLIP = {"x": 0, "y": 0, "width": 460, "height": 900}


def _set_theme(page, live_server, theme: str) -> None:
    """Pin the viewer's theme via the pl_theme cookie base.html's inline script reads."""
    # url OR path, never both — Playwright rejects a cookie carrying the pair.
    page.context.add_cookies([{"name": "pl_theme", "value": theme, "url": live_server.url}])


def _save(page, name: str, **kwargs) -> Path:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    target = SHOT_DIR / f"{name}.png"
    target.write_bytes(page.screenshot(**kwargs))
    return target


@pytest.fixture()
def admin_page(live_server, page, login_via_code):
    _seed()
    login_via_code(ADMIN_EMAIL)
    page.set_viewport_size(VIEWPORT)
    return page


def describe_feature_switch_screenshots():
    def it_captures_the_features_tab(admin_page, live_server):
        """Shot 1 — Site Settings → Features, with one feature set to Coming soon so its
        message input is visible in context."""
        for key in ("meetings", "spaces", "equipment", "directory", "teach", "wiki"):
            turn_on(key)
        coming_soon("voting", "Launching Sept 30th!")
        hide("guilds")

        admin_page.goto(
            f"{live_server.url}{reverse('hub_admin_site_settings')}?tab=features",
            wait_until="networkidle",
            timeout=20000,
        )
        admin_page.wait_for_timeout(400)
        saved = _save(admin_page, "01-features-tab", full_page=True)
        print(f"\nSaved {saved}")

    def it_captures_a_coming_soon_entry_with_its_bubble_open(admin_page, live_server):
        """Shot 2 — the sidebar Coming soon entry, bubble revealed.

        Revealed by FOCUS, not hover: .pl-help opens on :hover AND :focus-within, and focus is
        the half of criterion 3 that a mouse screenshot cannot evidence. If this shot shows the
        bubble, the keyboard path works.
        """
        for key in ("meetings", "spaces", "equipment", "directory", "teach", "wiki"):
            turn_on(key)
        coming_soon("voting", "Launching Sept 30th!")

        admin_page.goto(f"{live_server.url}{reverse('hub_home')}", wait_until="networkidle", timeout=20000)
        admin_page.focus(".hub-sidebar__link--soon")
        admin_page.wait_for_timeout(400)  # let the bubble's 0.15s transition finish
        saved = _save(admin_page, "02-coming-soon-focus", clip={"x": 0, "y": 0, "width": 460, "height": 900})
        print(f"\nSaved {saved}")

    def it_captures_the_same_entry_under_the_mouse(admin_page, live_server):
        """Shot 2b — the same entry on hover, which is what most people will actually do."""
        for key in ("meetings", "spaces", "equipment", "directory", "teach", "wiki"):
            turn_on(key)
        coming_soon("voting", "Launching Sept 30th!")

        admin_page.goto(f"{live_server.url}{reverse('hub_home')}", wait_until="networkidle", timeout=20000)
        admin_page.hover(".hub-sidebar__link--soon")
        admin_page.wait_for_timeout(400)
        saved = _save(admin_page, "03-coming-soon-hover", clip={"x": 0, "y": 0, "width": 460, "height": 900})
        print(f"\nSaved {saved}")

    def it_captures_the_cms_tab(admin_page, live_server):
        """Shot 1b — Site Settings > CMS, the tab amendment 5 tidies.

        ``legacy_cms_last_synced_at`` and ``legacy_cms_last_sync_duration`` are set here on
        purpose: both lines are inside ``{% if %}``s, so an unseeded database renders a SHORTER
        tab than any real one and the spacing under review would not appear in the shot at all.
        """
        from django.utils import timezone

        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.legacy_cms_sync_enabled = True
        config.legacy_cms_last_synced_at = timezone.now()
        config.legacy_cms_last_sync_duration = 30
        config.save()

        admin_page.goto(
            f"{live_server.url}{reverse('hub_admin_site_settings')}?tab=legacy-cms",
            wait_until="networkidle",
            timeout=20000,
        )
        admin_page.wait_for_timeout(400)
        saved = _save(admin_page, "06-cms-tab", full_page=True)
        print(f"\nSaved {saved}")

    def it_captures_the_class_catalog_switch(admin_page, live_server):
        """Shot 8 — Class Catalog, the switch amendment 5 adds, in its Coming soon state.

        Focused rather than hovered, for the same reason as shot 2: focus is the half of the
        behaviour a mouse screenshot cannot evidence.
        """
        for key in ("meetings", "spaces", "equipment", "directory", "teach", "wiki", "voting"):
            turn_on(key)
        coming_soon("catalog", "The new catalog opens in October")

        admin_page.goto(f"{live_server.url}{reverse('hub_home')}", wait_until="networkidle", timeout=20000)
        admin_page.focus(".hub-sidebar__link--soon")
        admin_page.wait_for_timeout(400)
        saved = _save(admin_page, "08-class-catalog-soon", clip=SIDEBAR_CLIP)
        print(f"\nSaved {saved}")

    @pytest.mark.parametrize("tab", ["brand", "general", "calendar", "automations", "discord"])
    def it_captures_the_other_tabs_the_layout_fix_touches(admin_page, live_server, tab):
        """Shot 7 — the remaining panels amendment 5 moved off the inline display:flex.

        Every one of them had the same dead `gap`, so every one of them changes. Brand is the
        exception and is here as a control: it was already correct, on a `.pl-brand-tab` rule
        byte identical to the new shared one, and it should look untouched now that it shares
        the rule instead of owning a twin. These exist so the change can be seen rather than
        asserted: a stacked panel is a thing you look at.
        """
        admin_page.goto(
            f"{live_server.url}{reverse('hub_admin_site_settings')}?tab={tab}",
            wait_until="networkidle",
            timeout=20000,
        )
        admin_page.wait_for_timeout(400)
        saved = _save(admin_page, f"07-tab-{tab}", full_page=True)
        print(f"\nSaved {saved}")

    @pytest.mark.parametrize("theme", ["dark", "light"])
    def it_captures_a_hidden_feature_next_to_the_entries_that_remain(admin_page, live_server, theme):
        """Shot 3 — Meetings hidden and Voting coming soon, so both off states read against the
        live entries around them. Shot in BOTH themes: the Coming soon colour is a sidebar-scale
        de-emphasis and has to be visibly quieter on the light rail as well as the dark one."""
        for key in ("spaces", "equipment", "directory", "teach", "wiki"):
            turn_on(key)
        hide("meetings")
        coming_soon("voting", "Launching Sept 30th!")
        coming_soon("guilds", "Guild pages are on their way")
        Guild.objects.get_or_create(name="Ceramics Guild", defaults={"slug": "ceramics-guild", "is_active": True})

        _set_theme(admin_page, live_server, theme)
        admin_page.goto(f"{live_server.url}{reverse('hub_home')}", wait_until="networkidle", timeout=20000)
        admin_page.wait_for_timeout(400)
        saved = _save(admin_page, f"04-hidden-and-soon-sidebar-{theme}", clip=SIDEBAR_CLIP)
        print(f"\nSaved {saved}")

    @pytest.mark.parametrize("theme", ["dark", "light"])
    def it_captures_the_sidebar_with_everything_on_for_comparison(admin_page, live_server, theme):
        """Shot 4 — the control. Every feature On, which must look exactly like today. Paired
        with shot 3 at the same size and theme so the two can be read side by side."""
        from core.features import FEATURES

        for feature in FEATURES:
            turn_on(feature.key)
        Guild.objects.get_or_create(name="Ceramics Guild", defaults={"slug": "ceramics-guild", "is_active": True})
        _set_theme(admin_page, live_server, theme)
        admin_page.goto(f"{live_server.url}{reverse('hub_home')}", wait_until="networkidle", timeout=20000)
        admin_page.wait_for_timeout(400)
        saved = _save(admin_page, f"05-all-on-control-{theme}", clip=SIDEBAR_CLIP)
        print(f"\nSaved {saved}")
