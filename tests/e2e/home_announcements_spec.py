"""The Spotlight messages ticker at the top of Home in a real browser (#889, a ticker since #935).

It crawls right to left at a steady pace set from the list's width, loops through a hidden copy without a jump, holds
still while hovered or focused so a link can be clicked, and does not move under prefers-reduced-motion. Close hides it
in this browser until the set changes: a new or edited message brings it back, and with storage blocked it simply
shows. Its speed is the one admins set with the Crawl speed slider on Admin Tools > Spotlight (#942), whose preview
crawls at the slider's value as it moves and which saves when let go. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.factories import AnnouncementFactory
from core.models import Announcement, SiteConfiguration, TourState
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

TICKER = "[data-home-announcements]"
TRACK = f"{TICKER} [data-ticker-track]"
LIST = f"{TICKER} [data-ticker-list]"
COPY = f"{TICKER} [data-ticker-copy]"
SLIDER = "[data-crawl-speed-input]"
WORDS = "[data-crawl-speed-value]"
PREVIEW = "[data-crawl-speed-preview]"
PREVIEW_TRACK = f"{PREVIEW} [data-ticker-track]"
PREVIEW_LIST = f"{PREVIEW} [data-ticker-list]"
TOAST = ".plt-toast .plt-msg"


def _sign_in(login_via_code, email: str) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.get(user=user)
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["welcome_dismissed_at"])
    TourState.objects.mark_completed(user, "member-welcome")


def _announcements(count: int) -> list[Announcement]:
    return [
        AnnouncementFactory(body=f"<p>Zorblax notice <strong>{n + 1}</strong></p>", position=n) for n in range(count)
    ]


def _open_home(page, live_server, login_via_code, email: str, *, count: int = 3, width: int = 1440) -> None:
    _announcements(count)
    _sign_in(login_via_code, email)
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(f"{live_server.url}{reverse('hub_home')}")
    page.mouse.move(0, 0)
    expect(page.locator(TICKER)).to_be_visible()


def _set_speed(speed: int) -> None:
    config = SiteConfiguration.load()
    config.spotlight_crawl_speed = speed
    config.save(update_fields=["spotlight_crawl_speed"])


def _duration(page, track: str) -> float:
    return float(_style(page, track, "animationDuration").removesuffix("s"))


def _style(page, selector: str, prop: str) -> str:
    return page.locator(selector).evaluate(f"(el) => getComputedStyle(el).{prop}")


def _offset(page) -> float:
    """How far left the track has moved, in pixels."""
    return page.locator(TRACK).evaluate("(el) => -new DOMMatrixReadOnly(getComputedStyle(el).transform).m41")


def describe_the_crawl():
    def it_crawls_right_to_left(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "tick-crawl-e2e@example.com")

        assert _style(page, TRACK, "animationName") == "pl-ticker-crawl"
        assert _style(page, TRACK, "animationPlayState") == "running"
        before = _offset(page)
        page.wait_for_timeout(600)
        assert _offset(page) > before

    def it_sets_a_steady_pace_from_the_list_width(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "tick-pace-e2e@example.com")

        # Nothing saved: 25 px a second.
        width = page.locator(LIST).evaluate("(el) => el.offsetWidth")
        assert abs(_duration(page, TRACK) - width / 25) < 0.05

    def it_crawls_at_the_saved_speed(page, live_server, login_via_code):
        _set_speed(70)
        _open_home(page, live_server, login_via_code, "tick-speed-e2e@example.com")

        width = page.locator(LIST).evaluate("(el) => el.offsetWidth")
        assert abs(_duration(page, TRACK) - width / 70) < 0.05

    def it_loops_through_a_copy_of_the_same_width(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "tick-loop-e2e@example.com", count=1)

        widths = page.evaluate(
            """(sel) => [sel.list, sel.copy, sel.track, sel.viewport].map((s) => document.querySelector(s).offsetWidth)""",
            {
                "list": LIST,
                "copy": COPY,
                "track": TRACK,
                "viewport": f"{TICKER} [data-ticker-viewport]",
            },
        )
        list_width, copy_width, track_width, viewport_width = widths
        assert list_width == copy_width
        assert track_width == list_width * 2
        # One short message: the list is stretched to the line, so it leaves the line before it comes back.
        assert list_width >= viewport_width

    def it_keeps_the_copy_from_screen_readers_and_the_keyboard(page, live_server, login_via_code):
        AnnouncementFactory(body='<p>Zorblax <a href="/feedback/">feedback link</a></p>', position=9)
        _open_home(page, live_server, login_via_code, "tick-copy-e2e@example.com", count=1)

        expect(page.locator(COPY)).to_have_attribute("aria-hidden", "true")
        focused = page.locator(f"{COPY} a").evaluate("(el) => { el.focus(); return document.activeElement === el; }")
        assert focused is False
        expect(page.get_by_role("region", name="Spotlight messages").get_by_role("link")).to_have_count(1)


def describe_holding_still():
    def it_pauses_while_hovered_and_runs_again_after(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "tick-hover-e2e@example.com")

        page.locator(TICKER).hover()
        assert _style(page, TRACK, "animationPlayState") == "paused"
        held = _offset(page)
        page.wait_for_timeout(400)
        assert _offset(page) == held
        page.mouse.move(0, 900)
        assert _style(page, TRACK, "animationPlayState") == "running"

    def it_pauses_while_a_link_in_it_has_focus(page, live_server, login_via_code):
        AnnouncementFactory(body='<p>Zorblax <a href="/feedback/">feedback link</a></p>', position=0)
        _open_home(page, live_server, login_via_code, "tick-focus-e2e@example.com", count=0)

        page.locator(f"{LIST} a").focus()
        assert _style(page, TRACK, "animationPlayState") == "paused"

    def it_lets_a_link_be_clicked(page, live_server, login_via_code):
        AnnouncementFactory(body='<p>Zorblax <a href="/feedback/">feedback link</a></p>', position=0)
        _open_home(page, live_server, login_via_code, "tick-click-e2e@example.com", count=0)

        # As a member does: the pointer lands on the line, the crawl holds, and the link stays put to be clicked.
        page.locator(TICKER).hover()
        page.locator(f"{LIST} a").click()
        expect(page).to_have_url(f"{live_server.url}/feedback/")

    def it_does_not_move_under_reduced_motion(page, live_server, login_via_code):
        page.emulate_media(reduced_motion="reduce")
        _open_home(page, live_server, login_via_code, "tick-reduced-e2e@example.com", width=375)

        assert _style(page, TRACK, "animationName") == "none"
        expect(page.locator(COPY)).to_be_hidden()
        # Every message shown, wrapped inside the ticker rather than clipped off its right edge.
        ticker = page.locator(TICKER).bounding_box()
        for n in range(1, 4):
            box = page.locator(f"{LIST} li").nth(n - 1).bounding_box()
            assert box["x"] + box["width"] <= ticker["x"] + ticker["width"]
            expect(page.locator(LIST).get_by_text(f"Zorblax notice {n}")).to_be_in_viewport(ratio=1)
        before = _offset(page)
        page.wait_for_timeout(400)
        assert _offset(page) == before == 0


def describe_on_a_phone():
    def it_is_one_slim_line_with_no_sideways_scroll(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "tick-phone-e2e@example.com", width=375)

        assert page.locator(TICKER).bounding_box()["height"] < 40
        assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        expect(page.locator(f"{TICKER} [data-announcement-close]")).to_be_in_viewport()


def describe_closing_the_ticker():
    def it_stays_closed_until_a_new_message_arrives(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "tick-close-e2e@example.com")
        page.locator(f"{TICKER} [data-announcement-close]").click()
        expect(page.locator(TICKER)).to_have_count(0)
        page.reload()
        expect(page.locator(".pl-quicklinks")).to_be_visible()
        expect(page.locator(TICKER)).to_have_count(0)

        AnnouncementFactory(body="<p>Zorblax brand new</p>", position=0)
        page.reload()
        expect(page.locator(TICKER)).to_be_visible()
        expect(page.locator(LIST)).to_contain_text("Zorblax brand new")

    def it_comes_back_when_a_message_is_edited(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "tick-edit-e2e@example.com", count=1)
        page.locator(f"{TICKER} [data-announcement-close]").click()
        page.reload()
        expect(page.locator(TICKER)).to_have_count(0)

        announcement = Announcement.objects.get()
        announcement.body = "<p>Zorblax edited</p>"
        announcement.save()
        page.reload()
        expect(page.locator(LIST)).to_contain_text("Zorblax edited")

    def it_stays_closed_when_only_the_order_changes(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "tick-order-e2e@example.com", count=2)
        page.locator(f"{TICKER} [data-announcement-close]").click()
        first, second = Announcement.objects.in_order()
        Announcement.reorder([second.pk, first.pk])
        page.reload()
        expect(page.locator(".pl-quicklinks")).to_be_visible()
        expect(page.locator(TICKER)).to_have_count(0)

    def it_shows_and_closes_with_storage_blocked(page, live_server, login_via_code):
        page.add_init_script(
            "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"
        )
        _open_home(page, live_server, login_via_code, "tick-blocked-e2e@example.com")
        assert _style(page, TRACK, "animationName") == "pl-ticker-crawl"
        page.locator(f"{TICKER} [data-announcement-close]").click()
        expect(page.locator(TICKER)).to_have_count(0)
        page.reload()
        expect(page.locator(TICKER)).to_be_visible()


def _sign_in_admin(login_via_code, email: str) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.filter(user=user).first() or MemberFactory(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["fog_role", "welcome_dismissed_at"])
    member.sync_user_permissions()
    TourState.objects.mark_completed(user, "member-welcome")


def _open_spotlight(page, live_server, login_via_code, email: str) -> None:
    _sign_in_admin(login_via_code, email)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(f"{live_server.url}{reverse('hub_admin_spotlight')}")
    expect(page.locator(PREVIEW)).to_be_visible()


def _slide_to(page, value: int) -> None:
    """Drag the slider to ``value`` without letting go: an input event and no change."""
    page.locator(SLIDER).evaluate(
        "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }", value
    )


def _expect_preview_pace(page, speed: int) -> None:
    width = page.locator(PREVIEW_LIST).evaluate("(el) => el.offsetWidth")
    assert abs(_duration(page, PREVIEW_TRACK) - width / speed) < 0.05


def describe_the_crawl_speed_slider():
    def it_previews_while_it_moves_and_saves_when_let_go(page, live_server, login_via_code):
        AnnouncementFactory(body="<p>Zorblax preview notice</p>", position=0)
        _open_spotlight(page, live_server, login_via_code, "speed-save-e2e@example.com")

        expect(page.locator(PREVIEW_LIST)).to_contain_text("Zorblax preview notice")
        expect(page.locator(WORDS)).to_have_text("25 px a second")
        assert _style(page, PREVIEW_TRACK, "animationName") == "pl-ticker-crawl"
        _expect_preview_pace(page, 25)

        _slide_to(page, 60)
        expect(page.locator(WORDS)).to_have_text("60 px a second")
        expect(page.locator(SLIDER)).to_have_attribute("aria-valuetext", "60 px a second")
        _expect_preview_pace(page, 60)
        assert SiteConfiguration.load().spotlight_crawl_speed == 25  # moved, not yet let go

        page.locator(SLIDER).dispatch_event("change")
        expect(page.locator(TOAST)).to_have_text("Crawl speed saved. Members see it the next time Home loads.")
        assert SiteConfiguration.load().spotlight_crawl_speed == 60

    def it_saves_from_the_keyboard(page, live_server, login_via_code):
        _open_spotlight(page, live_server, login_via_code, "speed-keys-e2e@example.com")

        page.locator(SLIDER).focus()
        page.keyboard.press("ArrowRight")
        expect(page.locator(WORDS)).to_have_text("30 px a second")
        expect(page.locator(TOAST)).to_contain_text("Crawl speed saved.")
        assert SiteConfiguration.load().spotlight_crawl_speed == 30

    def it_crawls_a_sample_line_with_nothing_live(page, live_server, login_via_code):
        _open_spotlight(page, live_server, login_via_code, "speed-sample-e2e@example.com")

        expect(page.locator(PREVIEW_LIST)).to_have_text(
            "This is how fast the Spotlight messages crawl across the top of Home."
        )
        _expect_preview_pace(page, 25)

    def it_pauses_while_hovered(page, live_server, login_via_code):
        _open_spotlight(page, live_server, login_via_code, "speed-hover-e2e@example.com")

        page.locator(PREVIEW).hover()
        assert _style(page, PREVIEW_TRACK, "animationPlayState") == "paused"

    def it_goes_back_to_the_stored_speed_when_the_save_is_refused(page, live_server, login_via_code):
        _set_speed(40)
        _open_spotlight(page, live_server, login_via_code, "speed-refused-e2e@example.com")

        # A value the slider itself never offers, so the server refuses it.
        page.locator(SLIDER).evaluate(
            "(el) => { el.max = 100; el.value = 95; el.dispatchEvent(new Event('change', { bubbles: true })); }"
        )
        expect(page.locator(TOAST)).to_have_text("Pick a crawl speed from 10 to 80 px a second, in steps of 5.")
        expect(page.locator(SLIDER)).to_have_value("40")
        expect(page.locator(WORDS)).to_have_text("40 px a second")
        _expect_preview_pace(page, 40)
        assert SiteConfiguration.load().spotlight_crawl_speed == 40

    def it_says_so_and_goes_back_when_the_connection_drops(page, live_server, login_via_code):
        _open_spotlight(page, live_server, login_via_code, "speed-offline-e2e@example.com")

        page.context.set_offline(True)
        page.locator(SLIDER).evaluate(
            "(el) => { el.value = 50; el.dispatchEvent(new Event('change', { bubbles: true })); }"
        )
        expect(page.locator(TOAST)).to_have_text("The crawl speed did not save. Try again.")
        expect(page.locator(SLIDER)).to_have_value("25")
        page.context.set_offline(False)
        assert SiteConfiguration.load().spotlight_crawl_speed == 25

    def it_does_not_move_under_reduced_motion(page, live_server, login_via_code):
        page.emulate_media(reduced_motion="reduce")
        _open_spotlight(page, live_server, login_via_code, "speed-reduced-e2e@example.com")

        assert _style(page, PREVIEW_TRACK, "animationName") == "none"
        _slide_to(page, 80)
        assert _style(page, PREVIEW_TRACK, "animationName") == "none"
