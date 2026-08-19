"""BDD specs for hub views."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import Client, RequestFactory

from core.models import SiteConfiguration
from hub.context_processors import hub_sidebar
from hub.views import _get_hub_context, _get_member
from membership.models import Member
from tests.membership.factories import GuildFactory, MemberContactFactory, MemberFactory

# Every real profile POST carries the contacts inline-formset management form; test POSTs
# must include it too or the formset is invalid and the profile save is skipped.
_CONTACTS_MGMT = {"contacts-TOTAL_FORMS": "0", "contacts-INITIAL_FORMS": "0"}


@pytest.mark.django_db
def describe_get_hub_context():
    """Tests for _get_hub_context helper via the guild_voting view."""

    def it_includes_guilds_in_context(client: Client):
        User.objects.create_user(username="u1", password="pass")
        g1 = GuildFactory(name="Alpha")
        g2 = GuildFactory(name="Beta")
        client.login(username="u1", password="pass")

        response = client.get("/guilds/voting/")

        assert g1 in list(response.context["guilds"])
        assert g2 in list(response.context["guilds"])

    def it_returns_initials_from_member(client: Client):
        User.objects.create_user(username="u2", password="pass", first_name="Jane", last_name="Doe")
        client.login(username="u2", password="pass")

        response = client.get("/guilds/voting/")

        assert response.context["user_initials"] == "JD"

    def it_returns_empty_initials_when_no_member_linked(client: Client):
        user = User.objects.create_user(username="u3", password="pass", first_name="Jane")
        client.login(username="u3", password="pass")
        Member.objects.filter(user=user).delete()

        response = client.get("/guilds/voting/")

        assert response.context["user_initials"] == ""

    def it_returns_empty_initials_for_unauthenticated_request(rf: RequestFactory):
        """Calling _get_hub_context directly with an anonymous user covers the
        is_authenticated=False branch."""
        request = rf.get("/guilds/voting/")
        request.user = AnonymousUser()

        ctx = _get_hub_context(request)

        assert ctx["user_initials"] == ""

    def it_excludes_inactive_guilds_and_includes_active_ones(rf: RequestFactory):
        user = User.objects.create_user(username="hubctxguilds", password="pass")
        active = GuildFactory(name="Active Hub Guild", is_active=True)
        inactive = GuildFactory(name="Retired Hub Guild", is_active=False)
        request = rf.get("/guilds/voting/")
        request.user = user

        guilds = list(_get_hub_context(request)["guilds"])

        assert active in guilds
        assert inactive not in guilds


@pytest.mark.django_db
def describe_hub_sidebar():
    """Tests for the hub_sidebar context processor (registered globally in settings.py,
    so it runs on every authenticated page render alongside _get_hub_context)."""

    def it_excludes_inactive_guilds_and_includes_active_ones(rf: RequestFactory):
        user = User.objects.create_user(username="sidebarguilds", password="pass")
        active = GuildFactory(name="Active Sidebar Guild", is_active=True)
        inactive = GuildFactory(name="Retired Sidebar Guild", is_active=False)
        request = rf.get("/guilds/voting/")
        request.user = user

        guilds = list(hub_sidebar(request)["guilds"])

        assert active in guilds
        assert inactive not in guilds

    def it_returns_no_guilds_for_an_anonymous_request(rf: RequestFactory):
        GuildFactory(name="Anon Guild", is_active=True)
        request = rf.get("/guilds/voting/")
        request.user = AnonymousUser()

        ctx = hub_sidebar(request)

        assert list(ctx["guilds"]) == []


def describe_get_member():
    """Tests for _get_member helper (callers are @login_required)."""

    @pytest.mark.django_db
    def it_returns_member_when_linked(rf: RequestFactory):
        user = User.objects.create_user(username="has_member", password="pass")
        request = rf.get("/settings/profile/")
        request.user = user

        result = _get_member(request)

        assert result == user.member

    @pytest.mark.django_db
    def it_returns_none_when_no_member_linked(rf: RequestFactory):
        user = User.objects.create_user(username="no_member", password="pass")
        Member.objects.filter(user=user).delete()
        user = User.objects.get(pk=user.pk)  # Refresh to clear cached .member
        request = rf.get("/settings/profile/")
        request.user = user

        result = _get_member(request)

        assert result is None


@pytest.mark.django_db
def describe_guild_voting():
    def it_requires_login(client: Client):
        response = client.get("/guilds/voting/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_renders_voting_page(client: Client):
        User.objects.create_user(username="voter", password="pass")
        client.login(username="voter", password="pass")

        response = client.get("/guilds/voting/")

        assert response.status_code == 200


@pytest.mark.django_db
def describe_member_directory():
    def describe_when_anonymous():
        def it_redirects_to_login_by_default(client: Client):
            response = client.get("/members/")
            assert response.status_code == 302
            assert response["Location"] == "/accounts/login/?next=/members/"

        def describe_with_the_public_directory_setting_on():
            def it_renders_without_signing_in(client: Client):
                config = SiteConfiguration.load()
                config.member_directory_public = True
                config.save()
                MemberFactory(full_legal_name="Paula Public", status="active", show_in_directory=True)

                response = client.get("/members/")

                assert response.status_code == 200
                assert b"Paula Public" in response.content

    def it_renders_for_a_signed_in_member(client: Client):
        # member_directory_public stays at its default (False) — sign-in alone grants access.
        User.objects.create_user(username="signed_in_viewer", password="pass")
        MemberFactory(full_legal_name="Dora Directory", status="active", show_in_directory=True)
        client.login(username="signed_in_viewer", password="pass")

        response = client.get("/members/")

        assert response.status_code == 200
        assert b"Dora Directory" in response.content

    def it_lists_active_opted_in_members(client: Client):
        viewer = User.objects.create_user(username="viewer", password="pass")
        # The viewer's own auto-created member is listed by default — hide it so this
        # test measures only the members it sets up.
        viewer.member.show_in_directory = False
        viewer.member.save(update_fields=["show_in_directory"])
        m1 = MemberFactory(full_legal_name="Alice", status="active", show_in_directory=True)
        m2 = MemberFactory(full_legal_name="Bob", status="active", show_in_directory=True)
        MemberFactory(full_legal_name="Hidden", status="active", show_in_directory=False)
        MemberFactory(full_legal_name="Former", status="former", show_in_directory=True)
        client.login(username="viewer", password="pass")

        response = client.get("/members/")

        assert response.status_code == 200
        members = list(response.context["members"])
        assert m1 in members
        assert m2 in members
        assert len(members) == 2

    def it_shows_pronouns_in_directory(client: Client):
        User.objects.create_user(username="viewer", password="pass")
        MemberFactory(full_legal_name="Sam", show_in_directory=True, pronouns=Member.Pronouns.THEY_THEM)
        client.login(username="viewer", password="pass")

        response = client.get("/members/")

        assert "they/them" in response.content.decode()

    def it_hides_prefer_not_to_share_pronouns(client: Client):
        User.objects.create_user(username="viewer2", password="pass")
        MemberFactory(full_legal_name="Alex", show_in_directory=True, pronouns=Member.Pronouns.PREFER_NOT)
        client.login(username="viewer2", password="pass")

        response = client.get("/members/")

        assert "prefer not to share" not in response.content.decode()

    def it_does_not_trigger_n_plus_1_on_primary_email(client: Client):
        """Regression guard for the member.primary_email N+1.

        The template accesses ``member.primary_email`` several times per row. Without
        a ``Prefetch`` of the primary allauth EmailAddress, this would fire N queries
        per member. See ``hub.views.member_directory`` and
        docs/superpowers/specs/2026-04-07-user-email-aliases-design.md.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        # Five members with linked users → each has an auto-created primary EmailAddress.
        for i in range(5):
            user = User.objects.create_user(username=f"m{i}", email=f"m{i}@example.com")
            member = user.member
            member.show_in_directory = True
            member.full_legal_name = f"Member {i}"
            member.save(update_fields=["show_in_directory", "full_legal_name"])

        viewer = User.objects.create_user(username="viewer-n1", password="pass")
        client.force_login(viewer)

        with CaptureQueriesContext(connection) as ctx:
            response = client.get("/members/")

        assert response.status_code == 200
        # N+1 would show ~4 queries per member. Prefetch should give us 1 query total
        # for the primary EmailAddress rows, regardless of member count.
        email_q = [q for q in ctx.captured_queries if "account_emailaddress" in q["sql"].lower()]
        assert len(email_q) <= 2, f"N+1 on EmailAddress: {len(email_q)} queries for 5 members"


@pytest.mark.django_db
def describe_guild_detail():
    def it_is_accessible_to_anonymous_guests(client: Client):
        guild = GuildFactory()
        response = client.get(f"/guilds/{guild.slug}/")
        assert response.status_code == 200

    def it_renders_guild_detail(client: Client):
        User.objects.create_user(username="viewer", password="pass")
        guild = GuildFactory(name="Ceramics")
        client.login(username="viewer", password="pass")

        response = client.get(f"/guilds/{guild.slug}/")

        assert response.status_code == 200
        assert response.context["guild"] == guild

    def it_returns_404_for_nonexistent_guild(client: Client):
        User.objects.create_user(username="viewer2", password="pass")
        client.login(username="viewer2", password="pass")

        response = client.get("/guilds/99999/")

        assert response.status_code == 404


@pytest.mark.django_db
def describe_user_settings():
    def it_requires_login(client: Client):
        response = client.get("/settings/")
        assert response.status_code == 302

    def it_renders_profile_form_and_notification_matrix(client: Client):
        user = User.objects.create_user(username="withmember", password="pass")
        client.login(username="withmember", password="pass")

        response = client.get("/settings/")

        assert response.status_code == 200
        assert response.context["member"] == user.member
        assert response.context["profile_form"] is not None
        assert response.context["notif_matrix"] is not None
        assert response.context["notif_channels"] is not None
        assert "add_email_form" in response.context
        assert "email_addresses" in response.context

    def it_shows_admins_a_capabilities_link_in_the_staff_section(client: Client):
        from membership.models import Member

        user = User.objects.create_user(username="adminsettings", password="pass")
        member = user.member
        member.fog_role = Member.FogRole.ADMIN
        member.save()
        client.login(username="adminsettings", password="pass")

        response = client.get("/settings/?tab=notifications")

        assert response.status_code == 200
        # The link points at the Permissions tab (where the capability toggles live).
        assert "tab=permissions" in response.context["capabilities_url"]
        assert b"Manage your admin duties" in response.content

    def it_hides_the_capabilities_link_from_non_admins(client: Client):
        User.objects.create_user(username="plainsettings", password="pass")
        client.login(username="plainsettings", password="pass")

        response = client.get("/settings/?tab=notifications")

        assert response.status_code == 200
        assert response.context["capabilities_url"] is None
        assert b"Manage your admin duties" not in response.content

    def it_defaults_to_profile_tab(client: Client):
        User.objects.create_user(username="tabdefault", password="pass")
        client.login(username="tabdefault", password="pass")

        response = client.get("/settings/")

        assert response.context["active_tab"] == "profile"

    def it_honors_tab_query_param(client: Client):
        User.objects.create_user(username="tabemails", password="pass")
        client.login(username="tabemails", password="pass")

        response = client.get("/settings/?tab=emails")

        assert response.context["active_tab"] == "emails"

    def it_falls_back_to_profile_when_tab_param_is_not_whitelisted(client: Client):
        """Regression: ``active_tab`` flows into an Alpine x-data JS expression,
        so raw user input must not reach the template — arbitrary values are
        coerced back to ``profile`` to prevent XSS."""
        User.objects.create_user(username="xssguard", password="pass")
        client.login(username="xssguard", password="pass")

        response = client.get("/settings/?tab=%27%2Balert(1)%2B%27")

        assert response.context["active_tab"] == "profile"
        # And the raw payload never lands in the rendered HTML.
        assert b"alert(1)" not in response.content

    def it_renders_with_no_member_linked(client: Client):
        user = User.objects.create_user(username="nomember", password="pass")
        client.login(username="nomember", password="pass")
        Member.objects.filter(user=user).delete()

        response = client.get("/settings/")

        assert response.status_code == 200
        assert response.context["member"] is None
        assert response.context["profile_form"] is None

    def it_shows_info_message_when_no_member(client: Client):
        user = User.objects.create_user(username="nolink", password="pass")
        client.login(username="nolink", password="pass")
        Member.objects.filter(user=user).delete()

        response = client.get("/settings/")

        messages_list = list(response.context["messages"])
        assert any("not linked" in str(m) for m in messages_list)

    def it_updates_member_profile_on_post_with_profile_form_id(client: Client):
        user = User.objects.create_user(username="editor", password="pass")
        member = user.member
        client.login(username="editor", password="pass")

        response = client.post(
            "/settings/",
            {**_CONTACTS_MGMT, "form_id": "profile", "preferred_name": "Ed", "phone": "555-1234"},
            follow=True,
        )

        assert response.status_code == 200
        member.refresh_from_db()
        assert member.preferred_name == "Ed"
        assert member.phone == "555-1234"
        assert any("updated" in str(m) for m in response.context["messages"])

    def it_logs_site_activity_on_successful_profile_update(client: Client):
        from core.models import SiteActivity

        user = User.objects.create_user(username="activityeditor", password="pass")
        member = user.member
        client.login(username="activityeditor", password="pass")

        client.post(
            "/settings/",
            {**_CONTACTS_MGMT, "form_id": "profile", "preferred_name": "Ed", "phone": "555-1234"},
            follow=True,
        )

        assert SiteActivity.objects.filter(
            kind=SiteActivity.Kind.PROFILE_UPDATED,
            actor=user,
            target_id=member.pk,
        ).exists()

    def it_strips_whitespace_from_post_data(client: Client):
        user = User.objects.create_user(username="stripper", password="pass")
        member = user.member
        client.login(username="stripper", password="pass")

        client.post(
            "/settings/",
            {**_CONTACTS_MGMT, "form_id": "profile", "preferred_name": "  Trimmed  ", "phone": "  555-0000  "},
        )

        member.refresh_from_db()
        assert member.preferred_name == "Trimmed"
        assert member.phone == "555-0000"

    def it_rejects_phone_exceeding_max_length(client: Client):
        User.objects.create_user(username="longphone", password="pass")
        client.login(username="longphone", password="pass")

        response = client.post(
            "/settings/",
            {"form_id": "profile", "preferred_name": "Ok", "phone": "x" * 21},
        )

        assert response.status_code == 200
        assert response.context["profile_form"].errors

    def it_saves_text_and_visibility_when_the_uploaded_photo_is_invalid(client: Client):
        """Regression: an invalid/oversized photo must not discard the member's other edits."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = User.objects.create_user(username="badphoto", password="pass")
        member = user.member
        client.login(username="badphoto", password="pass")
        bogus = SimpleUploadedFile("notreally.png", b"this is definitely not an image", content_type="image/png")

        response = client.post(
            "/settings/",
            {
                **_CONTACTS_MGMT,
                "form_id": "profile",
                "preferred_name": "Ed",
                "about_me": "my new bio",
                "discord_handle": "eddy",
                "show_about_me": "on",
                "profile_photo": bogus,
            },
            follow=True,
        )

        assert response.status_code == 200
        member.refresh_from_db()
        assert member.preferred_name == "Ed"
        assert member.about_me == "my new bio"
        assert member.discord_handle == "eddy"
        assert member.is_public("about_me") is True
        assert member.is_public("phone") is False  # unchecked toggle still applied
        assert not member.profile_photo  # rejected upload never written
        assert any("photo" in str(m).lower() for m in response.context["messages"])

    def it_saves_pronouns(client: Client):
        user = User.objects.create_user(username="pronounuser", password="pass")
        member = user.member
        client.login(username="pronounuser", password="pass")

        client.post(
            "/settings/",
            {
                **_CONTACTS_MGMT,
                "form_id": "profile",
                "preferred_name": "",
                "pronouns": "she/her",
                "phone": "",
                "discord_handle": "",
                "about_me": "",
                "show_in_directory": False,
            },
        )

        member.refresh_from_db()
        assert member.pronouns == "she/her"

    def it_errors_and_redirects_when_profile_post_has_no_member(client: Client):
        user = User.objects.create_user(username="profilenolink", password="pass")
        client.login(username="profilenolink", password="pass")
        Member.objects.filter(user=user).delete()

        response = client.post("/settings/", {"form_id": "profile", "preferred_name": "X"}, follow=True)

        assert response.status_code == 200
        assert any("not linked" in str(m) for m in response.context["messages"])

    def it_handles_notifications_post_and_redirects_to_notifications_tab(client: Client):
        User.objects.create_user(username="notifposter", password="pass")
        client.login(username="notifposter", password="pass")

        response = client.post("/settings/", {"form_id": "notifications"})

        assert response.status_code == 302
        assert "tab=notifications" in response.url

    def it_shows_success_message_on_notifications_post(client: Client):
        User.objects.create_user(username="notifmsg", password="pass")
        client.login(username="notifmsg", password="pass")

        response = client.post("/settings/", {"form_id": "notifications"}, follow=True)

        assert any("preferences updated" in str(m).lower() for m in response.context["messages"])

    def it_seeds_primary_verified_state_from_primary_email(client: Client):
        from allauth.account.models import EmailAddress

        user = User.objects.create_user(username="primaryverified", email="v@example.com", password="pass")
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(user=user, email="v@example.com", verified=True, primary=True)
        client.login(username="primaryverified", password="pass")

        response = client.get("/settings/?tab=emails")

        assert response.context["primary_verified_json"] == "true"

    def it_flags_unverified_primary_so_resend_button_shows(client: Client):
        from allauth.account.models import EmailAddress

        user = User.objects.create_user(username="unverifiedprimary", email="u@example.com", password="pass")
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(user=user, email="u@example.com", verified=False, primary=True)
        client.login(username="unverifiedprimary", password="pass")

        response = client.get("/settings/?tab=emails")

        assert response.context["primary_verified_json"] == "false"

    def it_lists_user_email_addresses_in_context(client: Client):
        from allauth.account.models import EmailAddress

        user = User.objects.create_user(username="emaillist", email="primary@example.com", password="pass")
        # The signup signal may auto-create an EmailAddress row; clear to start deterministic.
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(user=user, email="primary@example.com", verified=True, primary=True)
        EmailAddress.objects.create(user=user, email="alias@example.com", verified=True, primary=False)
        client.login(username="emaillist", password="pass")

        response = client.get("/settings/?tab=emails")

        addrs = list(response.context["email_addresses"])
        assert {a.email for a in addrs} == {"primary@example.com", "alias@example.com"}

    def it_saves_a_new_contact_via_the_profile_form(client: Client):
        user = User.objects.create_user(username="contactsaver", password="pass")
        member = user.member
        client.login(username="contactsaver", password="pass")

        client.post(
            "/settings/",
            {
                "form_id": "profile",
                "contacts-TOTAL_FORMS": "1",
                "contacts-INITIAL_FORMS": "0",
                "contacts-0-label": "Website",
                "contacts-0-value": "https://maker.example",
                "contacts-0-show_in_directory": "on",
                "contacts-0-sort_order": "0",
            },
        )

        contact = member.contacts.get()
        assert contact.label == "Website"
        assert contact.value == "https://maker.example"
        assert contact.show_in_directory is True

    def it_deletes_a_contact_when_the_profile_form_flags_the_row(client: Client):
        user = User.objects.create_user(username="contactdeleter", password="pass")
        member = user.member
        contact = MemberContactFactory(member=member, label="Old", value="https://old.example")
        client.login(username="contactdeleter", password="pass")

        client.post(
            "/settings/",
            {
                "form_id": "profile",
                "contacts-TOTAL_FORMS": "1",
                "contacts-INITIAL_FORMS": "1",
                "contacts-0-id": str(contact.pk),
                "contacts-0-label": "Old",
                "contacts-0-value": "https://old.example",
                "contacts-0-show_in_directory": "on",
                "contacts-0-sort_order": "0",
                "contacts-0-DELETE": "on",
            },
        )

        assert member.contacts.count() == 0

    def it_saves_instructor_bio_for_an_instructor(client: Client):
        user = User.objects.create_user(username="teacherbio", password="pass")
        member = user.member
        member.instructor_slug = "teacher-bio"
        member.save(update_fields=["instructor_slug"])
        client.login(username="teacherbio", password="pass")

        client.post(
            "/settings/",
            {**_CONTACTS_MGMT, "form_id": "profile", "instructor_bio": "I teach welding."},
        )

        member.refresh_from_db()
        assert member.instructor_bio == "I teach welding."

    def it_hides_the_instructor_subtab_for_non_instructors(client: Client):
        User.objects.create_user(username="plainmember", password="pass")
        client.login(username="plainmember", password="pass")

        html = client.get("/settings/").content.decode()

        assert "ptab = 'instructor'" not in html

    def it_shows_the_instructor_subtab_for_instructors(client: Client):
        user = User.objects.create_user(username="subtabteacher", password="pass")
        member = user.member
        member.instructor_slug = "subtab-teacher"
        member.save(update_fields=["instructor_slug"])
        client.login(username="subtabteacher", password="pass")

        html = client.get("/settings/").content.decode()

        assert "ptab = 'instructor'" in html
        assert "About me as an instructor" in html


@pytest.mark.django_db
def describe_legacy_settings_redirects():
    def it_redirects_old_profile_path_to_user_settings(client: Client):
        User.objects.create_user(username="legacyprofile", password="pass")
        client.login(username="legacyprofile", password="pass")

        response = client.get("/settings/profile/")

        assert response.status_code == 302
        assert response.url == "/settings/"

    def it_redirects_allauth_account_email_get_to_emails_tab(client: Client):
        User.objects.create_user(username="legacyallauth", password="pass")
        client.login(username="legacyallauth", password="pass")

        response = client.get("/accounts/email/")

        assert response.status_code == 302
        assert response.url == "/settings/?tab=emails"

    def it_sends_email_action_post_back_to_emails_tab(client: Client):
        """After allauth's EmailView handles add/remove/resend/primary, the user
        should land on the Emails tab — not the Profile tab."""
        from allauth.account.models import EmailAddress

        user = User.objects.create_user(username="emailaction", email="me@example.com", password="pass")
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(user=user, email="me@example.com", verified=True, primary=True)
        client.login(username="emailaction", password="pass")

        response = client.post("/accounts/email/", {"action_add": "", "email": "alias@example.com"})

        assert response.status_code == 302
        assert response.url == "/settings/?tab=emails"


_PROFILE_PHOTO_DELETE_URL = "/settings/profile-photo/delete/"


def _tiny_png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
        b"\xc0\x00\x00\x00\x03\x00\x01\x5b\x0d\xc1\x6a\x00\x00\x00\x00IEND\xae"
        b"B`\x82"
    )


@pytest.mark.django_db
def describe_profile_photo_delete():
    """Tests for the POST-only profile photo clearing endpoint."""

    def it_requires_login(client: Client):
        response = client.post(_PROFILE_PHOTO_DELETE_URL)

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_rejects_non_POST_requests(client: Client):
        User.objects.create_user(username="gets", password="pass")
        client.login(username="gets", password="pass")

        response = client.get(_PROFILE_PHOTO_DELETE_URL)

        assert response.status_code == 405

    def it_errors_when_user_has_no_linked_member(client: Client):
        user = User.objects.create_user(username="unlinked", password="pass")
        Member.objects.filter(user=user).delete()
        # Refresh so the cached .member attribute on user is cleared.
        User.objects.get(pk=user.pk)
        client.login(username="unlinked", password="pass")

        response = client.post(_PROFILE_PHOTO_DELETE_URL, follow=True)

        assert response.status_code == 200
        msgs = [str(m) for m in response.context["messages"]]
        assert any("not linked" in m for m in msgs)

    def it_clears_the_profile_photo_and_redirects_to_profile_tab(client: Client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = User.objects.create_user(username="hasphoto", password="pass")
        member = user.member
        member.profile_photo = SimpleUploadedFile("me.png", _tiny_png_bytes(), content_type="image/png")
        member.save()
        assert member.profile_photo
        client.login(username="hasphoto", password="pass")

        response = client.post(_PROFILE_PHOTO_DELETE_URL)

        assert response.status_code == 302
        assert response.url.endswith("/settings/?tab=profile")
        member.refresh_from_db()
        assert not member.profile_photo

    def it_preserves_other_profile_fields_when_deleting_the_photo(client: Client):
        """Regression: clearing the photo must not revert the member's saved fields."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = User.objects.create_user(username="delkeep", password="pass")
        member = user.member
        member.preferred_name = "Keeper"
        member.about_me = "still here"
        member.directory_visibility = {"phone": False}
        member.profile_photo = SimpleUploadedFile("me.png", _tiny_png_bytes(), content_type="image/png")
        member.save()
        client.login(username="delkeep", password="pass")

        client.post(_PROFILE_PHOTO_DELETE_URL)

        member.refresh_from_db()
        assert not member.profile_photo
        assert member.preferred_name == "Keeper"
        assert member.about_me == "still here"
        assert member.is_public("phone") is False

    def it_is_a_noop_when_no_photo_is_set(client: Client):
        User.objects.create_user(username="nophoto", password="pass")
        client.login(username="nophoto", password="pass")

        response = client.post(_PROFILE_PHOTO_DELETE_URL)

        assert response.status_code == 302
        assert response.url.endswith("/settings/?tab=profile")


@pytest.mark.django_db
def describe_welcome_modal_context():
    """The first-login welcome nudge flag from _get_hub_context."""

    def it_shows_for_a_fresh_member(client: Client):
        User.objects.create_user(username="freshie", password="pass")
        client.login(username="freshie", password="pass")

        response = client.get("/guilds/voting/")

        assert response.context["show_welcome_modal"] is True

    def it_does_not_show_once_dismissed(client: Client):
        user = User.objects.create_user(username="dismissed", password="pass")
        user.member.dismiss_welcome()
        client.login(username="dismissed", password="pass")

        response = client.get("/guilds/voting/")

        assert response.context["show_welcome_modal"] is False

    def it_does_not_show_when_the_profile_is_already_started(client: Client):
        user = User.objects.create_user(username="hasprofile", password="pass")
        member = user.member
        member.about_me = "Longtime member."
        member.save()
        client.login(username="hasprofile", password="pass")

        response = client.get("/guilds/voting/")

        assert response.context["show_welcome_modal"] is False

    def it_does_not_show_for_a_user_without_a_member(client: Client):
        user = User.objects.create_user(username="nomemberw", password="pass")
        Member.objects.filter(user=user).delete()
        User.objects.get(pk=user.pk)  # clear cached .member
        client.login(username="nomemberw", password="pass")

        response = client.get("/guilds/voting/")

        assert response.context["show_welcome_modal"] is False


@pytest.mark.django_db
def describe_welcome_dismiss():
    def it_requires_login(client: Client):
        response = client.post("/welcome/dismiss/")

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def it_rejects_non_POST_requests(client: Client):
        User.objects.create_user(username="wgets", password="pass")
        client.login(username="wgets", password="pass")

        response = client.get("/welcome/dismiss/")

        assert response.status_code == 405

    def it_stamps_and_redirects_to_the_profile_tab(client: Client):
        user = User.objects.create_user(username="setup", password="pass")
        client.login(username="setup", password="pass")

        response = client.post("/welcome/dismiss/", {"destination": "profile"})

        assert response.status_code == 302
        assert response.url.endswith("/settings/?tab=profile")
        user.member.refresh_from_db()
        assert user.member.welcome_dismissed_at is not None

    def it_stamps_and_returns_to_next_for_maybe_later(client: Client):
        user = User.objects.create_user(username="later", password="pass")
        client.login(username="later", password="pass")

        response = client.post("/welcome/dismiss/", {"next": "/guilds/voting/"})

        assert response.status_code == 302
        assert response.url == "/guilds/voting/"
        user.member.refresh_from_db()
        assert user.member.welcome_dismissed_at is not None

    def it_ignores_an_unsafe_next_and_falls_back_to_the_calendar(client: Client):
        User.objects.create_user(username="unsafe", password="pass")
        client.login(username="unsafe", password="pass")

        response = client.post("/welcome/dismiss/", {"next": "https://evil.example.com/x"})

        assert response.status_code == 302
        assert response.url == "/calendar/"

    def it_is_a_noop_but_still_redirects_when_the_user_has_no_member(client: Client):
        user = User.objects.create_user(username="dismnomember", password="pass")
        Member.objects.filter(user=user).delete()
        User.objects.get(pk=user.pk)  # clear cached .member
        client.login(username="dismnomember", password="pass")

        response = client.post("/welcome/dismiss/", {"destination": "profile"})

        assert response.status_code == 302
        assert response.url.endswith("/settings/?tab=profile")
