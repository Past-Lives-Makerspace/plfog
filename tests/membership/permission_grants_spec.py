"""BDD specs for the "You have been granted" email (#1163).

Turning on an admin capability, Instructor, the Admin role or a guild's Class Reviewer for
someone else rings their bell and emails them, once the save commits, what each permission lets
them do, how, and a button per page, naming who turned it on. Each permission sends its own
email, even two in one save; a grant to yourself sends nothing; the task is safe to run twice;
every send is an Email Log row.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

from contextlib import suppress
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse

from core.email_log_filters import type_label
from core.models import Notification, TransactionalEmailLog
from membership import permission_grants
from membership.models import AdminCapability, GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

Cap = AdminCapability.Capability
PASSWORD = "pw12345!"


def _person(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password=PASSWORD)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.fog_role = fog_role
    member.full_legal_name = f"{username.title()} Wrenfield"
    member.save(update_fields=["status", "fog_role", "full_legal_name"])
    member.sync_user_permissions()
    return member


@pytest.fixture
def granter() -> Member:
    return _person("osric", fog_role=Member.FogRole.ADMIN)


@pytest.fixture
def pell() -> Member:
    return _person("pell")


@pytest.fixture
def admin_client(client: Client, granter: Member) -> Client:
    client.login(username="osric", password=PASSWORD)
    return client


def _granted_emails() -> list:
    return [e for e in mail.outbox if e.subject.startswith("You have been granted")]


def _html(email) -> str:
    return email.alternatives[0][0]


def describe_admin_capabilities():
    def it_sends_each_capability_one_save_turned_on_its_own_email(
        granter: Member, pell: Member, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            pell.sync_admin_capabilities([Cap.REFUNDS, Cap.BILLING_APPROVER], granted_by=granter.user)
        emails = _granted_emails()
        assert [e.to for e in emails] == [["pell@example.com"], ["pell@example.com"]]
        assert sorted(e.subject for e in emails) == [
            "You have been granted Billing Administrator",
            "You have been granted Refunds",
        ]
        rows = TransactionalEmailLog.objects.filter(trigger_kind=permission_grants.PERMISSION_GRANTED_EVENT)
        assert rows.count() == 2
        bells = Notification.objects.filter(user=pell.user, trigger=permission_grants.PERMISSION_GRANTED_EVENT)
        assert bells.count() == 2

    def it_emails_only_what_is_new(granter: Member, pell: Member, django_capture_on_commit_callbacks):
        pell.admin_capabilities.create(capability=Cap.REFUNDS)
        with django_capture_on_commit_callbacks(execute=True):
            pell.sync_admin_capabilities([Cap.REFUNDS, Cap.TOUR_GUIDE], granted_by=granter.user)
        [email] = _granted_emails()
        assert email.subject == "You have been granted Tour Guide"

    def it_sends_nothing_for_a_save_that_turns_nothing_on(
        granter: Member, pell: Member, django_capture_on_commit_callbacks
    ):
        pell.admin_capabilities.create(capability=Cap.REFUNDS)
        with django_capture_on_commit_callbacks(execute=True):
            pell.sync_admin_capabilities([], granted_by=granter.user)
        assert _granted_emails() == []

    def it_sends_nothing_for_a_grant_to_yourself(granter: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            granter.sync_admin_capabilities([Cap.WEBMASTER], granted_by=granter.user)
            granter.set_admin_capability(Cap.REFUNDS, True, granted_by=granter.user)
        assert _granted_emails() == []

    def it_emails_a_single_toggle_turned_on_by_someone_else_once(
        granter: Member, pell: Member, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            pell.set_admin_capability(Cap.WEBMASTER, True, granted_by=granter.user)
            pell.set_admin_capability(Cap.WEBMASTER, True, granted_by=granter.user)
        assert [e.subject for e in _granted_emails()] == ["You have been granted Webmaster"]

    def it_emails_from_the_permissions_tab(admin_client: Client, pell: Member, django_capture_on_commit_callbacks):
        url = reverse("hub_admin_member_edit", args=[pell.pk])
        with django_capture_on_commit_callbacks(execute=True):
            admin_client.post(url, {"form_id": "capabilities", "cap_space_approver": "on"})
        [email] = _granted_emails()
        assert email.subject == "You have been granted Space & Cubby Administrator"


def describe_instructor():
    def it_emails_a_member_who_never_applied(granter: Member, pell: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            pell.grant_instructor(granted_by=granter)
        [email] = _granted_emails()
        assert email.subject == "You have been granted Instructor"
        assert reverse("classes:teach_overview") in _html(email)

    def it_leaves_an_answered_application_to_its_own_approval_email(
        granter: Member, pell: Member, django_capture_on_commit_callbacks
    ):
        from django.utils import timezone

        pell.teaching_applied_at = timezone.now()
        pell.save(update_fields=["teaching_applied_at"])
        with django_capture_on_commit_callbacks(execute=True):
            pell.grant_instructor(granted_by=granter)
        assert _granted_emails() == []

    def it_sends_nothing_to_an_instructor_already_on_or_to_yourself(
        granter: Member, pell: Member, django_capture_on_commit_callbacks
    ):
        pell.grant_instructor(granted_by=granter)
        with django_capture_on_commit_callbacks(execute=True):
            pell.grant_instructor(granted_by=granter)
            granter.grant_instructor(granted_by=granter)
        assert _granted_emails() == []

    def it_emails_from_the_instructor_toggle(admin_client: Client, pell: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            admin_client.post(reverse("hub_admin_member_teaching", args=[pell.pk]), {"action": "grant"})
        assert [e.to for e in _granted_emails()] == [["pell@example.com"]]


def describe_the_admin_role():
    def it_emails_a_member_made_admin(granter: Member, pell: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            pell.apply_admin_role(Member.FogRole.ADMIN, granted_by=granter.user)
        [email] = _granted_emails()
        assert email.subject == "You have been granted Admin"
        assert reverse("hub_admin_tools") in _html(email)

    def it_sends_nothing_when_they_were_already_admin(granter: Member, django_capture_on_commit_callbacks):
        other = _person("thessaly", fog_role=Member.FogRole.ADMIN)
        with django_capture_on_commit_callbacks(execute=True):
            other.apply_admin_role(Member.FogRole.ADMIN, granted_by=granter.user)
            other.apply_admin_role(Member.FogRole.MEMBER, granted_by=granter.user)
        assert _granted_emails() == []

    def it_emails_from_the_details_tab(admin_client: Client, pell: Member, django_capture_on_commit_callbacks):
        data = {
            "full_legal_name": pell.full_legal_name,
            "status": Member.Status.ACTIVE,
            "member_type": Member.MemberType.STANDARD,
            "role": Member.FogRole.ADMIN,
            "roles-TOTAL_FORMS": "0",
            "roles-INITIAL_FORMS": "0",
            "roles-MIN_NUM_FORMS": "0",
            "roles-MAX_NUM_FORMS": "1000",
        }
        with django_capture_on_commit_callbacks(execute=True):
            response = admin_client.post(reverse("hub_admin_member_edit", args=[pell.pk]), data)
        assert response.status_code == 302
        assert [e.subject for e in _granted_emails()] == ["You have been granted Admin"]


def describe_class_reviewer():
    def it_emails_a_staffer_switched_on_naming_the_guild(
        admin_client: Client, pell: Member, django_capture_on_commit_callbacks
    ):
        guild = GuildFactory(name="Larkspur Bindery Guild")
        GuildStaffMembershipFactory(guild=guild, member=pell, role=GuildStaffMembership.Role.SECRETARY)
        url = reverse("hub_guild_staff_class_reviewer", args=[guild.pk, pell.pk])
        with django_capture_on_commit_callbacks(execute=True):
            admin_client.post(url, {"on": "on"})
            admin_client.post(url, {"on": "on"})
            admin_client.post(url, {})
        [email] = _granted_emails()
        assert email.subject == "You have been granted Class Reviewer for Larkspur Bindery Guild"

    def it_emails_a_class_reviewer_added_from_guild_roles(
        admin_client: Client, pell: Member, django_capture_on_commit_callbacks
    ):
        guild = GuildFactory(name="Larkspur Bindery Guild")
        url = reverse("hub_admin_member_guild_staff_add", args=[pell.pk])
        with django_capture_on_commit_callbacks(execute=True):
            admin_client.post(url, {"guild": guild.pk, "role": "", "custom_title": "", "class_reviewer": "on"})
        assert [e.subject for e in _granted_emails()] == [
            "You have been granted Class Reviewer for Larkspur Bindery Guild"
        ]

    def it_emails_a_new_staffer_added_with_the_switch_on_from_the_staff_tab(
        admin_client: Client, pell: Member, django_capture_on_commit_callbacks
    ):
        guild = GuildFactory(name="Larkspur Bindery Guild")
        url = reverse("hub_guild_staff_add", args=[guild.pk])
        data = {
            "member": pell.pk,
            "role": GuildStaffMembership.Role.TREASURER,
            "custom_title": "",
            "class_reviewer": "on",
        }
        with django_capture_on_commit_callbacks(execute=True):
            admin_client.post(url, data)
        assert len(_granted_emails()) == 1

    def it_sends_nothing_for_a_staffer_added_without_the_switch(
        granter: Member, pell: Member, django_capture_on_commit_callbacks
    ):
        guild = GuildFactory(name="Larkspur Bindery Guild")
        with django_capture_on_commit_callbacks(execute=True):
            guild.add_staff(pell, role=GuildStaffMembership.Role.TREASURER, granted_by=granter.user)
        assert _granted_emails() == []


def describe_the_email():
    def it_says_what_how_and_links_each_page_and_who_to_ask(
        granter: Member, pell: Member, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            pell.sync_admin_capabilities([Cap.EQUIPMENT], granted_by=granter.user)
        [email] = _granted_emails()
        html, text = _html(email), email.body
        guide = AdminCapability.GUIDES[Cap.EQUIPMENT]
        base = settings.MEMBER_BASE_URL.rstrip("/")
        for body in (html, text):
            assert "You have been granted Equipment Administrator" in body
            assert guide.what in body
            assert guide.how in body
            assert "Osric Wrenfield turned this on for you" in body
            assert "If you need any help, don't hesitate to ask Osric Wrenfield." in body.replace("&#x27;", "'")
            for page in guide.pages:
                assert f"{base}{reverse(page.url_name)}" in body
        assert html.count("background: #EEB44B") == len(guide.pages)

    def it_lists_the_get_set_up_steps_and_one_button_for_tour_guide(
        granter: Member, pell: Member, django_capture_on_commit_callbacks
    ):
        from tours.setup import TOUR_GUIDE_SETUP

        with django_capture_on_commit_callbacks(execute=True):
            pell.sync_admin_capabilities([Cap.TOUR_GUIDE], granted_by=granter.user)
        [email] = _granted_emails()
        html, text = _html(email), email.body.replace("&#x27;", "'")
        setup_url = f"{settings.MEMBER_BASE_URL.rstrip('/')}{reverse('tours:upcoming')}"
        guide = AdminCapability.GUIDES[Cap.TOUR_GUIDE]
        for body in (html.replace("&#x27;", "'"), text):
            assert "You have been granted Tour Guide" in body
            assert guide.what in body
            assert guide.how not in body
            for step in TOUR_GUIDE_SETUP.steps[1:]:
                assert step.email_line in body
            # Google sign in is not set up here, so the page shows the calendar step unavailable
            # and the email leaves it out.
            assert TOUR_GUIDE_SETUP.steps[0].email_line not in body
            assert "the Tours page" in body
            assert "You're paid $50 when a visitor you toured becomes a member." in body
            assert setup_url in body
        assert "Get Set Up: " + setup_url in text
        assert html.count("background: #EEB44B") == 1
        assert ">Get Set Up</a>" in html
        bell = Notification.objects.get(user=pell.user, trigger=permission_grants.PERMISSION_GRANTED_EVENT)
        assert bell.body == "Get set up in a few short steps."
        assert bell.url.endswith(reverse("tours:upcoming"))

    def it_lists_the_calendar_step_once_google_sign_in_is_set_up(
        granter: Member, pell: Member, django_capture_on_commit_callbacks, settings
    ):
        from tours.setup import TOUR_GUIDE_SETUP

        settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
        settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"
        with django_capture_on_commit_callbacks(execute=True):
            pell.sync_admin_capabilities([Cap.TOUR_GUIDE], granted_by=granter.user)
        [email] = _granted_emails()
        assert "1. Connect your Google Calendar (optional)" in email.body
        assert [step.key for step in TOUR_GUIDE_SETUP.email_steps(pell)] == [s.key for s in TOUR_GUIDE_SETUP.steps]

    def it_names_an_admin_when_the_granter_has_no_member_record(pell: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            pell.grant_instructor(granted_by=None)
        [email] = _granted_emails()
        assert "An admin turned this on for you" in email.body
        assert "don't hesitate to ask an admin." in email.body

    def it_rings_the_bell_with_the_same_title(granter: Member, pell: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            pell.sync_admin_capabilities([Cap.EQUIPMENT], granted_by=granter.user)
        bell = Notification.objects.get(user=pell.user, trigger=permission_grants.PERMISSION_GRANTED_EVENT)
        assert bell.title == "You have been granted Equipment Administrator"

    def it_emails_a_member_with_no_account_at_their_staged_address(granter: Member, django_capture_on_commit_callbacks):
        unlinked = MemberFactory(_pre_signup_email="unlinked-wren@example.com", full_legal_name="Unlinked Wren")
        with django_capture_on_commit_callbacks(execute=True):
            unlinked.sync_admin_capabilities([Cap.REFUNDS], granted_by=granter.user)
        assert [e.to for e in _granted_emails()] == [["unlinked-wren@example.com"]]

    def it_has_a_guide_for_every_capability():
        assert set(Cap.values) <= set(AdminCapability.GUIDES)
        for key, guide in AdminCapability.GUIDES.items():
            assert guide.pages, key
            if key in Cap.values:
                assert guide.what == AdminCapability.DESCRIPTIONS[key]
            for page in guide.pages:
                reverse(page.url_name)


def describe_the_queue():
    def it_sends_nothing_until_the_save_commits(granter: Member, pell: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            pell.sync_admin_capabilities([Cap.REFUNDS, Cap.WEBMASTER], granted_by=granter.user)
        assert _granted_emails() == []
        assert len(callbacks) == 2  # one task per permission

    def it_is_safe_to_run_twice(granter: Member, pell: Member):
        args = (pell.pk, Cap.REFUNDS, granter.user.pk, None, "batch-wren-1")
        permission_grants.send_permission_granted.call(*args)
        permission_grants.send_permission_granted.call(*args)
        assert len(_granted_emails()) == 1
        assert (
            Notification.objects.filter(user=pell.user, trigger=permission_grants.PERMISSION_GRANTED_EVENT).count() == 1
        )

    def it_refuses_a_grant_with_no_guide_at_the_save(granter: Member, pell: Member):
        with pytest.raises(KeyError):
            permission_grants.record(pell, ["not_a_permission"], granted_by=granter.user)


def describe_the_email_log():
    def it_logs_each_send_under_its_own_type(granter: Member, pell: Member, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            pell.sync_admin_capabilities([Cap.REFUNDS], granted_by=granter.user)
        log = TransactionalEmailLog.objects.get(trigger_kind=permission_grants.PERMISSION_GRANTED_EVENT)
        assert (log.to_email, log.subject, log.status) == (
            "pell@example.com",
            "You have been granted Refunds",
            TransactionalEmailLog.Status.SENT,
        )
        assert type_label(log.trigger_kind) == "Permission granted"

    def it_logs_a_failed_send_with_its_error(granter: Member, pell: Member):
        with patch("core.email._deliver", side_effect=RuntimeError("relay refused wren")):
            with suppress(RuntimeError):
                permission_grants.send_permission_granted.call(pell.pk, Cap.REFUNDS, granter.user.pk, None, "b2")
        log = TransactionalEmailLog.objects.get(trigger_kind=permission_grants.PERMISSION_GRANTED_EVENT)
        assert log.status == TransactionalEmailLog.Status.FAILED
        assert "relay refused wren" in log.error_message
