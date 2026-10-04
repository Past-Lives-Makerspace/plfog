"""End-to-end: the hero Adjust sliders say which axis can move.

The guild banner, the Help Center banner and the class page's category hero are cover
fitted, so a photo overflows its frame on one axis only and the other slider writes an
object-position nothing can show. ``hero_placement.js`` ``measure()`` reads the slack the
same way ``card_focus.js`` does (issue #427): the dead slider is disabled and one line under
the sliders says why. The frames are fluid, so the verdict is worked out again when the
frame changes shape. Only a browser lays the frame out and decodes the photo, so this drives
the real component. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import io
from typing import Any, cast
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse
from PIL import Image
from playwright.sync_api import Locator, Page, Route, ViewportSize, expect

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering
from membership.models import Guild, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory, OrgInfoPageFactory

ADMIN_EMAIL = "hero-slack-admin@example.com"
ADJUST = 'button[title="Adjust Placement"]'
WHY = ".pl-hero-adjust__why"
HERO_IMG = "img[data-hero-img]"
DESKTOP: ViewportSize = {"width": 1280, "height": 900}
PHONE: ViewportSize = {"width": 390, "height": 844}

X_DEAD = "This photo already fits side to side, so only up and down moves it."
Y_DEAD = "This photo already fits top to bottom, so only left and right moves it."
BOTH_DEAD = "This photo already fits the banner exactly, so there is nothing to move."


def _png(name: str, width: int, height: int) -> ContentFile:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(buffer, "PNG")
    return ContentFile(buffer.getvalue(), name=name)


def _sign_in_as_admin(login_via_code) -> None:
    MembershipPlanFactory()  # so the user signal can provision the admin's member
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    Member.objects.filter(user=user).update(fog_role=Member.FogRole.ADMIN)


def _guild(width: int, height: int) -> Guild:
    return cast(Guild, GuildFactory(name="Slack Guild", banner_image=_png("slack-banner.png", width, height)))


def _guild_url(live_server, guild: Guild) -> str:
    return f"{live_server.url}{reverse('hub_guild_detail', kwargs={'slug': guild.slug})}"


def _frame(page: Page) -> dict[str, Any]:
    """The hero img's box (the frame the photo is cover fitted into) and the photo's size."""
    return page.locator(HERO_IMG).evaluate(
        "img => ({w: img.clientWidth, h: img.clientHeight, nw: img.naturalWidth, nh: img.naturalHeight})"
    )


def _wait_for_pixels(page: Page) -> None:
    page.wait_for_function(
        f"() => {{ const img = document.querySelector('{HERO_IMG}'); return !!(img && img.complete && img.naturalWidth); }}"
    )


def _sliders(page: Page, vertical: str, horizontal: str) -> tuple[Locator, Locator]:
    """The bar's Vertical and Horizontal range inputs, found by the label the page gives each."""
    bar = page.locator(".pl-hero-adjust")
    down = bar.locator("div", has=page.get_by_text(vertical, exact=True)).locator('input[type="range"]')
    across = bar.locator("div", has=page.get_by_text(horizontal, exact=True)).locator('input[type="range"]')
    return down, across


def _wide_frame(frame: dict[str, Any]) -> bool:
    """True when the frame is a wider shape than the photo: the photo is width fitted."""
    return frame["w"] / frame["h"] > frame["nw"] / frame["nh"]


def describe_hero_placement_slack():
    def it_disables_the_dead_slider_on_the_guild_banner_and_follows_the_frame_when_it_changes_shape(
        live_server, page: Page, login_via_code, serve_media
    ):
        # A 2:1 banner. The desktop frame is a wider shape, so the photo is width fitted and
        # only up and down has room; on a phone the frame is a narrower shape and it flips.
        guild = _guild(1000, 500)
        _sign_in_as_admin(login_via_code)
        page.set_viewport_size(DESKTOP)
        page.goto(_guild_url(live_server, guild))
        _wait_for_pixels(page)
        assert _wide_frame(_frame(page)), _frame(page)
        page.locator(ADJUST).click()

        down, across = _sliders(page, "Vertical", "Horizontal")
        expect(across).to_be_disabled()
        expect(down).to_be_enabled()
        expect(page.locator(WHY)).to_have_text(X_DEAD)
        # A disabled range keeps showing the saved position; measuring never moves it.
        expect(across).to_have_value("50")

        page.set_viewport_size(PHONE)
        page.wait_for_function(
            f"() => {{ const img = document.querySelector('{HERO_IMG}');"
            " return img.clientWidth / img.clientHeight < img.naturalWidth / img.naturalHeight; }"
        )
        expect(down).to_be_disabled()
        expect(across).to_be_enabled()
        expect(page.locator(WHY)).to_have_text(Y_DEAD)

        across.fill("20")
        expect(page.locator(HERO_IMG)).to_have_css("object-position", "20% 50%")

    def it_says_there_is_nothing_to_move_when_the_banner_is_the_frames_exact_shape(
        live_server, page: Page, login_via_code, serve_media
    ):
        # Measure the desktop frame with any banner, then give the guild a banner of exactly
        # that size: no slack either way, both sliders off, one line.
        guild = _guild(1000, 500)
        _sign_in_as_admin(login_via_code)
        page.set_viewport_size(DESKTOP)
        page.goto(_guild_url(live_server, guild))
        _wait_for_pixels(page)
        frame = _frame(page)
        guild.banner_image = _png("exact-banner.png", frame["w"], frame["h"])
        guild.save()

        page.goto(_guild_url(live_server, guild))
        _wait_for_pixels(page)
        assert (_frame(page)["nw"], _frame(page)["nh"]) == (frame["w"], frame["h"])
        page.locator(ADJUST).click()

        down, across = _sliders(page, "Vertical", "Horizontal")
        expect(down).to_be_disabled()
        expect(across).to_be_disabled()
        expect(page.locator(WHY)).to_have_text(BOTH_DEAD)

    def it_leaves_both_sliders_on_until_the_banner_has_pixels(live_server, page: Page, login_via_code):
        # Hold the banner's response: until the photo decodes there is nothing to measure, so
        # nothing is disabled on a guess. Releasing it settles the verdict on the img's load.
        guild = _guild(1000, 500)
        _sign_in_as_admin(login_via_code)
        held: list[Route] = []

        def _hold(route: Route) -> None:
            held.append(route)

        # On the context, not the page: the hub's service worker fetches the photo, and a
        # page route never sees a service worker's requests.
        page.context.route(f"{live_server.url}/media/**", _hold)
        page.set_viewport_size(DESKTOP)
        page.goto(_guild_url(live_server, guild), wait_until="domcontentloaded")
        page.locator(ADJUST).click()

        down, across = _sliders(page, "Vertical", "Horizontal")
        expect(page.locator(".pl-hero-adjust")).to_be_visible()
        expect(down).to_be_enabled()
        expect(across).to_be_enabled()
        expect(page.locator(WHY)).to_be_hidden()

        assert held, "expected the banner request to be held"
        held[0].fulfill(path=guild.banner_image.path)
        expect(across).to_be_disabled()
        expect(page.locator(WHY)).to_have_text(X_DEAD)

    def it_disables_the_dead_slider_on_the_help_center_banner(live_server, page: Page, login_via_code, serve_media):
        # 4:1 is wider than the Help hero at a desktop width: height fitted, only left and right.
        OrgInfoPageFactory(banner_image=_png("help-banner.png", 1600, 400))
        _sign_in_as_admin(login_via_code)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hub_help')}")
        _wait_for_pixels(page)
        assert not _wide_frame(_frame(page)), _frame(page)
        page.locator(ADJUST).click()

        down, across = _sliders(page, "Vertical", "Horizontal")
        expect(down).to_be_disabled()
        expect(across).to_be_enabled()
        expect(page.locator(WHY)).to_have_text(Y_DEAD)

    def it_disables_the_dead_slider_on_the_class_pages_category_hero(
        live_server, page: Page, login_via_code, serve_media, settings
    ):
        # A class with no photo of its own shows its category's hero, cover fitted, with the
        # Adjust tooling under it. 4:1 is wider than that frame too.
        category = CategoryFactory(hero_image=_png("category-hero.png", 1600, 400))
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, category=category, image="")
        _sign_in_as_admin(login_via_code)
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}")
        _wait_for_pixels(page)
        assert not _wide_frame(_frame(page)), _frame(page)
        page.locator(ADJUST).click()

        down, across = _sliders(page, "V", "H")
        expect(down).to_be_disabled()
        expect(across).to_be_enabled()
        expect(page.locator(WHY)).to_have_text(Y_DEAD)
