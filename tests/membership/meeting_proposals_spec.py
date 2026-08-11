"""BDD specs for agenda-item proposals: the approve / decline / withdraw lifecycle
(copying the CommunityEvent decision idiom) and the meeting permission helpers."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory
from django.utils import timezone

from core.models import EventDelivery
from hub.view_as import ROLE_ADMIN, ROLE_GUILD_OFFICER, ROLE_MEMBER, ViewAs
from membership.models import (
    GuildStaffMembership,
    InvalidProposalTransition,
    MeetingItemProposal,
    MeetingLockedError,
    Member,
)
from membership.permissions import can_edit_meeting, can_propose_to_meeting
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MeetingAgendaItemFactory,
    MeetingFactory,
    MeetingItemProposalFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _user(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _deliveries(event_key: str) -> int:
    return EventDelivery.objects.filter(event_key=event_key, channel="in_app").count()


def _request(user: User | AnonymousUser, *, roles: set[str] | None = None, picked: str | None = None):
    request = RequestFactory().get("/")
    request.user = user
    if roles is not None:
        request.view_as = ViewAs(actual=frozenset(roles), picked=picked)
    return request


def describe_MeetingItemProposal():
    def describe_approve():
        def it_creates_a_credited_agenda_item_from_the_proposal():
            proposer = _user("proposer")
            proposal = MeetingItemProposalFactory(title="New sander", why="Ours died.", proposed_by=proposer)
            reviewer = _user("reviewer")
            item = proposal.approve(reviewer=reviewer)
            proposal.refresh_from_db()
            assert item.name == "New sander"
            assert item.description == "Ours died."
            assert item.proposed_by == proposer
            assert proposal.state == MeetingItemProposal.State.APPROVED
            assert proposal.reviewed_by == reviewer
            assert proposal.reviewed_at is not None
            assert proposal.created_item == item
            assert item.source_proposal == proposal
            assert _deliveries("meeting.item_decided") == 1

        def it_lands_the_new_item_at_the_agenda_bottom():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p1"))
            existing = MeetingAgendaItemFactory(meeting=proposal.meeting)
            item = proposal.approve(reviewer=_user("rev1"))
            assert item.sort_order > existing.sort_order

        def it_applies_edit_then_approve_overrides():
            proposal = MeetingItemProposalFactory(title="Raw idea", why="Rough", proposed_by=_user("p2"))
            item = proposal.approve(reviewer=_user("rev2"), title="Polished topic", why="Sharpened rationale")
            assert item.name == "Polished topic"
            assert item.description == "Sharpened rationale"
            assert item.proposed_by == proposal.proposed_by  # credit survives the edit

        def it_refuses_a_non_pending_proposal():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p3"))
            proposal.approve(reviewer=_user("rev3"))
            with pytest.raises(InvalidProposalTransition):
                proposal.approve(reviewer=_user("rev4"))

        def it_refuses_a_withdrawn_proposal():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p4"))
            proposal.withdraw(by=proposal.proposed_by)
            with pytest.raises(InvalidProposalTransition):
                proposal.approve(reviewer=_user("rev5"))

        def it_refuses_when_the_meeting_is_locked():
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(approved=True), proposed_by=_user("p5"))
            with pytest.raises(MeetingLockedError):
                proposal.approve(reviewer=_user("rev6"))

    def describe_decline():
        def it_stamps_the_decision_with_an_optional_note():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p6"))
            reviewer = _user("rev7")
            proposal.decline(reviewer=reviewer, note="Covered last month.")
            proposal.refresh_from_db()
            assert proposal.state == MeetingItemProposal.State.DECLINED
            assert proposal.reviewed_by == reviewer
            assert proposal.review_note == "Covered last month."
            assert proposal.created_item is None
            assert _deliveries("meeting.item_decided") == 1

        def it_allows_a_blank_note():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p7"))
            proposal.decline(reviewer=_user("rev8"))
            proposal.refresh_from_db()
            assert proposal.state == MeetingItemProposal.State.DECLINED
            assert proposal.review_note == ""

        def it_refuses_a_double_decision():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p8"))
            proposal.decline(reviewer=_user("rev9"))
            with pytest.raises(InvalidProposalTransition):
                proposal.decline(reviewer=_user("rev10"))

        def it_refuses_a_withdrawn_proposal():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p9"))
            proposal.withdraw(by=proposal.proposed_by)
            with pytest.raises(InvalidProposalTransition):
                proposal.decline(reviewer=_user("rev11"))

        def it_refuses_when_the_meeting_is_locked():
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(approved=True), proposed_by=_user("p10"))
            with pytest.raises(MeetingLockedError):
                proposal.decline(reviewer=_user("rev12"))

    def describe_withdraw():
        def it_lets_the_proposer_pull_a_pending_proposal_silently():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p11"))
            proposal.withdraw(by=proposal.proposed_by)
            proposal.refresh_from_db()
            assert proposal.state == MeetingItemProposal.State.WITHDRAWN
            assert proposal.reviewed_by is None
            assert _deliveries("meeting.item_decided") == 0  # silent — no emit

        def it_refuses_anyone_but_the_proposer():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p12"))
            with pytest.raises(ValueError, match="Only the proposer"):
                proposal.withdraw(by=_user("someone-else"))

        def it_refuses_a_non_pending_proposal():
            proposal = MeetingItemProposalFactory(proposed_by=_user("p13"))
            proposal.decline(reviewer=_user("rev13"))
            with pytest.raises(InvalidProposalTransition):
                proposal.withdraw(by=proposal.proposed_by)

    def describe_str():
        def it_reads_title_meeting_and_state():
            proposal = MeetingItemProposalFactory(title="Dust collection", proposed_by=_user("p14"))
            assert str(proposal).startswith("Dust collection → ")
            assert str(proposal).endswith("(Pending)")


def describe_can_edit_meeting():
    def describe_guild_meetings():
        def it_allows_the_guilds_lead():
            lead = _user("lead")
            meeting = MeetingFactory(guild=GuildFactory(guild_lead=lead.member))
            assert can_edit_meeting(_request(lead, roles={ROLE_MEMBER}), meeting) is True

        def it_allows_guild_staff():
            staff = _user("staff")
            guild = GuildFactory()
            GuildStaffMembershipFactory(guild=guild, member=staff.member, role=GuildStaffMembership.Role.SECRETARY)
            assert can_edit_meeting(_request(staff, roles={ROLE_MEMBER}), MeetingFactory(guild=guild)) is True

        def it_allows_an_admin():
            admin = _user("admin")
            assert can_edit_meeting(_request(admin, roles={ROLE_ADMIN, ROLE_MEMBER}), MeetingFactory()) is True

        def it_denies_a_plain_member():
            plain = _user("plain")
            meeting = MeetingFactory()
            GuildMembershipFactory(guild=meeting.guild, member=plain.member)
            assert can_edit_meeting(_request(plain, roles={ROLE_MEMBER}), meeting) is False

    def describe_council_meetings():
        def it_allows_an_admin():
            admin = _user("admin2")
            assert (
                can_edit_meeting(_request(admin, roles={ROLE_ADMIN, ROLE_MEMBER}), MeetingFactory(guild=None)) is True
            )

        def it_allows_a_guild_officer():
            officer = _user("officer")
            request = _request(officer, roles={ROLE_GUILD_OFFICER, ROLE_MEMBER})
            assert can_edit_meeting(request, MeetingFactory(guild=None)) is True

        def it_allows_any_guilds_lead_or_staff():
            lead = _user("anylead")
            GuildFactory(guild_lead=lead.member)
            assert can_edit_meeting(_request(lead, roles={ROLE_MEMBER}), MeetingFactory(guild=None)) is True

        def it_denies_a_plain_member():
            plain = _user("plain2")
            assert can_edit_meeting(_request(plain, roles={ROLE_MEMBER}), MeetingFactory(guild=None)) is False

        def it_denies_an_admin_previewing_as_member():
            admin = _user("admin3")
            request = _request(admin, roles={ROLE_ADMIN, ROLE_MEMBER}, picked=ROLE_MEMBER)
            assert can_edit_meeting(request, MeetingFactory(guild=None)) is False

        def it_denies_an_anonymous_request():
            assert can_edit_meeting(_request(AnonymousUser()), MeetingFactory(guild=None)) is False


def describe_can_propose_to_meeting():
    def describe_guild_meetings():
        def it_allows_an_active_member_of_the_guild():
            member_user = _user("gm1")
            meeting = MeetingFactory()
            GuildMembershipFactory(guild=meeting.guild, member=member_user.member)
            assert can_propose_to_meeting(_request(member_user, roles={ROLE_MEMBER}), meeting) is True

        def it_allows_the_guilds_leadership_trivially():
            lead = _user("gm2")
            meeting = MeetingFactory(guild=GuildFactory(guild_lead=lead.member))
            assert can_propose_to_meeting(_request(lead, roles={ROLE_MEMBER}), meeting) is True

        def it_denies_a_non_member_of_the_guild():
            outsider = _user("gm3")
            GuildMembershipFactory(guild=GuildFactory(), member=outsider.member)  # a different guild
            assert can_propose_to_meeting(_request(outsider, roles={ROLE_MEMBER}), MeetingFactory()) is False

        def it_denies_a_suspended_member_of_the_guild():
            member_user = _user("gm4")
            meeting = MeetingFactory()
            GuildMembershipFactory(guild=meeting.guild, member=member_user.member)
            member_user.member.status = Member.Status.SUSPENDED
            member_user.member.save(update_fields=["status"])
            assert can_propose_to_meeting(_request(member_user, roles={ROLE_MEMBER}), meeting) is False

        def it_denies_an_anonymous_request():
            assert can_propose_to_meeting(_request(AnonymousUser()), MeetingFactory()) is False

    def describe_council_meetings():
        def it_allows_a_guild_lead():
            lead = _user("cm1")
            GuildFactory(guild_lead=lead.member)
            assert can_propose_to_meeting(_request(lead, roles={ROLE_MEMBER}), MeetingFactory(guild=None)) is True

        def it_denies_a_plain_member():
            plain = _user("cm2")
            assert can_propose_to_meeting(_request(plain, roles={ROLE_MEMBER}), MeetingFactory(guild=None)) is False

    def describe_closed_meetings():
        def it_denies_everyone_once_the_minutes_are_locked():
            lead = _user("cl1")
            meeting = MeetingFactory(guild=GuildFactory(guild_lead=lead.member), approved=True)
            assert can_propose_to_meeting(_request(lead, roles={ROLE_MEMBER}), meeting) is False

        def it_denies_everyone_once_the_meeting_date_has_passed():
            lead = _user("cl2")
            guild = GuildFactory(guild_lead=lead.member)
            meeting = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() - timedelta(days=1))
            assert can_propose_to_meeting(_request(lead, roles={ROLE_MEMBER}), meeting) is False
