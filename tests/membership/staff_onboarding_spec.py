"""BDD specs for welcoming a new guild lead or staffer to Guild Staff Tools (#1076).

A grant through ``Guild.assign_lead`` or ``Guild.add_staff`` logs a ``GRANTED`` step and, after
it commits, emails the member their tools and the rules; a second title on the same guild, the same
lead again, or a staffer promoted to lead sends nothing new. ``Member.staff_onboarding_step`` reads
the log: rules until confirmed, then the onboarding tour until taken or exited, then nothing.

Names are factory strings no changelog could contain (STANDARDS.md section 8).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail

from membership import staff_onboarding
from membership.models import Guild, GuildStaffMembership, GuildStaffOnboardingEvent, Member
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

Action = GuildStaffOnboardingEvent.Action
Step = Member.StaffOnboardingStep


def _member(username: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Brackenmoor"
    member.save(update_fields=["status", "full_legal_name"])
    return member


@pytest.fixture
def guild() -> Guild:
    return GuildFactory(name="Thistledown Guild")


def _grants(member: Member) -> list[GuildStaffOnboardingEvent]:
    return list(member.staff_onboarding_events.filter(action=Action.GRANTED))


def describe_a_new_lead():
    def it_logs_the_grant_and_emails_the_tools_and_rules_after_commit(guild: Guild, django_capture_on_commit_callbacks):
        member = _member("quillon")
        with django_capture_on_commit_callbacks(execute=True):
            guild.assign_lead(member)

        assert [g.guild for g in _grants(member)] == [guild]
        [email] = mail.outbox
        assert email.to == ["quillon@example.com"]
        assert email.subject == "Welcome to Guild Staff Tools for Thistledown Guild"
        assert "orientations, reservations, equipment and email lists all run through the Member Portal" in email.body
        html = email.alternatives[0][0]
        assert "You now have Guild Staff Tools for Thistledown Guild" in html
        for name, _purpose, path in staff_onboarding.tool_links(guild):
            assert name in html
            assert path in html

    def it_sends_nothing_until_the_grant_commits(guild: Guild, django_capture_on_commit_callbacks):
        member = _member("quillon")
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            guild.assign_lead(member)
        assert mail.outbox == []
        assert len(callbacks) == 1

    def it_emails_a_lead_who_has_no_account_yet_at_their_staged_address(
        guild: Guild, django_capture_on_commit_callbacks
    ):
        member = MemberFactory(_pre_signup_email="unlinked-lead@example.com")
        with django_capture_on_commit_callbacks(execute=True):
            guild.assign_lead(member)
        assert [e.to for e in mail.outbox] == [["unlinked-lead@example.com"]]

    def it_sends_nothing_for_the_same_lead_again(guild: Guild, django_capture_on_commit_callbacks):
        member = _member("quillon")
        with django_capture_on_commit_callbacks(execute=True):
            guild.assign_lead(member)
            guild.assign_lead(member)
        assert len(_grants(member)) == 1
        assert len(mail.outbox) == 1

    def it_sends_nothing_when_a_staffer_is_promoted_to_lead(guild: Guild, django_capture_on_commit_callbacks):
        member = _member("quillon")
        with django_capture_on_commit_callbacks(execute=True):
            guild.add_staff(member, role=GuildStaffMembership.Role.TREASURER)
            guild.assign_lead(member)
        assert len(_grants(member)) == 1


def describe_a_new_staffer():
    def it_logs_their_first_role_on_the_guild_and_emails_them(guild: Guild, django_capture_on_commit_callbacks):
        member = _member("marrow")
        with django_capture_on_commit_callbacks(execute=True):
            guild.add_staff(member, role=GuildStaffMembership.Role.SECRETARY)
        assert [g.guild for g in _grants(member)] == [guild]
        assert [e.to for e in mail.outbox] == [["marrow@example.com"]]

    def it_sends_nothing_for_a_second_title_on_the_same_guild(guild: Guild, django_capture_on_commit_callbacks):
        member = _member("marrow")
        with django_capture_on_commit_callbacks(execute=True):
            guild.add_staff(member, role=GuildStaffMembership.Role.SECRETARY)
            guild.add_staff(member, role=GuildStaffMembership.Role.TREASURER)
        assert len(_grants(member)) == 1
        assert len(mail.outbox) == 1

    def it_welcomes_them_again_on_another_guild(guild: Guild, django_capture_on_commit_callbacks):
        member = _member("marrow")
        other = GuildFactory(name="Saltmarsh Guild")
        with django_capture_on_commit_callbacks(execute=True):
            guild.add_staff(member, role=GuildStaffMembership.Role.SECRETARY)
            other.add_staff(member, role=GuildStaffMembership.Role.ORIENTER)
        assert {g.guild for g in _grants(member)} == {guild, other}
        assert len(mail.outbox) == 2

    def it_welcomes_a_class_reviewer_added_on_their_own(guild: Guild, django_capture_on_commit_callbacks):
        member = _member("marrow")
        with django_capture_on_commit_callbacks(execute=True):
            guild.add_staff(member, class_reviewer=True)
        assert len(_grants(member)) == 1


def describe_send_welcome():
    def it_refuses_a_step_that_is_not_a_grant_on_a_guild():
        member = _member("orrin")
        step = GuildStaffOnboardingEvent.objects.create(member=member, action=Action.CONFIRMED)
        with pytest.raises(ValueError, match="no guild"):
            staff_onboarding.send_welcome(step)


def describe_rules_page_path():
    def it_points_at_the_wiki_home_until_the_rules_page_exists():
        assert staff_onboarding.rules_page_path() == "/wiki/"

    def it_points_at_the_rules_page_once_seeded():
        from membership.models import WikiPage

        page = WikiPage.objects.create_page(
            title=staff_onboarding.RULES_PAGE_TITLE, kind=WikiPage.Kind.REFERENCE, author=None
        )
        assert staff_onboarding.rules_page_path() == f"/wiki/p/{page.slug}/"


def describe_staff_onboarding_step():
    def it_is_nothing_for_someone_never_granted():
        assert _member("orrin").staff_onboarding_step() is None

    def it_asks_for_the_rules_then_the_tour_then_nothing(guild: Guild):
        member = _member("orrin")
        guild.assign_lead(member)
        assert member.staff_onboarding_step() == Step.RULES

        GuildStaffOnboardingEvent.objects.create(member=member, action=Action.CONFIRMED)
        assert member.staff_onboarding_step() == Step.TOUR

        GuildStaffOnboardingEvent.objects.create(
            member=member, action=Action.TOUR_EXITED, tour_key=staff_onboarding.ONBOARDING_TOUR
        )
        assert member.staff_onboarding_step() is None

    def it_keeps_offering_the_onboarding_tour_after_another_walkthrough(guild: Guild):
        member = _member("orrin")
        guild.assign_lead(member)
        GuildStaffOnboardingEvent.objects.create(member=member, action=Action.CONFIRMED)
        GuildStaffOnboardingEvent.objects.create(member=member, action=Action.TOUR_TAKEN, tour_key="member-welcome")
        assert member.staff_onboarding_step() == Step.TOUR

    def it_is_nothing_once_they_no_longer_lead_or_staff_a_guild(guild: Guild):
        member = _member("orrin")
        guild.assign_lead(member)
        guild.clear_lead()
        assert member.staff_onboarding_step() is None
