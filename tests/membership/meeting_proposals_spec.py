"""BDD specs for agenda-item proposals: the approve / decline / withdraw lifecycle
(copying the CommunityEvent decision idiom) and the meeting permission helpers."""

from __future__ import annotations

from datetime import date, timedelta

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
from membership.permissions import can_edit_meeting, can_propose_to_meeting, viewer_guild_membership_ids
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

    def describe_carry_over():
        def it_links_to_a_fresh_proposal_on_the_next_dated_meeting():
            guild = GuildFactory()
            source = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=5))
            nxt = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=35))
            proposer = _user("co1")
            proposal = MeetingItemProposalFactory(
                meeting=source, title="Buy clamps", why="We share two.", proposed_by=proposer
            )
            reviewer = _user("cor1")
            new_proposal = proposal.carry_over(reviewer=reviewer)
            proposal.refresh_from_db()
            assert new_proposal is not None
            assert new_proposal.meeting == nxt
            assert new_proposal.state == MeetingItemProposal.State.PENDING
            assert new_proposal.title == "Buy clamps"
            assert new_proposal.why == "We share two."
            assert new_proposal.proposed_by == proposer
            assert proposal.state == MeetingItemProposal.State.CARRIED_OVER
            assert proposal.carried_to == new_proposal
            assert proposal.reviewed_by == reviewer
            assert new_proposal.carried_from == proposal
            assert _deliveries("meeting.item_decided") == 1

        def it_defers_when_no_next_meeting_exists_yet():
            proposal = MeetingItemProposalFactory(proposed_by=_user("co2"))
            result = proposal.carry_over(reviewer=_user("cor2"))
            proposal.refresh_from_db()
            assert result is None
            assert proposal.state == MeetingItemProposal.State.CARRIED_OVER
            assert proposal.carried_to is None
            assert _deliveries("meeting.item_decided") == 1

        def it_refuses_a_non_pending_proposal():
            proposal = MeetingItemProposalFactory(proposed_by=_user("co3"))
            proposal.decline(reviewer=_user("cor3"))
            with pytest.raises(InvalidProposalTransition):
                proposal.carry_over(reviewer=_user("cor4"))

        def it_refuses_when_the_meeting_is_locked():
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(approved=True), proposed_by=_user("co4"))
            with pytest.raises(MeetingLockedError):
                proposal.carry_over(reviewer=_user("cor5"))

    def describe_table():
        def it_sets_aside_with_a_note():
            proposal = MeetingItemProposalFactory(proposed_by=_user("t1"))
            reviewer = _user("tr1")
            proposal.table(reviewer=reviewer, note="Revisit next quarter.")
            proposal.refresh_from_db()
            assert proposal.state == MeetingItemProposal.State.TABLED
            assert proposal.reviewed_by == reviewer
            assert proposal.review_note == "Revisit next quarter."
            assert proposal.reviewed_at is not None
            assert _deliveries("meeting.item_decided") == 1

        def it_allows_a_blank_note():
            proposal = MeetingItemProposalFactory(proposed_by=_user("t2"))
            proposal.table(reviewer=_user("tr2"))
            proposal.refresh_from_db()
            assert proposal.state == MeetingItemProposal.State.TABLED
            assert proposal.review_note == ""

        def it_refuses_a_non_pending_proposal():
            proposal = MeetingItemProposalFactory(proposed_by=_user("t3"))
            proposal.decline(reviewer=_user("tr3"))
            with pytest.raises(InvalidProposalTransition):
                proposal.table(reviewer=_user("tr4"))

        def it_refuses_when_the_meeting_is_locked():
            proposal = MeetingItemProposalFactory(meeting=MeetingFactory(approved=True), proposed_by=_user("t4"))
            with pytest.raises(MeetingLockedError):
                proposal.table(reviewer=_user("tr5"))

    def describe_attach_carryover_to():
        def it_materializes_a_fresh_pending_proposal_on_the_target():
            proposer = _user("at1")
            proposal = MeetingItemProposalFactory(title="New vise", why="Ours wobbles.", proposed_by=proposer)
            proposal.carry_over(reviewer=_user("atr1"))  # defers — no next meeting yet
            proposal.refresh_from_db()
            target = MeetingFactory(
                guild=proposal.meeting.guild, scheduled_date=timezone.localdate() + timedelta(days=40)
            )
            new_proposal = proposal.attach_carryover_to(target)
            proposal.refresh_from_db()
            assert new_proposal.meeting == target
            assert new_proposal.state == MeetingItemProposal.State.PENDING
            assert new_proposal.title == "New vise"
            assert new_proposal.why == "Ours wobbles."
            assert new_proposal.proposed_by == proposer
            assert proposal.carried_to == new_proposal

        def it_refuses_a_non_carried_over_proposal():
            proposal = MeetingItemProposalFactory(proposed_by=_user("at2"))  # still pending
            with pytest.raises(InvalidProposalTransition):
                proposal.attach_carryover_to(MeetingFactory())

        def it_refuses_when_already_attached():
            guild = GuildFactory()
            source = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=5))
            MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=35))  # a next exists
            proposal = MeetingItemProposalFactory(meeting=source, proposed_by=_user("at3"))
            proposal.carry_over(reviewer=_user("atr3"))  # links eagerly → carried_to set
            proposal.refresh_from_db()
            another = MeetingFactory(guild=guild, scheduled_date=timezone.localdate() + timedelta(days=65))
            with pytest.raises(InvalidProposalTransition):
                proposal.attach_carryover_to(another)

    def describe_carried_from_label():
        def it_is_blank_for_an_original():
            proposal = MeetingItemProposalFactory(proposed_by=_user("cfl1"))
            assert proposal.carried_from_label == ""

        def it_names_the_source_month():
            guild = GuildFactory()
            source = MeetingFactory(guild=guild, scheduled_date=date(2026, 3, 10))
            MeetingFactory(guild=guild, scheduled_date=date(2026, 4, 14))
            proposal = MeetingItemProposalFactory(meeting=source, proposed_by=_user("cfl2"))
            new_proposal = proposal.carry_over(reviewer=_user("cflr2"))
            assert new_proposal is not None
            assert new_proposal.carried_from_label == "Carried over from March 2026"

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

    def describe_with_a_prefetched_membership_set():
        def it_allows_a_member_of_the_scope_straight_from_the_set():
            member_user = _user("bulk1")
            meeting = MeetingFactory()
            req = _request(member_user, roles={ROLE_MEMBER})
            assert can_propose_to_meeting(req, meeting, member_guild_ids={meeting.guild_id}) is True

        def it_denies_a_member_absent_from_the_set():
            member_user = _user("bulk2")
            meeting = MeetingFactory()
            GuildMembershipFactory(guild=meeting.guild, member=member_user.member)  # a real roster row
            req = _request(member_user, roles={ROLE_MEMBER})
            # The set is authoritative for the bulk path — an empty set means "not proposable".
            assert can_propose_to_meeting(req, meeting, member_guild_ids=set()) is False

        def it_still_lets_an_editor_through_when_absent_from_the_set():
            lead = _user("bulk3")
            meeting = MeetingFactory(guild=GuildFactory(guild_lead=lead.member))
            req = _request(lead, roles={ROLE_MEMBER})
            assert can_propose_to_meeting(req, meeting, member_guild_ids=set()) is True


def describe_viewer_guild_membership_ids():
    def it_returns_the_members_joined_guild_pks():
        member_user = _user("vg1")
        g1 = GuildFactory(name="G-one")
        g2 = GuildFactory(name="G-two")
        GuildMembershipFactory(guild=g1, member=member_user.member)
        GuildMembershipFactory(guild=g2, member=member_user.member)
        req = _request(member_user, roles={ROLE_MEMBER})
        assert viewer_guild_membership_ids(req) == {g1.pk, g2.pk}

    def it_is_empty_for_a_request_with_no_member():
        assert viewer_guild_membership_ids(_request(AnonymousUser())) == set()
