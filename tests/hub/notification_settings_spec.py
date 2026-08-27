"""Notifications settings tab saves NotificationPreference rows."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.events import settings_matrix
from core.models import NotificationPreference
from membership.models import Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _make_admin(client, username):
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw12345!")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save()
    client.login(username=username, password="pw12345!")
    return user, member


def _make_officer(client, username):
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw12345!")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.GUILD_OFFICER
    member.save()
    client.login(username=username, password="pw12345!")
    return user, member


def _preview_as(client, role):
    session = client.session
    session["view_as_role"] = role
    session.save()


def _matrix_sections(response):
    return [section for section, _rows in response.context["notif_matrix"]]


def describe_notifications_tab():
    def it_saves_push_and_email_toggles(client):
        User.objects.create_user(username="m", email="m@example.com", password="pw12345!")
        client.login(username="m", password="pw12345!")
        client.post(
            reverse("hub_user_settings"),
            {
                "form_id": "notifications",
                "pref__class_published__push": "on",
                "pref__tab_charged__email": "on",
            },
        )
        user = User.objects.get(username="m")
        assert (
            NotificationPreference.objects.get(user=user, event_key="class_published", channel="push").enabled is True
        )
        assert NotificationPreference.objects.get(user=user, event_key="tab_charged", channel="email").enabled is True

    def it_clears_unchecked_toggles(client):
        user = User.objects.create_user(username="m2", email="m2@example.com", password="pw12345!")
        NotificationPreference.objects.create(user=user, event_key="class_published", channel="push", enabled=True)
        client.login(username="m2", password="pw12345!")
        client.post(reverse("hub_user_settings"), {"form_id": "notifications"})  # nothing checked
        assert (
            NotificationPreference.objects.get(user=user, event_key="class_published", channel="push").enabled is False
        )

    def it_renders_the_push_label_with_a_platform_tooltip(client):
        User.objects.create_user(username="m3", email="m3@example.com", password="pw12345!")
        client.login(username="m3", password="pw12345!")
        content = client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
        assert "Push (Browser)" not in content  # renamed to plain "Push"
        # uses the canonical .pl-help hover bubble, not a browser title= tooltip
        assert "pl-help__bubble" in content
        assert "Android only for now. iOS coming soon." in content


def describe_build_matrix_staff_flag():
    def it_omits_the_staff_section_when_false():
        user, _member = _make_admin_matrix_user("flagadmin")
        with_staff = [s for s, _r in settings_matrix.build_matrix(user, include_staff_section=True)]
        without_staff = [s for s, _r in settings_matrix.build_matrix(user, include_staff_section=False)]
        assert settings_matrix.STAFF_SECTION in with_staff
        assert settings_matrix.STAFF_SECTION not in without_staff

    def it_never_renders_a_staff_only_channel_column_in_a_member_preview():
        # visible_channels must forward the flag: a channel only staff events offer must not
        # survive as a dead column when the staff section is hidden.
        user, _member = _make_admin_matrix_user("flagadmin2")
        member_view = settings_matrix.visible_channels(user, include_staff_section=False)
        # Every channel shown in the member-view preview is offered by some non-staff event.
        events = settings_matrix._visible_events(user, include_staff_section=False)
        for channel in member_view:
            assert any(event.channel(channel) is not None for event in events)


def _make_admin_matrix_user(username):
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save()
    return user, member


def describe_view_as_staff_section():
    def it_shows_the_staff_section_to_an_admin_viewing_as_self(client):
        _make_admin(client, "vaself")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION in _matrix_sections(response)

    def it_hides_the_staff_section_when_an_admin_previews_as_member(client):
        _make_admin(client, "vamember")
        _preview_as(client, "member")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION not in _matrix_sections(response)

    def it_hides_the_staff_section_when_an_admin_previews_as_guest(client):
        _make_admin(client, "vaguest")
        _preview_as(client, "guest")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION not in _matrix_sections(response)

    def it_shows_the_staff_section_when_an_admin_previews_as_officer(client):
        _make_admin(client, "vaofficer")
        _preview_as(client, "guild_officer")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION in _matrix_sections(response)

    def it_hides_the_staff_section_when_an_officer_previews_as_member(client):
        _make_officer(client, "offviewmember")
        _preview_as(client, "member")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION not in _matrix_sections(response)

    def it_keeps_the_staff_section_for_a_guild_lead_whose_role_is_member(client):
        # A lead's fog_role is member, so include_staff stays True (the flag flips only when a
        # higher-role holder previews down) — they keep the staff rows their led_guilds grant.
        user = User.objects.create_user(username="leadmember", email="lead@example.com", password="pw12345!")
        member = Member.objects.get(user=user)
        GuildFactory(guild_lead=member)
        client.login(username="leadmember", password="pw12345!")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION in _matrix_sections(response)

    def it_does_not_wipe_staff_prefs_when_saving_while_previewing_as_member(client):
        # The §5.2 wipe trap: the GET hid the staff section, so the POST omits its checkboxes.
        # save_matrix must receive include_staff_section=False and skip staff events, or every
        # staff pref would be written enabled=False.
        user, _member = _make_admin(client, "wipeadmin")
        NotificationPreference.objects.create(user=user, event_key="new_member_joined", channel="email", enabled=True)
        _preview_as(client, "member")
        client.post(reverse("hub_user_settings"), {"form_id": "notifications"})  # no staff checkbox present
        pref = NotificationPreference.objects.get(user=user, event_key="new_member_joined", channel="email")
        assert pref.enabled is True  # untouched — not silently wiped

    def it_still_saves_staff_prefs_for_an_admin_viewing_as_self(client):
        # Control: viewing as self, the staff checkbox is present, so an unchecked POST clears it.
        user, _member = _make_admin(client, "selfsave")
        NotificationPreference.objects.create(user=user, event_key="new_member_joined", channel="email", enabled=True)
        client.post(reverse("hub_user_settings"), {"form_id": "notifications"})  # staff box unchecked
        pref = NotificationPreference.objects.get(user=user, event_key="new_member_joined", channel="email")
        assert pref.enabled is False
