"""End to end: the notification page's per-channel bulk switches (#524).

An admin presses the real buttons in a real browser and saves. Proves each control flips
only the unlocked cells of its own channel and scope (page, one section, one group inside
Admin / Permissions) and leaves everything else as it was, that nothing is saved until
Save, that the unsaved-changes guard notices a bulk flip, and that a member without
Discord linked is offered no Discord control anywhere. Every case reads the saved
preferences back after Save. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from core.events import settings_matrix
from core.events.registry import Channel
from core.models import NotificationPreference
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

ADMIN_EMAIL = "bulk-admin@example.com"
MEMBER_EMAIL = "bulk-member@example.com"


def _admin_signed_in(login_via_code):
    """An Admin with Discord linked, so every channel has live cells somewhere.

    Also a CMS Administrator and a guild lead, so Admin / Permissions holds three groups
    and a group-scoped switch has neighbours it must leave alone.
    """
    MembershipPlanFactory()
    login_via_code(ADMIN_EMAIL)
    user = get_user_model().objects.get(username=ADMIN_EMAIL)
    Member.objects.filter(user=user).update(fog_role=Member.FogRole.ADMIN, discord_user_id="bulk-admin-discord")
    member = Member.objects.get(user=user)
    member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
    GuildFactory(name="Bulk Guild", guild_lead=member)
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


class Placed(NamedTuple):
    """A rendered cell with where it sits: its section and, inside Admin / Permissions, its group."""

    section: str
    group: str
    cell: settings_matrix.Cell


def _cells(user):
    """Every rendered cell by field name, read back from the saved preferences."""
    return {
        cell.name: Placed(section.title, block.heading, cell)
        for section in settings_matrix.build_matrix(user)
        for block in section.blocks
        for row in block.rows
        for cell in row.cells
        if cell.present
    }


def _open_notifications(page, live_server):
    page.goto(f"{live_server.url}{reverse('hub_user_settings')}?tab=notifications")
    page.locator("#notif-classes").wait_for(state="visible")


def _assert_only(before, after, *, flipped_to, targeted):
    """Every cell ``targeted`` picks out is now ``flipped_to``; every other cell is unchanged.

    ``targeted`` sees a Placed and must itself require an editable cell, so padlocked Email,
    the bell and unlinked Discord always fall in the "unchanged" half.
    """
    hits = 0
    for name, placed in before.items():
        now = after[name].cell.enabled
        if targeted(placed):
            assert placed.cell.is_editable, name
            assert now is flipped_to, name
            hits += 1
        else:
            assert now == placed.cell.enabled, name
    assert hits, "the control targeted no cell"


def _save(page):
    page.locator('form[data-dirty-key="notifications"] button[type="submit"]').click()
    page.wait_for_url("**/settings/**tab=notifications")
    expect(page.locator("body")).to_contain_text("Notification preferences updated.")


def describe_notification_bulk_controls():
    def it_turns_off_every_unlocked_email_on_the_page_and_nothing_else(live_server, page, login_via_code):
        user = _admin_signed_in(login_via_code)
        before = _cells(user)
        assert any(p.cell.channel is Channel.EMAIL and p.cell.is_editable and p.cell.enabled for p in before.values())
        _open_notifications(page, live_server)

        page.get_by_role("button", name="Turn off Email for everything", exact=True).click()

        # Only the unlocked Email boxes flipped, in the page, before any save.
        for name, (_section, _group, cell) in before.items():
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

        # Padlocked Email, the bell, Push and Discord: exactly as they were.
        _assert_only(
            before,
            _cells(user),
            flipped_to=False,
            targeted=lambda p: p.cell.channel is Channel.EMAIL and p.cell.is_editable,
        )
        # A padlocked Email never gets a preference row written for it.
        assert not NotificationPreference.objects.filter(user=user, event_key="class_cancelled", channel="email")

    def it_turns_off_email_for_one_section_only(live_server, page, login_via_code):
        user = _admin_signed_in(login_via_code)
        before = _cells(user)
        billing_emails = [
            name
            for name, (section, _group, cell) in before.items()
            if section == "Billing" and cell.channel is Channel.EMAIL and cell.is_editable and cell.enabled
        ]
        assert billing_emails, "expected Billing to hold at least one switchable Email that is on"
        _open_notifications(page, live_server)

        page.get_by_role("button", name="Turn off Email for Billing", exact=True).click()
        _save(page)

        _assert_only(
            before,
            _cells(user),
            flipped_to=False,
            targeted=lambda p: p.section == "Billing" and p.cell.channel is Channel.EMAIL and p.cell.is_editable,
        )

    def it_turns_off_email_for_one_group_inside_admin_permissions_only(live_server, page, login_via_code):
        user = _admin_signed_in(login_via_code)
        before = _cells(user)
        groups = {p.group for p in before.values() if p.section == settings_matrix.ADMIN_SECTION}
        assert {"Admin", "CMS Administrator", "Guild leadership"} <= groups
        assert any(
            p.group == "Admin" and p.cell.channel is Channel.EMAIL and p.cell.is_editable and p.cell.enabled
            for p in before.values()
        )
        _open_notifications(page, live_server)

        # exact: "Turn off Email for Admin / Permissions" is a different, wider button.
        page.get_by_role("button", name="Turn off Email for Admin", exact=True).click()
        _save(page)

        # The other groups, padlocked cells, Push and Discord stay as they were.
        _assert_only(
            before,
            _cells(user),
            flipped_to=False,
            targeted=lambda p: (
                p.section == settings_matrix.ADMIN_SECTION
                and p.group == "Admin"
                and p.cell.channel is Channel.EMAIL
                and p.cell.is_editable
            ),
        )

    def it_turns_on_every_unlocked_push_on_the_page_and_nothing_else(live_server, page, login_via_code):
        user = _admin_signed_in(login_via_code)
        before = _cells(user)
        assert any(
            p.cell.channel is Channel.PUSH and p.cell.is_editable and not p.cell.enabled for p in before.values()
        )
        _open_notifications(page, live_server)

        page.get_by_role("button", name="Turn on Push for everything", exact=True).click()
        _save(page)

        _assert_only(
            before,
            _cells(user),
            flipped_to=True,
            targeted=lambda p: p.cell.channel is Channel.PUSH and p.cell.is_editable,
        )

    def it_turns_on_discord_for_one_section_for_a_linked_member(live_server, page, login_via_code):
        user = _admin_signed_in(login_via_code)
        before = _cells(user)
        assert any(
            p.section == "Classes"
            and p.cell.channel is Channel.DISCORD_DM
            and p.cell.is_editable
            and not p.cell.enabled
            for p in before.values()
        )
        _open_notifications(page, live_server)

        page.get_by_role("button", name="Turn on Discord for Classes", exact=True).click()
        _save(page)

        _assert_only(
            before,
            _cells(user),
            flipped_to=True,
            targeted=lambda p: p.section == "Classes" and p.cell.channel is Channel.DISCORD_DM and p.cell.is_editable,
        )
        assert NotificationPreference.objects.get(user=user, event_key="class_reminder", channel="discord_dm").enabled

    def it_offers_an_unlinked_member_no_discord_control_and_never_touches_discord(live_server, page, login_via_code):
        MembershipPlanFactory()
        login_via_code(MEMBER_EMAIL)
        user = get_user_model().objects.get(username=MEMBER_EMAIL)
        # A choice kept from a time Discord was linked: nothing on this page may wipe it.
        NotificationPreference.objects.create(user=user, event_key="class_reminder", channel="discord_dm", enabled=True)
        before = _cells(user)
        _open_notifications(page, live_server)

        # No Discord control at any scope: page, section or grid.
        assert page.locator(".pl-notif-onoff__btn").count() > 0
        expect(page.get_by_role("button", name=re.compile(r"Discord"))).to_have_count(0)

        page.get_by_role("button", name="Turn on all channels for everything", exact=True).click()
        _save(page)

        # Every editable cell is on now, and not one of them is Discord.
        _assert_only(
            before,
            _cells(user),
            flipped_to=True,
            targeted=lambda p: p.cell.is_editable,
        )
        assert not any(p.cell.channel is Channel.DISCORD_DM and p.cell.is_editable for p in before.values())
        discord_rows = NotificationPreference.objects.filter(user=user, channel="discord_dm")
        assert list(discord_rows.values_list("event_key", "enabled")) == [("class_reminder", True)]
