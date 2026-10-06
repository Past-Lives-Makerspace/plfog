"""End-to-end: authenticated hub pages never scroll sideways on a phone.

A shared component defeated the app's ``html { overflow-x: hidden }`` guard: the
``.pl-help__bubble`` help tooltip is ``position: absolute; left: 0`` with a
``max-width: 320px`` and is only ``visibility: hidden`` (not ``display: none``)
when idle, so its box still counts toward document scroll width. Anchored to a
tiny "?" icon sitting well into a narrow column (e.g. the guild page's Studio
Hours card), the hidden bubble poked ~66px past a 393px viewport and the whole
page scrolled sideways — content shifted, the left edge clipped. The root-level
``overflow-x: hidden`` did not contain it (the root's overflow propagates to the
viewport and mobile Safari ignores it for panning), so the fix contains overflow
at the real content column (``.hub-content { overflow-x: clip }``) and, on phones,
pins the bubble to the viewport gutters so it stays fully readable.

This drives the real browser at a 393px viewport across the guild detail page
(where the bug reproduced) plus several other authenticated surfaces, asserting
each renders exactly as wide as the viewport. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from tests.membership.factories import (
    GuildFactory,
    GuildLinkFactory,
    GuildStaffMembershipFactory,
    LeadershipBadgeFactory,
    MeetingFactory,
    MemberFactory,
    MembershipPlanFactory,
)

PHONE = {"width": 393, "height": 852}
NO_H_SCROLL = "() => document.documentElement.scrollWidth === document.documentElement.clientWidth"
MEMBER_EMAIL = "mobile-overflow-member@example.com"

# Deterministic: Guild.save mints slug = slugify(name) in a clean test DB.
GUILD_NAME = "Fiber Arts Guild"
GUILD_SLUG = "fiber-arts-guild"

# The page under test → the URL name it reverses to. The guild is resolved by
# its deterministic slug; the rest take no args.
PAGES = [
    ("guild_detail", "hub_guild_detail", (GUILD_SLUG,)),
    ("member_directory", "hub_member_directory", ()),
    ("user_settings", "hub_user_settings", ()),
    ("meetings_home", "hub_meetings", ()),
    ("hub_home", "hub_home", ()),
]


def _banner() -> SimpleUploadedFile:
    """A real PNG so the guild hero renders its full-bleed banner (not the no-image state)."""
    buf = io.BytesIO()
    Image.new("RGB", (1600, 500), (40, 60, 90)).save(buf, format="PNG")
    return SimpleUploadedFile("banner.png", buf.getvalue(), content_type="image/png")


def _seed_world() -> None:
    """A plan (so login auto-provisions the member) and a fully-populated guild.

    The guild carries a banner, a lead, staff, and links so the exact markup that
    drove the overflow renders: the Studio Hours card and its ``.pl-help`` tooltip
    in the narrow right-hand aside.
    """
    MembershipPlanFactory()
    guild = GuildFactory(
        name=GUILD_NAME,
        banner_image=_banner(),
        about="We meet weekly to spin, weave, and dye. " * 8,
        show_members=True,
        contact_email="fiber@example.com",
    )
    guild.guild_lead = MemberFactory(full_legal_name="Wilhelmina Aldous-Featherington")
    guild.save(update_fields=["guild_lead"])
    # Leadership badges on the lead's directory card (#650): one at the 40 character cap.
    for label in ("Community Engagement Manager Extraordina", "Elevator Certified"):
        LeadershipBadgeFactory(label=label).give(guild.guild_lead)
    GuildStaffMembershipFactory(guild=guild, member=MemberFactory(full_legal_name="Bartholomew Cavendish"))
    GuildLinkFactory(guild=guild, label="Ravelry group", url="https://example.com/ravelry")
    MeetingFactory(guild=guild)


def describe_mobile_no_horizontal_overflow():
    @pytest.mark.parametrize("label,url_name,args", PAGES, ids=[p[0] for p in PAGES])
    def it_never_scrolls_sideways_at_phone_width(live_server, page, login_via_code, label, url_name, args):
        _seed_world()
        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)

        page.goto(f"{live_server.url}{reverse(url_name, args=args)}")
        assert page.evaluate(NO_H_SCROLL), f"{label} scrolls sideways at {PHONE['width']}px"

    def it_never_scrolls_sideways_on_the_notifications_tab(live_server, page, login_via_code):
        """The settings page in PAGES above lands on the default tab, where the whole
        notification pane is ``x-show``/``x-cloak`` hidden and contributes nothing to
        ``scrollWidth``. Ask for the tab explicitly, so the matrix is actually laid out,
        including a topic that carries padlocked rows.
        """
        _seed_world()
        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)

        page.goto(f"{live_server.url}{reverse('hub_user_settings')}?tab=notifications")
        page.locator("#notif-classes").wait_for(state="visible")
        assert page.evaluate(NO_H_SCROLL), f"notifications tab scrolls sideways at {PHONE['width']}px"

    def it_keeps_an_admins_notifications_tab_narrow_with_every_bulk_control_reachable(
        live_server, page, login_via_code
    ):
        """An admin's page adds the framed Admin / Permissions section and its bars (#524).
        At 390px the page must not scroll sideways, and every On / Off button must be
        reachable: the page bars wrap, and a grid's own buttons scroll into view inside
        that grid's scroll box."""
        from django.contrib.auth import get_user_model

        from membership.models import Member

        email = "mobile-overflow-admin@example.com"
        MembershipPlanFactory()
        page.set_viewport_size({"width": 390, "height": 844})
        login_via_code(email)
        Member.objects.filter(user=get_user_model().objects.get(username=email)).update(
            fog_role=Member.FogRole.ADMIN, discord_user_id="mobile-overflow-admin"
        )

        page.goto(f"{live_server.url}{reverse('hub_user_settings')}?tab=notifications")
        page.locator("#notif-admin-permissions").wait_for(state="visible")
        assert page.evaluate(NO_H_SCROLL), "an admin's notifications tab scrolls sideways at 390px"

        buttons = page.locator(".pl-notif-onoff__btn")
        assert buttons.count() > 0
        for index in range(buttons.count()):
            button = buttons.nth(index)
            button.scroll_into_view_if_needed()
            box = button.bounding_box()
            assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 390, button.get_attribute(
                "aria-label"
            )
        assert page.evaluate(NO_H_SCROLL), "scrolling a grid's buttons into view pushed the page sideways"
