"""Admin Tools > Spotlight's Announcements section in a real browser (#889).

An admin adds an announcement in the short editor (bold, italic, links) and watches its count, edits it,
switches one off and on, reorders by the up and down buttons and by dragging, and deletes one through the
confirm modal; the switch and the order save at once, and every save shows a toast. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.factories import AnnouncementFactory
from core.models import Announcement, TourState
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

SECTION = "[data-announcements-admin]"
ROWS = f"{SECTION} [data-announcement-row]"
TOAST = ".plt-toast .plt-msg"


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


def _spotlight(page, live_server) -> None:
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(f"{live_server.url}{reverse('hub_admin_spotlight')}")


def _row_texts(page) -> list[str]:
    return [text.strip() for text in page.locator(f"{ROWS} [data-announcement-text]").all_inner_texts()]


def _type_into_editor(page, text: str) -> None:
    editor = page.locator(".pl-rte .ql-editor")
    editor.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.press("Backspace")
    page.keyboard.type(text)


def describe_adding_and_editing():
    def it_adds_one_in_the_short_editor_with_a_live_count(page, live_server, login_via_code):
        _sign_in_admin(login_via_code, "ann-admin-add-e2e@example.com")
        _spotlight(page, live_server)
        expect(page.locator(f"{SECTION} [data-announcements-empty]")).to_be_visible()

        page.locator("[data-announcement-add]").click()
        page.wait_for_url("**/manage/spotlight/announcements/new/")
        expect(page.locator(".ql-toolbar .ql-bold")).to_be_visible()
        expect(page.locator(".ql-toolbar .ql-italic")).to_be_visible()
        expect(page.locator(".ql-toolbar .ql-link")).to_be_visible()
        expect(page.locator(".ql-toolbar .ql-header")).to_have_count(0)
        expect(page.locator(".ql-toolbar .ql-list")).to_have_count(0)

        counter = page.locator("[data-announcement-count]")
        _type_into_editor(page, "z" * 161)
        expect(counter).to_have_text("161 / 160")
        expect(counter).to_have_class(re.compile(r"\bis-over\b"))

        _type_into_editor(page, "Open house Saturday, ")
        page.keyboard.press("ControlOrMeta+B")
        page.keyboard.type("bring a friend")
        expect(counter).to_have_text("35 / 160")
        expect(counter).not_to_have_class(re.compile(r"\bis-over\b"))
        page.get_by_role("button", name="Save").click()

        page.wait_for_url(re.compile(r"/manage/spotlight/(#announcements)?$"))
        expect(page.locator(TOAST)).to_have_text("Announcement added. It shows on Home while it is live.")
        expect(page.locator(ROWS)).to_have_count(1)
        expect(page.locator(f"{ROWS} [data-announcement-text] strong")).to_have_text("bring a friend")
        expect(page.locator(f"{ROWS} [data-announcement-status]")).to_have_text("Live")
        assert Announcement.objects.get().body == "<p>Open house Saturday, <strong>bring a friend</strong></p>"

    def it_refuses_too_long_a_save_and_keeps_the_words(page, live_server, login_via_code):
        _sign_in_admin(login_via_code, "ann-admin-long-e2e@example.com")
        page.goto(f"{live_server.url}{reverse('hub_admin_announcement_new')}")
        _type_into_editor(page, "y" * 170)
        page.get_by_role("button", name="Save").click()

        expect(page.locator(".pl-field-error")).to_have_text("Keep it to 160 characters (this one is 170).")
        expect(page.locator(".pl-rte .ql-editor")).to_have_text("y" * 170)
        assert not Announcement.objects.exists()

    def it_edits_one_and_its_dates(page, live_server, login_via_code):
        AnnouncementFactory(body="<p>Zorblax first draft</p>")
        _sign_in_admin(login_via_code, "ann-admin-edit-e2e@example.com")
        _spotlight(page, live_server)

        page.locator(f"{ROWS} [data-announcement-edit]").click()
        page.wait_for_url("**/manage/spotlight/announcements/*/")
        expect(page.locator("[data-announcement-count]")).to_have_text("19 / 160")
        _type_into_editor(page, "Zorblax second draft")
        page.fill("#id_show_from_date", "2099-01-02")
        page.select_option("#id_show_from_time", "09:00")
        page.get_by_role("button", name="Save").click()

        page.wait_for_url(re.compile(r"/manage/spotlight/(#announcements)?$"))
        expect(page.locator(TOAST)).to_have_text("Announcement saved.")
        assert _row_texts(page) == ["Zorblax second draft"]
        expect(page.locator(f"{ROWS} [data-announcement-status]")).to_have_text("Scheduled")
        expect(page.locator(f"{ROWS} [data-announcement-dates]")).to_have_text("From Jan 2, 9:00 AM")

    def it_refuses_an_end_before_the_start(page, live_server, login_via_code):
        _sign_in_admin(login_via_code, "ann-admin-dates-e2e@example.com")
        page.goto(f"{live_server.url}{reverse('hub_admin_announcement_new')}")
        _type_into_editor(page, "Zorblax dates")
        page.fill("#id_show_from_date", "2099-01-05")
        page.fill("#id_show_until_date", "2099-01-04")
        page.get_by_role("button", name="Save").click()

        expect(page.locator(".pl-field-error")).to_have_text("Show until must be after Show from.")


def describe_the_rows():
    def it_switches_one_off_and_on_at_once(page, live_server, login_via_code):
        announcement = AnnouncementFactory(body="<p>Zorblax switch</p>")
        _sign_in_admin(login_via_code, "ann-admin-toggle-e2e@example.com")
        _spotlight(page, live_server)

        page.locator(f"{ROWS} .pl-toggle__slider").click()
        expect(page.locator(TOAST).last).to_have_text("Announcement off. Members no longer see it.")
        expect(page.locator(f"{ROWS} [data-announcement-status]")).to_have_text("Off")
        announcement.refresh_from_db()
        assert announcement.is_active is False

        page.locator(f"{ROWS} .pl-toggle__slider").click()
        expect(page.locator(f"{ROWS} [data-announcement-status]")).to_have_text("Live")
        announcement.refresh_from_db()
        assert announcement.is_active is True

    def it_reorders_with_the_buttons_and_keeps_the_order(page, live_server, login_via_code):
        for n in range(3):
            AnnouncementFactory(body=f"<p>Zorblax {n + 1}</p>", position=n)
        _sign_in_admin(login_via_code, "ann-admin-buttons-e2e@example.com")
        _spotlight(page, live_server)
        assert _row_texts(page) == ["Zorblax 1", "Zorblax 2", "Zorblax 3"]

        page.locator(ROWS).nth(0).get_by_role("button", name="Move announcement down").click()
        expect(page.locator(TOAST).last).to_have_text("Order saved.")
        assert _row_texts(page) == ["Zorblax 2", "Zorblax 1", "Zorblax 3"]
        page.locator(ROWS).nth(2).get_by_role("button", name="Move announcement up").click()
        expect(page.locator(f"{ROWS} [data-announcement-text]").nth(1)).to_have_text("Zorblax 3")

        page.reload()
        assert _row_texts(page) == ["Zorblax 2", "Zorblax 3", "Zorblax 1"]
        assert [row.body for row in Announcement.objects.in_order()] == [
            "<p>Zorblax 2</p>",
            "<p>Zorblax 3</p>",
            "<p>Zorblax 1</p>",
        ]

    def it_reorders_by_dragging_a_row(page, live_server, login_via_code):
        for n in range(3):
            AnnouncementFactory(body=f"<p>Zorblax {n + 1}</p>", position=n)
        _sign_in_admin(login_via_code, "ann-admin-drag-e2e@example.com")
        _spotlight(page, live_server)

        page.locator(ROWS).nth(2).locator(".pl-slide-grip").drag_to(page.locator(ROWS).nth(0))
        expect(page.locator(TOAST).last).to_have_text("Order saved.")
        page.reload()
        assert _row_texts(page) == ["Zorblax 3", "Zorblax 1", "Zorblax 2"]

    def it_deletes_one_through_the_confirm(page, live_server, login_via_code):
        AnnouncementFactory(body="<p>Zorblax keep</p>", position=0)
        AnnouncementFactory(body="<p>Zorblax goes</p>", position=1)
        _sign_in_admin(login_via_code, "ann-admin-delete-e2e@example.com")
        _spotlight(page, live_server)

        page.locator(ROWS).nth(1).locator("[data-announcement-delete]").click()
        dialog = page.locator(".pl-modal-backdrop:visible", has_text="Delete this announcement?")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("Members stop seeing it at once.")
        dialog.get_by_role("button", name="Delete").click()

        page.wait_for_url(re.compile(r"/manage/spotlight/(#announcements)?$"))
        expect(page.locator(TOAST).last).to_have_text("Announcement deleted. Members no longer see it.")
        assert _row_texts(page) == ["Zorblax keep"]
