"""BDD specs for the hub-native admin pages: voting dashboard, members, member edit, site settings."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member

pytestmark = pytest.mark.django_db


def _create_superuser(client: Client, *, username: str = "admin") -> User:
    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def _create_member_user(*, username: str, fog_role: str = Member.FogRole.MEMBER) -> User:
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member
    member.fog_role = fog_role
    if not member.full_legal_name:
        member.full_legal_name = username.title()
    member.save()
    return user


def describe_admin_voting_dashboard():
    def it_requires_login(client):
        response = client.get(reverse("hub_admin_voting_dashboard"))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_member_user(username="plain")
        client.login(username=user.username, password="p")
        response = client.get(reverse("hub_admin_voting_dashboard"))
        assert response.status_code == 403

    def it_renders_for_admin(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_voting_dashboard"))
        assert response.status_code == 200
        assert b"Voting Dashboard" in response.content
        assert "stats" in response.context


def describe_admin_members():
    def it_requires_login(client):
        response = client.get(reverse("hub_admin_members"))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_member_user(username="m1")
        client.login(username=user.username, password="p")
        response = client.get(reverse("hub_admin_members"))
        assert response.status_code == 403

    def it_renders_for_admin_with_default_active_filter(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_members"))
        assert response.status_code == 200
        assert b"Manage Members" in response.content
        assert response.context["status_filter"] == "active"

    def it_filters_by_all_status(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_members") + "?status=all")
        assert response.status_code == 200
        assert response.context["status_filter"] == "all"

    def it_filters_by_search_role_and_type(client):
        _create_superuser(client)
        target = _create_member_user(username="searchtarget", fog_role=Member.FogRole.ADMIN)
        target.member.full_legal_name = "Findable Person"
        target.member.member_type = Member.MemberType.STANDARD
        target.member.save()
        response = client.get(reverse("hub_admin_members") + "?status=all&q=Findable&role=admin&type=standard")
        assert response.status_code == 200
        assert response.context["search"] == "Findable"
        assert response.context["role_filter"] == "admin"
        assert response.context["type_filter"] == "standard"
        assert b"Findable Person" in response.content


def describe_admin_member_invite():
    def it_requires_login(client):
        response = client.post(reverse("hub_admin_member_invite"), {"email": "new@x.com"})
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_member_user(username="mi1")
        client.login(username=user.username, password="p")
        response = client.post(reverse("hub_admin_member_invite"), {"email": "new@x.com"})
        assert response.status_code == 403

    def it_rejects_get(client):
        _create_superuser(client, username="invadmin")
        response = client.get(reverse("hub_admin_member_invite"))
        assert response.status_code == 405

    def it_sends_an_invite_and_redirects(client):
        from core.models import Invite

        _create_superuser(client, username="invadmin")
        response = client.post(reverse("hub_admin_member_invite"), {"email": "newbie@x.com"})
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_members")
        assert Invite.objects.filter(email="newbie@x.com").exists()

    def it_shows_an_error_for_an_existing_member(client):
        from core.models import Invite

        _create_superuser(client, username="invadmin")
        _create_member_user(username="taken")  # active member with email taken@x.com
        response = client.post(reverse("hub_admin_member_invite"), {"email": "taken@x.com"}, follow=True)
        assert b"already exists" in response.content
        assert not Invite.objects.filter(email="taken@x.com").exists()


def describe_admin_member_edit_role_dispatch():
    def it_promotes_to_instructor(client):
        _create_superuser(client)
        target = _create_member_user(username="becomeinst")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={
                "full_legal_name": target.member.full_legal_name,
                "preferred_name": "",
                "pronouns": "",
                "discord_handle": "",
                "about_me": "",
                "status": Member.Status.ACTIVE,
                "member_type": Member.MemberType.STANDARD,
                "role": "instructor",
                "show_in_directory": "on",
            },
        )
        assert response.status_code == 302
        target.member.refresh_from_db()
        assert target.member.fog_role == Member.FogRole.MEMBER
        assert target.member.status == Member.Status.ACTIVE
        assert target.member.instructor_slug != ""

    def it_demotes_to_guest_by_setting_status_former(client):
        _create_superuser(client)
        target = _create_member_user(username="becomeguest")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={
                "full_legal_name": target.member.full_legal_name,
                "preferred_name": "",
                "pronouns": "",
                "discord_handle": "",
                "about_me": "",
                "status": Member.Status.ACTIVE,
                "member_type": Member.MemberType.STANDARD,
                "role": "guest",
                "show_in_directory": "on",
            },
        )
        assert response.status_code == 302
        target.member.refresh_from_db()
        assert target.member.status == Member.Status.FORMER
        assert target.member.fog_role == Member.FogRole.MEMBER

    def it_initial_role_reflects_existing_instructor_record(client):
        from classes.factories import InstructorFactory

        _create_superuser(client)
        target = _create_member_user(username="alreadyinst")
        InstructorFactory(user=target)
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        assert response.context["form"]["role"].initial == "instructor"

    def it_initial_role_reflects_inactive_status_as_guest(client):
        _create_superuser(client)
        target = _create_member_user(username="formerguy")
        target.member.status = Member.Status.FORMER
        target.member.save(update_fields=["status"])
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        assert response.context["form"]["role"].initial == "guest"


def describe_admin_member_edit():
    def it_requires_login(client):
        m = _create_member_user(username="target")
        response = client.get(reverse("hub_admin_member_edit", args=[m.member.pk]))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        target = _create_member_user(username="target2")
        plain = _create_member_user(username="plain2")
        client.login(username=plain.username, password="p")
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 403

    def it_renders_edit_form_for_admin(client):
        _create_superuser(client)
        target = _create_member_user(username="target3")
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        assert b"Edit Member" in response.content

    def it_saves_changes_and_redirects(client):
        _create_superuser(client)
        target = _create_member_user(username="target4")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={
                "full_legal_name": "Updated Name",
                "preferred_name": "",
                "pronouns": "",
                "discord_handle": "",
                "about_me": "",
                "status": Member.Status.ACTIVE,
                "member_type": Member.MemberType.STANDARD,
                "role": Member.FogRole.MEMBER,
                "show_in_directory": "on",
            },
        )
        assert response.status_code == 302
        target.member.refresh_from_db()
        assert target.member.full_legal_name == "Updated Name"

    def it_re_renders_on_invalid_post(client):
        _create_superuser(client)
        target = _create_member_user(username="target5")
        response = client.post(
            reverse("hub_admin_member_edit", args=[target.member.pk]),
            data={"full_legal_name": ""},
        )
        assert response.status_code == 200

    def it_404s_for_unknown_member(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_member_edit", args=[99999]))
        assert response.status_code == 404


def describe_admin_site_settings():
    def it_requires_login(client):
        response = client.get(reverse("hub_admin_site_settings"))
        assert response.status_code == 302

    def it_forbids_plain_members(client):
        user = _create_member_user(username="plain3")
        client.login(username=user.username, password="p")
        response = client.get(reverse("hub_admin_site_settings"))
        assert response.status_code == 403

    def it_renders_settings_form_for_admin(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings"))
        assert response.status_code == 200
        assert b"Site Settings" in response.content

    def it_saves_changes_and_redirects(client):
        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "feeds-TOTAL_FORMS": "0",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        config = SiteConfiguration.load()
        assert config.registration_mode == SiteConfiguration.RegistrationMode.OPEN

    def it_creates_calendar_feed_from_formset(client):
        from core.models import CalendarFeed

        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "feeds-TOTAL_FORMS": "1",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
                "feeds-0-name": "Workshops",
                "feeds-0-ical_url": "https://example.com/workshops.ics",
                "feeds-0-color": "#FF8800",
            },
        )
        assert response.status_code == 302
        assert CalendarFeed.objects.filter(name="Workshops").exists()

    def it_discards_blank_calendar_feed_rows(client):
        from core.models import CalendarFeed

        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "feeds-TOTAL_FORMS": "1",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
                "feeds-0-name": "",
                "feeds-0-ical_url": "",
                "feeds-0-color": "#EEB44B",
            },
        )
        assert response.status_code == 302
        assert CalendarFeed.objects.count() == 0

    def it_deletes_calendar_feed_via_formset(client):
        from core.models import CalendarFeed

        _create_superuser(client)
        feed = CalendarFeed.objects.create(name="Old", ical_url="https://example.com/old.ics", color="#EEB44B")
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "sync_classes_enabled": "",
                "classes_calendar_color": "#abcdef",
                "mailchimp_api_key": "",
                "mailchimp_list_id": "",
                "google_analytics_measurement_id": "",
                "feeds-TOTAL_FORMS": "1",
                "feeds-INITIAL_FORMS": "1",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
                "feeds-0-id": str(feed.pk),
                "feeds-0-name": feed.name,
                "feeds-0-ical_url": feed.ical_url,
                "feeds-0-color": feed.color,
                "feeds-0-DELETE": "on",
            },
        )
        assert response.status_code == 302
        assert not CalendarFeed.objects.filter(pk=feed.pk).exists()

    def it_renders_calendar_tab_when_requested(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings") + "?tab=calendar")
        assert response.status_code == 200
        assert response.context["active_tab"] == "calendar"

    def it_re_renders_on_invalid_post(client):
        _create_superuser(client)
        response = client.post(
            reverse("hub_admin_site_settings"),
            data={"registration_mode": "not-a-real-mode"},
        )
        assert response.status_code == 200


def describe_admin_site_settings_legacy_cms():
    def it_renders_legacy_cms_tab_when_active(client):
        _create_superuser(client)
        response = client.get(reverse("hub_admin_site_settings") + "?tab=legacy-cms")
        assert response.status_code == 200
        assert b"Legacy CMS" in response.content
        assert response.context["active_tab"] == "legacy-cms"

    def it_syncs_now_on_post_with_sync_now_action(client):
        from unittest.mock import patch

        _create_superuser(client)
        with patch("classes.import_service.sync_legacy_cms", return_value=5) as mock_sync:
            response = client.post(
                reverse("hub_admin_site_settings"),
                data={"action": "sync_now"},
            )
        assert response.status_code == 302
        assert "tab=legacy-cms" in response["Location"]
        mock_sync.assert_called_once()

    def it_handles_sync_now_failure_gracefully(client):
        from unittest.mock import patch

        _create_superuser(client)
        with patch("classes.import_service.sync_legacy_cms", side_effect=RuntimeError("connection refused")):
            response = client.post(
                reverse("hub_admin_site_settings"),
                data={"action": "sync_now"},
            )
        assert response.status_code == 302
        assert "tab=legacy-cms" in response["Location"]

    def it_includes_instructor_sync_rows_in_context(client):
        from classes.factories import InstructorFactory

        _create_superuser(client)
        InstructorFactory(full_legal_name="Test Instructor")
        response = client.get(reverse("hub_admin_site_settings") + "?tab=legacy-cms")
        assert response.status_code == 200
        rows = response.context["instructor_sync_rows"]
        assert any(row["instructor"].display_name == "Test Instructor" for row in rows)


def describe_fog_admin_required():
    def it_redirects_anonymous_users_to_login(rf):
        from django.contrib.auth.models import AnonymousUser

        from hub.view_as import fog_admin_required

        @fog_admin_required
        def view(request):
            return "ok"

        request = rf.get("/")
        request.user = AnonymousUser()
        response = view(request)
        # @login_required is the outermost wrapper, so anonymous users redirect
        # before the view_as admin check ever runs.
        assert response.status_code == 302

    def it_returns_403_for_authenticated_non_admin(rf):
        from hub.view_as import fog_admin_required

        @fog_admin_required
        def view(request):
            return "ok"

        user = _create_member_user(username="nonadmin_decorator")
        request = rf.get("/")
        request.user = user
        # No view_as attribute attached — simulates the inner check rejecting
        # a user who doesn't actually hold admin.
        response = view(request)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Hub-native email-address management on the member edit page
# ---------------------------------------------------------------------------


def _target_with_email(*, username: str = "edittarget") -> User:
    """An editable member with a single primary, verified EmailAddress."""
    from allauth.account.models import EmailAddress

    user = _create_member_user(username=username)
    EmailAddress.objects.filter(user=user).delete()
    EmailAddress.objects.create(user=user, email=f"{username}@x.com", verified=True, primary=True)
    return user


def describe_admin_member_email_panel():
    def it_renders_emails_and_the_add_form_for_a_linked_member(client):
        _create_superuser(client)
        target = _target_with_email()
        response = client.get(reverse("hub_admin_member_edit", args=[target.member.pk]))
        assert response.status_code == 200
        assert b"Email addresses" in response.content
        assert b"edittarget@x.com" in response.content
        assert reverse("hub_admin_member_email_add", args=[target.member.pk]).encode() in response.content

    def it_shows_a_note_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="airtable-only@x.com")
        response = client.get(reverse("hub_admin_member_edit", args=[member.pk]))
        assert response.status_code == 200
        assert response.context["member_emails"] is None
        assert b"No linked user yet" in response.content


def describe_admin_member_email_add():
    def it_adds_a_verified_non_primary_alias(client):
        from allauth.account.models import EmailAddress

        _create_superuser(client)
        target = _target_with_email()
        response = client.post(
            reverse("hub_admin_member_email_add", args=[target.member.pk]),
            data={"email": "alt@example.com"},
        )
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[target.member.pk])
        created = EmailAddress.objects.get(user=target, email="alt@example.com")
        assert created.verified is True
        assert created.primary is False

    def it_rejects_a_duplicate_without_creating(client):
        from allauth.account.models import EmailAddress

        _create_superuser(client)
        target = _target_with_email()
        response = client.post(
            reverse("hub_admin_member_email_add", args=[target.member.pk]),
            data={"email": "edittarget@x.com"},
        )
        assert response.status_code == 302
        assert EmailAddress.objects.filter(user=target).count() == 1

    def it_forbids_a_plain_member(client):
        target = _target_with_email(username="add_forbid")
        plain = _create_member_user(username="add_plain")
        client.login(username=plain.username, password="p")
        response = client.post(
            reverse("hub_admin_member_email_add", args=[target.member.pk]),
            data={"email": "nope@example.com"},
        )
        assert response.status_code == 403

    def it_redirects_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="airtable@x.com")
        response = client.post(
            reverse("hub_admin_member_email_add", args=[member.pk]),
            data={"email": "new@example.com"},
        )
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[member.pk])

    def it_rejects_get(client):
        _create_superuser(client)
        target = _target_with_email(username="add_get")
        response = client.get(reverse("hub_admin_member_email_add", args=[target.member.pk]))
        assert response.status_code == 405


def describe_admin_member_email_actions():
    def _alias(user, email, *, verified=True, primary=False):
        from allauth.account.models import EmailAddress

        return EmailAddress.objects.create(user=user, email=email, verified=verified, primary=primary)

    def it_removes_a_non_primary_alias(client):
        from allauth.account.models import EmailAddress

        _create_superuser(client)
        target = _target_with_email(username="rm")
        alias = _alias(target, "gone@example.com")
        response = client.post(
            reverse("hub_admin_member_email_remove", args=[target.member.pk, alias.pk]),
        )
        assert response.status_code == 302
        assert not EmailAddress.objects.filter(pk=alias.pk).exists()

    def it_promotes_a_verified_alias_to_primary(client):
        _create_superuser(client)
        target = _target_with_email(username="sp")
        alias = _alias(target, "next@example.com", verified=True, primary=False)
        response = client.post(
            reverse("hub_admin_member_email_set_primary", args=[target.member.pk, alias.pk]),
        )
        assert response.status_code == 302
        alias.refresh_from_db()
        assert alias.primary is True

    def it_toggles_verified(client):
        _create_superuser(client)
        target = _target_with_email(username="tv")
        alias = _alias(target, "unv@example.com", verified=False, primary=False)
        response = client.post(
            reverse("hub_admin_member_email_toggle_verified", args=[target.member.pk, alias.pk]),
        )
        assert response.status_code == 302
        alias.refresh_from_db()
        assert alias.verified is True

    def it_redirects_remove_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="a@x.com")
        response = client.post(reverse("hub_admin_member_email_remove", args=[member.pk, 1]))
        assert response.status_code == 302
        assert response.url == reverse("hub_admin_member_edit", args=[member.pk])

    def it_redirects_set_primary_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="b@x.com")
        response = client.post(reverse("hub_admin_member_email_set_primary", args=[member.pk, 1]))
        assert response.status_code == 302

    def it_redirects_toggle_verified_for_an_unlinked_member(client):
        from tests.membership.factories import MemberFactory

        _create_superuser(client)
        member = MemberFactory(user=None, _pre_signup_email="c@x.com")
        response = client.post(reverse("hub_admin_member_email_toggle_verified", args=[member.pk, 1]))
        assert response.status_code == 302
