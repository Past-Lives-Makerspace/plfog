"""End to end: the notification page's per-channel bulk switches (#524).

An admin presses the real buttons in a real browser and saves. Proves the controls flip
only the unlocked cells of their channel and scope (padlocked Email, the bell, Push and
Discord stay put), that nothing is saved until Save, and that the unsaved-changes guard
notices a bulk flip. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from core.events import settings_matrix
from core.events.registry import Channel
from core.models import NotificationPreference
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

ADMIN_EMAIL = "bulk-admin@example.com"


def _admin_signed_in(login_via_code):
    """An Admin with Discord linked, so every channel has live cells somewhere."""
    MembershipPlanFactory()
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    Member.objects.filter(user=user).update(fog_role=Member.FogRole.ADMIN, discord_user_id="bulk-admin-discord")
    # Choices a bulk Email switch must not touch: Push and Discord on some rows, one of
    # them padlocked (class_cancelled forces its email).
    for key, channel in (
        ("class_reminder", Channel.PUSH),
        ("class_cancelled", Channel.PUSH),
        ("class_cancelled", Channel.DISCORD_DM),
        ("new_member_joined", Channel.DISCORD_DM),
        ("tab_charged", Channel.EMAIL),
    ):
        NotificationPreference.objects.create(user=user, event_key=key, channel=channel.value, enabled=True)
    return user


def _cells(user):
    """Every rendered cell by field name, with the section it sits in."""
    return {
        cell.name: (section.title, cell)
        for section in settings_matrix.build_matrix(user)
        for block in section.blocks
        for row in block.rows
        for cell in row.cells
        if cell.present
    }


def _open_notifications(page, live_server):
    page.goto(f"{live_server.url}{reverse('hub_user_settings')}?tab=notifications")
    page.locator("#notif-admin-permissions").wait_for(state="visible")


def _save(page):
    page.locator('form[data-dirty-key="notifications"] button[type="submit"]').click()
    page.wait_for_url("**/settings/**tab=notifications")
    expect(page.locator("body")).to_contain_text("Notification preferences updated.")


def describe_notification_bulk_controls():
    def it_turns_off_every_unlocked_email_on_the_page_and_nothing_else(live_server, page, login_via_code):
        user = _admin_signed_in(login_via_code)
        before = _cells(user)
        assert any(cell.channel is Channel.EMAIL and cell.is_editable and cell.enabled for _s, cell in before.values())
        _open_notifications(page, live_server)

        page.get_by_role("button", name="Turn off Email for everything", exact=True).click()

        # Only the unlocked Email boxes flipped, in the page, before any save.
        for name, (_section, cell) in before.items():
            box = page.locator(f'input[name="{name}"]')
            if cell.channel is Channel.EMAIL and cell.is_editable:
                expect(box).not_to_be_checked()
            elif cell.enabled:
                expect(box).to_be_checked()
        # Nothing is saved until Save.
        assert NotificationPreference.objects.get(user=user, event_key="tab_charged", channel="email").enabled

        # The unsaved-changes guard noticed the flip: leaving the tab asks first.
        page.locator("button.vote-tab", has_text="Guilds").click()
        dialog = page.get_by_role("dialog").filter(has_text="Discard unsaved changes?")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="Stay").click()
        expect(dialog).to_be_hidden()

        _save(page)

        after = _cells(user)
        for name, (_section, cell) in before.items():
            now = after[name][1]
            if cell.channel is Channel.EMAIL and cell.is_editable:
                assert now.enabled is False, name
            else:
                # Padlocked Email, the bell, Push and Discord: exactly as they were.
                assert now.enabled == cell.enabled, name
        # A padlocked Email never gets a preference row written for it.
        assert not NotificationPreference.objects.filter(user=user, event_key="class_cancelled", channel="email")

    def it_turns_off_email_for_one_section_only(live_server, page, login_via_code):
        user = _admin_signed_in(login_via_code)
        before = _cells(user)
        billing_emails = [
            name
            for name, (section, cell) in before.items()
            if section == "Billing" and cell.channel is Channel.EMAIL and cell.is_editable and cell.enabled
        ]
        assert billing_emails, "expected Billing to hold at least one switchable Email that is on"
        _open_notifications(page, live_server)

        page.get_by_role("button", name="Turn off Email for Billing", exact=True).click()
        _save(page)

        after = _cells(user)
        for name, (section, cell) in before.items():
            if section == "Billing" and cell.channel is Channel.EMAIL and cell.is_editable:
                assert after[name][1].enabled is False, name
            else:
                assert after[name][1].enabled == cell.enabled, name
