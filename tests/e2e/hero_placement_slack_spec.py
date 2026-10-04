"""End-to-end: the hero Adjust sliders say where each one moves the photo.

The guild banner, the Help Center banner and the class page's category hero are cover
fitted, so in any one frame a photo overflows on one axis only. These frames are fluid and
one saved position serves every screen, so which axis moves depends on the screen: a tall
phone frame moves a 2:1 photo sideways, a wide desktop frame moves it up and down. Each
template states the frame shapes its CSS can produce (``data-frame-min`` and
``data-frame-max`` on the img) and ``hero_placement.js`` ``measure()`` compares the photo's
shape with that range: a slider is disabled only when it moves nothing on every screen, and
otherwise the line under the sliders says where each one moves. The editor's own screen
plays no part. Only a browser decodes the photo and runs the component, so this drives the
real page. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import io
from typing import cast
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
WHY = "#hero-adjust-why"
HERO_IMG = "img[data-hero-img]"
DOWN = "#hero-adjust-y"
ACROSS = "#hero-adjust-x"
DESKTOP: ViewportSize = {"width": 1280, "height": 900}
PHONE: ViewportSize = {"width": 390, "height": 844}

X_DEAD = "This photo already fits side to side on every screen, so only up and down moves it."
Y_DEAD = "This photo already fits top to bottom on every screen, so only left and right moves it."
WHERE = "Left and right moves the photo on phones. Up and down moves it on wider screens."


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


def _guild(width: int, height: int, **kwargs: object) -> Guild:
    return cast(Guild, GuildFactory(name="Slack Guild", banner_image=_png("slack-banner.png", width, height), **kwargs))


def _guild_url(live_server, guild: Guild) -> str:
    return f"{live_server.url}{reverse('hub_guild_detail', kwargs={'slug': guild.slug})}"


def _wait_for_pixels(page: Page) -> None:
    page.wait_for_function(
        f"() => {{ const img = document.querySelector('{HERO_IMG}'); return !!(img && img.complete && img.naturalWidth); }}"
    )


def _open_adjust(page: Page) -> tuple[Locator, Locator]:
    """Open the Adjust bar and return its up and down slider and its left and right slider."""
    page.locator(ADJUST).click()
    expect(page.locator(".pl-hero-adjust")).to_be_visible()
    return page.locator(DOWN), page.locator(ACROSS)


def describe_hero_placement_slack():
    def it_keeps_both_sliders_on_for_a_photo_that_moves_each_way_on_some_screen(
        live_server, page: Page, login_via_code, serve_media
    ):
        # 2:1 sits inside the guild frame's range (0.5 to 3.4): a phone frame moves it sideways,
        # a desktop frame up and down. Both stay on and the line says where each moves, the
        # same on a desktop and a phone, because the editor's own frame plays no part.
        guild = _guild(1000, 500)
        _sign_in_as_admin(login_via_code)
        for viewport in (DESKTOP, PHONE):
            page.set_viewport_size(viewport)
            page.goto(_guild_url(live_server, guild))
            _wait_for_pixels(page)
            down, across = _open_adjust(page)
            expect(down).to_be_enabled()
            expect(across).to_be_enabled()
            expect(page.locator(WHY)).to_have_text(WHERE)
            # The line is tied to both sliders for keyboard and screen reader users.
            expect(down).to_have_attribute("aria-describedby", "hero-adjust-why")
            expect(across).to_have_attribute("aria-describedby", "hero-adjust-why")

        across.fill("20")
        expect(page.locator(HERO_IMG)).to_have_css("object-position", "20% 50%")

    def it_turns_off_up_and_down_for_a_photo_wider_than_any_frame(live_server, page: Page, login_via_code, serve_media):
        # 4:1 is wider than the widest guild frame (3.26, so 3.4 with margin): height fitted on
        # every screen, so up and down never moves it.
        guild = _guild(2000, 500)
        _sign_in_as_admin(login_via_code)
        page.set_viewport_size(DESKTOP)
        page.goto(_guild_url(live_server, guild))
        _wait_for_pixels(page)
        down, across = _open_adjust(page)

        expect(down).to_be_disabled()
        expect(across).to_be_enabled()
        expect(page.locator(WHY)).to_have_text(Y_DEAD)

    def it_turns_off_left_and_right_for_a_tall_photo_and_keeps_and_saves_its_saved_position(
        live_server, page: Page, login_via_code, serve_media
    ):
        # 2:5 is narrower than the narrowest guild frame: width fitted everywhere, so left and
        # right is off. The position saved before (20% across) still shows on the disabled
        # slider and rides the save untouched.
        guild = _guild(400, 1000, hero_crop_x=20, hero_crop_y=50)
        _sign_in_as_admin(login_via_code)
        page.set_viewport_size(DESKTOP)
        page.goto(_guild_url(live_server, guild))
        _wait_for_pixels(page)
        down, across = _open_adjust(page)

        expect(across).to_be_disabled()
        expect(down).to_be_enabled()
        expect(page.locator(WHY)).to_have_text(X_DEAD)
        expect(across).to_have_value("20")

        down.fill("70")
        with page.expect_request(lambda request: request.url.endswith(reverse("hub_hero_adjust"))) as posted:
            page.get_by_role("button", name="Save", exact=True).click()
        body = posted.value.post_data_json
        assert body is not None
        crop = body["crop"]
        assert (crop["x"], crop["y"]) == (20, 70)
        expect(page.locator(".pl-hero-adjust")).to_be_hidden()
        guild.refresh_from_db()
        assert guild.hero_object_position == "20% 70%"

    def it_leaves_both_sliders_on_until_the_banner_has_pixels(live_server, page: Page, login_via_code):
        # Hold the banner's response: until the photo decodes its shape is unknown, so nothing
        # is disabled on a guess and no line shows. Releasing it settles the verdict on load.
        guild = _guild(2000, 500)
        _sign_in_as_admin(login_via_code)
        held: list[Route] = []

        def _hold(route: Route) -> None:
            held.append(route)

        # On the context, not the page: the hub's service worker fetches the photo, and a
        # page route never sees a service worker's requests.
        page.context.route(f"{live_server.url}/media/**", _hold)
        page.set_viewport_size(DESKTOP)
        page.goto(_guild_url(live_server, guild), wait_until="domcontentloaded")
        down, across = _open_adjust(page)

        expect(down).to_be_enabled()
        expect(across).to_be_enabled()
        expect(page.locator(WHY)).to_be_hidden()

        assert held, "expected the banner request to be held"
        held[0].fulfill(path=guild.banner_image.path)
        expect(down).to_be_disabled()
        expect(page.locator(WHY)).to_have_text(Y_DEAD)

    def it_turns_off_up_and_down_on_the_help_center_banner_for_a_photo_wider_than_any_frame(
        live_server, page: Page, login_via_code, serve_media
    ):
        OrgInfoPageFactory(banner_image=_png("help-banner.png", 2000, 500))
        _sign_in_as_admin(login_via_code)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hub_help')}")
        _wait_for_pixels(page)
        down, across = _open_adjust(page)

        expect(down).to_be_disabled()
        expect(across).to_be_enabled()
        expect(page.locator(WHY)).to_have_text(Y_DEAD)

    def it_uses_the_class_pages_own_narrower_range_for_the_category_hero(
        live_server, page: Page, login_via_code, serve_media, settings
    ):
        # The class hero tops out at 2.38 (2.5 with margin), so a 2.67 photo that would keep
        # both sliders on a guild banner has nothing to move up and down here.
        category = CategoryFactory(hero_image=_png("category-hero.png", 1600, 600))
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, category=category, image="")
        _sign_in_as_admin(login_via_code)
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}")
        _wait_for_pixels(page)
        down, across = _open_adjust(page)

        expect(down).to_be_disabled()
        expect(across).to_be_enabled()
        expect(page.locator(WHY)).to_have_text(Y_DEAD)
