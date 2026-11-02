"""End-to-end: Admin Tools > Guilds switches save themselves, and a Founding Guild wears its badge (#1086).

Pytest's Django client never runs the autosave script, so this flips the switches in a real
browser: Founding Guild on one row saves at once (the save pill reads Saved), survives a reload,
leaves the other row alone, and puts the gold badge on that guild's page; Visible to members off
hides the guild. On a phone the table folds into one card per guild with no sideways scroll.

Waits are on what the page shows, never a fixed sleep. Set ``CAPTURE_FOUNDING_GUILD_SCREENSHOTS=1``
to write the PR screenshots and the What's New shots to ``mockups/screenshots/1086/``. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Guild, Member
from tests.e2e.guidance import shoot
from tests.features import turn_on
from tests.membership.factories import GuildFactory, MembershipPlanFactory

ADMIN_EMAIL = "founding-guild-admin@example.com"
SHOTS = Path(__file__).resolve().parents[2] / "mockups" / "screenshots" / "1086"
CAPTURE = bool(os.environ.get("CAPTURE_FOUNDING_GUILD_SCREENSHOTS"))
ALPINE_READY = "() => !!(document.querySelector('[data-guild-autosave]') || {})._x_dataStack"
SAVED_PAST = (
    "(n) => { const pill = document.querySelector('[data-save-pill]');"
    " return !!pill && Number(pill.dataset.saves) >= n && pill.textContent.trim() === 'Saved'; }"
)
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"
THEME_JS = """(t) => {
    const root = document.documentElement;
    if (window.__plSetTheme) { window.__plSetTheme(t === 'light'); return; }
    if (t === 'light') { root.setAttribute('data-theme', 'light'); root.classList.remove('dark'); }
    else { root.removeAttribute('data-theme'); root.classList.add('dark'); }
}"""


def _guilds() -> tuple[Guild, Guild]:
    MembershipPlanFactory()
    turn_on("guilds")
    glass = GuildFactory(
        name="Glassblowing Guild",
        about="Hot shop nights, a shared glory hole and lots of first gathers.",
    )
    weaving = GuildFactory(name="Weaving Guild", about="Floor looms, a yarn library and Tuesday warp nights.")
    GuildFactory(name="Leather Guild", is_active=False)
    return glass, weaving


def _sign_in_as_admin(login_via_code) -> None:
    login_via_code(ADMIN_EMAIL)
    member = get_user_model().objects.get(username=ADMIN_EMAIL).member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()


def _open_guilds_page(page, live_server) -> None:
    page.goto(f"{live_server.url}{reverse('hub_admin_guilds')}")
    page.wait_for_function(ALPINE_READY)


def _flip(page, guild: Guild, field: str) -> None:
    box = page.locator(f"#id_guild{guild.pk}_{field}")
    page.locator("label.pl-toggle", has=box).click()


def _theme(page, theme: str) -> None:
    page.evaluate(THEME_JS, theme)
    page.wait_for_timeout(300)  # the theme's colour transitions settle before a picture


def _capture(page, name: str, *, locator=None) -> None:
    if not CAPTURE:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    (locator or page).screenshot(path=str(SHOTS / name))


def describe_admin_guilds_page():
    def it_saves_founding_guild_at_once_and_the_guild_page_shows_the_badge(live_server, page, login_via_code):
        glass, weaving = _guilds()
        _sign_in_as_admin(login_via_code)
        _open_guilds_page(page, live_server)

        box = page.locator(f"#id_guild{glass.pk}_is_founding")
        expect(box).not_to_be_checked()
        _flip(page, glass, "is_founding")
        page.wait_for_function(SAVED_PAST, arg=1)
        glass.refresh_from_db()
        weaving.refresh_from_db()
        assert (glass.is_active, glass.is_founding) == (True, True)
        assert weaving.is_founding is False

        page.reload()
        page.wait_for_function(ALPINE_READY)
        expect(page.locator(f"#id_guild{glass.pk}_is_founding")).to_be_checked()
        expect(page.locator(f"[data-guild-row='{glass.pk}'] [data-founding-badge]")).to_be_visible()

        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[glass.slug])}")
        expect(page.locator("[data-founding-badge]")).to_have_text("Founding Guild")
        expect(page.locator("[data-founding-note]")).to_be_visible()

    def it_hides_a_guild_from_its_row(live_server, page, login_via_code):
        glass, _weaving = _guilds()
        _sign_in_as_admin(login_via_code)
        _open_guilds_page(page, live_server)

        _flip(page, glass, "is_active")
        page.wait_for_function(SAVED_PAST, arg=1)
        glass.refresh_from_db()
        assert (glass.is_active, glass.is_founding) == (False, False)

    def it_folds_into_cards_on_a_phone(live_server, page, login_via_code):
        _guilds()
        _sign_in_as_admin(login_via_code)
        page.set_viewport_size({"width": 390, "height": 844})
        _open_guilds_page(page, live_server)
        expect(page.locator("[data-guild-row]").first).to_be_visible()
        assert page.evaluate(NO_SIDEWAYS_SCROLL)


def describe_the_pictures():
    def it_holds_together_on_every_surface_in_both_themes(live_server, page, login_via_code, tmp_path, settings):
        """Walks every surface the PR shows; the What's New shots land in tmp_path unless capturing."""
        out = SHOTS if CAPTURE else tmp_path
        glass, _weaving = _guilds()
        glass.is_founding = True
        glass.is_featured = True
        glass.save(update_fields=["is_founding", "is_featured"])
        Guild.objects.filter(name="Leather Guild").update(is_founding=True)
        _sign_in_as_admin(login_via_code)

        # What's New: Admin Tools' card, the Guilds page's column, the guild page badge.
        page.goto(f"{live_server.url}{reverse('hub_admin_tools')}")
        page.locator("[data-tool-guilds]").scroll_into_view_if_needed()
        no_thanks = page.get_by_role("button", name="No thanks")
        if no_thanks.is_visible():  # the first visit's tour offer would sit over the cards
            no_thanks.click()
        shoot(page, out / "step-1.png", target="admin.guilds-card")
        _open_guilds_page(page, live_server)
        shoot(page, out / "step-2.png", target="admin.guilds-founding")
        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[glass.slug])}")
        shoot(page, out / "step-3.png", target="guild.founding-badge")

        # The PR pictures.
        page.set_viewport_size({"width": 1280, "height": 800})
        for theme in ("dark", "light"):
            _open_guilds_page(page, live_server)
            _theme(page, theme)
            _capture(page, f"admin-guilds-desktop-{theme}.png")
        page.set_viewport_size({"width": 390, "height": 844})
        _open_guilds_page(page, live_server)
        _theme(page, "dark")
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        if CAPTURE:
            page.screenshot(path=str(SHOTS / "admin-guilds-phone-dark.png"), full_page=True)

        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[glass.pk])}?tab=basic")
        page.wait_for_function(ALPINE_READY)
        card = page.locator("form[action$='/visibility/save/'] .hub-card")
        card.scroll_into_view_if_needed()
        for theme in ("dark", "light"):
            _theme(page, theme)
            _capture(page, f"basic-tab-{theme}.png", locator=card)

        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[glass.slug])}")
        for theme in ("dark", "light"):
            _theme(page, theme)
            _capture(page, f"guild-page-{theme}.png", locator=page.locator(".pl-guild-hero"))
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[glass.slug])}")
        _theme(page, "dark")
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        _capture(page, "guild-page-phone-dark.png", locator=page.locator(".pl-guild-hero"))

        page.set_viewport_size({"width": 1280, "height": 800})
        nav = page.get_by_role("navigation", name="Hub navigation")
        for theme in ("dark", "light"):
            page.goto(f"{live_server.url}{reverse('hub_home')}")
            _theme(page, theme)
            nav.get_by_role("link", name="Leather Guild").scroll_into_view_if_needed()
            _capture(page, f"sidebar-{theme}.png", locator=page.locator(".hub-sidebar"))
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{live_server.url}{reverse('hub_home')}")
        _theme(page, "dark")
        page.get_by_role("button", name="Toggle sidebar").click()
        tag = nav.get_by_role("link", name="Leather Guild").locator("[data-sidebar-founding]")
        tag.scroll_into_view_if_needed()
        expect(tag).to_be_visible()
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        _capture(page, "sidebar-phone-dark.png")

        # The directory is the guilds host's page (guilds.pastlives.space), so it is shot there.
        settings.GUILDS_HOSTS = [urlparse(live_server.url).hostname]
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{live_server.url}{reverse('hub_guild_directory')}")
        cards = page.locator(".pl-guild-dir__grid")
        expect(cards.locator("[data-founding-badge]")).to_have_count(1)
        for theme in ("dark", "light"):
            _theme(page, theme)
            _capture(page, f"directory-{theme}.png", locator=cards)
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{live_server.url}{reverse('hub_guild_directory')}")
        _theme(page, "dark")
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        _capture(page, "directory-phone-dark.png", locator=cards.locator(".cls-card").first)
