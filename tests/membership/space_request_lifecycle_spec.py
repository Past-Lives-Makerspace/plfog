"""BDD specs for the SpaceRequest lifecycle — submit / approve / decline / withdraw,
their guards, the idempotency constraint, and the notifications each transition fires
(including who the routing actually reaches)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from core.models import EventDelivery, SiteActivity
from membership.models import InvalidSpaceRequestTransition, Lease, Member, Space, SpaceRequest
from tests.membership.factories import (
    GuildFactory,
    MapHotspotFactory,
    MemberFactory,
    MembershipPlanFactory,
    SpaceFactory,
    SpaceRequestFactory,
)


def _user(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _member_with_user(username: str) -> Member:
    """A signed-in member — the User's auto-provisioned Member, named for readability."""
    user = _user(username)
    member = user.member
    member.preferred_name = username.title()
    member.status = Member.Status.ACTIVE
    member.save()
    return member


def _admin(username: str = "admin") -> Member:
    member = _member_with_user(username)
    member.fog_role = Member.FogRole.ADMIN
    member.save()
    return member


def _deliveries(event_key: str) -> int:
    return EventDelivery.objects.filter(event_key=event_key, channel="in_app").count()


def _email_recipients(event_key: str) -> set[str]:
    """The usernames the EMAIL channel actually reached (the spine records 'user:<pk>')."""
    refs = EventDelivery.objects.filter(event_key=event_key, channel="email").values_list("target_ref", flat=True)
    ids = [int(ref.split(":", 1)[1]) for ref in refs if ref.startswith("user:")]
    return set(User.objects.filter(pk__in=ids).values_list("username", flat=True))


@pytest.mark.django_db
def describe_submit():
    def it_stores_a_pending_request_and_notifies_the_admins():
        _admin()
        requester = _member_with_user("robin")
        space = SpaceFactory(manual_price=Decimal("420.00"))
        request = SpaceRequest(space=space, kind=SpaceRequest.RequestKind.LEASE, message="For pottery.")
        request.submit(requester=requester)
        request.refresh_from_db()
        assert request.state == SpaceRequest.ModerationState.PENDING
        assert request.requester == requester
        assert request.is_open is True
        assert _deliveries("space.lease_requested") == 1

    def it_routes_a_guild_owned_cubby_to_that_guilds_lead():
        lead = _member_with_user("lead")
        guild = GuildFactory(guild_lead=lead)
        requester = _member_with_user("robin2")
        space = SpaceFactory(sublet_guild=guild)
        request = SpaceRequest(space=space, kind=SpaceRequest.RequestKind.CUBBY)
        request.submit(requester=requester)
        assert _deliveries("space.cubby_requested") == 1
        assert "lead" in _email_recipients("space.cubby_requested")

    def it_falls_back_to_the_admins_for_an_unowned_cubby():
        _admin("boss")
        requester = _member_with_user("robin3")
        request = SpaceRequest(space=SpaceFactory(sublet_guild=None), kind=SpaceRequest.RequestKind.CUBBY)
        request.submit(requester=requester)
        assert _email_recipients("space.cubby_requested") == {"boss"}

    def it_logs_exactly_one_activity_row():
        requester = _member_with_user("robin4")
        SpaceRequest(space=SpaceFactory()).submit(requester=requester)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.SPACE_REQUEST).count() == 1

    def it_refuses_to_resubmit_an_existing_request():
        request = SpaceRequestFactory()
        with pytest.raises(InvalidSpaceRequestTransition):
            request.submit(requester=request.requester)


@pytest.mark.django_db
def describe_approve():
    def it_records_the_reviewer_and_notifies_the_member():
        requester = _member_with_user("robin5")
        reviewer = _user("rev")
        request = SpaceRequestFactory(requester=requester)
        request.approve(reviewer=reviewer)
        request.refresh_from_db()
        assert request.state == SpaceRequest.ModerationState.APPROVED
        assert request.reviewed_by == reviewer
        assert request.reviewed_at is not None
        assert _email_recipients("space.request_approved") == {"robin5"}

    def it_never_creates_a_lease():
        request = SpaceRequestFactory(requester=_member_with_user("robin6"))
        request.approve(reviewer=_user("rev2"))
        assert Lease.objects.count() == 0

    def it_leaves_the_space_untouched():
        space = SpaceFactory(status=Space.Status.AVAILABLE)
        request = SpaceRequestFactory(requester=_member_with_user("robin7"), space=space)
        request.approve(reviewer=_user("rev3"))
        space.refresh_from_db()
        assert space.status == Space.Status.AVAILABLE

    def it_raises_from_a_withdrawn_request():
        request = SpaceRequestFactory(state=SpaceRequest.ModerationState.WITHDRAWN)
        with pytest.raises(InvalidSpaceRequestTransition):
            request.approve(reviewer=_user("rev4"))


@pytest.mark.django_db
def describe_decline():
    def it_stores_the_note_and_notifies_the_member():
        requester = _member_with_user("robin8")
        request = SpaceRequestFactory(requester=requester)
        request.decline(reviewer=_user("rev5"), notes="That one is spoken for.")
        request.refresh_from_db()
        assert request.state == SpaceRequest.ModerationState.DECLINED
        assert request.review_notes == "That one is spoken for."
        assert _email_recipients("space.request_declined") == {"robin8"}

    def it_requires_a_note():
        request = SpaceRequestFactory()
        with pytest.raises(ValueError):
            request.decline(reviewer=_user("rev6"), notes="   ")

    def it_raises_from_an_approved_request():
        request = SpaceRequestFactory(state=SpaceRequest.ModerationState.APPROVED)
        with pytest.raises(InvalidSpaceRequestTransition):
            request.decline(reviewer=_user("rev7"), notes="No.")


@pytest.mark.django_db
def describe_withdraw():
    def it_keeps_the_row_as_an_audit_record():
        requester = _member_with_user("robin9")
        request = SpaceRequestFactory(requester=requester)
        request.withdraw(by=requester.user)
        request.refresh_from_db()
        assert request.state == SpaceRequest.ModerationState.WITHDRAWN
        assert SpaceRequest.objects.filter(pk=request.pk).exists()

    def it_notifies_nobody():
        requester = _member_with_user("robin10")
        request = SpaceRequestFactory(requester=requester)
        request.withdraw(by=requester.user)
        assert EventDelivery.objects.filter(event_key__startswith="space.").count() == 0

    def it_refuses_someone_elses_request():
        request = SpaceRequestFactory(requester=_member_with_user("robin11"))
        with pytest.raises(InvalidSpaceRequestTransition):
            request.withdraw(by=_user("stranger"))

    def it_raises_from_an_already_declined_request():
        requester = _member_with_user("robin12")
        request = SpaceRequestFactory(requester=requester, state=SpaceRequest.ModerationState.DECLINED)
        with pytest.raises(InvalidSpaceRequestTransition):
            request.withdraw(by=requester.user)


@pytest.mark.django_db
def describe_idempotency():
    def it_blocks_a_second_pending_request_for_the_same_space():
        requester = MemberFactory()
        space = SpaceFactory()
        SpaceRequestFactory(requester=requester, space=space)
        with pytest.raises(IntegrityError), transaction.atomic():
            SpaceRequestFactory(requester=requester, space=space)

    def it_lets_a_member_ask_again_after_withdrawing():
        requester = _member_with_user("robin13")
        space = SpaceFactory()
        first = SpaceRequestFactory(requester=requester, space=space)
        first.withdraw(by=requester.user)
        again = SpaceRequestFactory(requester=requester, space=space)
        assert again.pk != first.pk

    def it_lets_a_different_member_ask_for_the_same_space():
        space = SpaceFactory()
        SpaceRequestFactory(space=space)
        assert SpaceRequestFactory(space=space).pk is not None


@pytest.mark.django_db
def describe_relationships():
    def it_protects_request_history_from_a_deleted_space():
        request = SpaceRequestFactory()
        with pytest.raises(ProtectedError):
            request.space.delete()

    def it_keeps_the_request_when_its_marker_is_removed():
        hotspot = MapHotspotFactory()
        request = SpaceRequestFactory(space=hotspot.space, hotspot=hotspot)
        hotspot.delete()
        request.refresh_from_db()
        assert request.hotspot is None

    def describe_review_audience_label():
        def it_names_the_guild_lead_for_a_guild_owned_cubby():
            guild = GuildFactory(name="Clay Guild")
            request = SpaceRequestFactory(space=SpaceFactory(sublet_guild=guild), kind=SpaceRequest.RequestKind.CUBBY)
            assert request.review_audience_label == "the Clay Guild lead"

        def it_names_the_admins_for_a_studio():
            guild = GuildFactory(name="Clay Guild")
            request = SpaceRequestFactory(space=SpaceFactory(sublet_guild=guild), kind=SpaceRequest.RequestKind.LEASE)
            assert request.review_audience_label == "the makerspace admins"

        def it_names_the_admins_for_an_unowned_cubby():
            request = SpaceRequestFactory(space=SpaceFactory(sublet_guild=None), kind=SpaceRequest.RequestKind.CUBBY)
            assert request.review_audience_label == "the makerspace admins"

    def it_describes_itself_by_member_space_and_state():
        request = SpaceRequestFactory(
            requester=MemberFactory(preferred_name="Robin"), space=SpaceFactory(space_id="A9")
        )
        assert str(request) == "Robin → A9 (Pending review)"


@pytest.mark.django_db
def describe_queryset():
    def it_filters_to_pending():
        SpaceRequestFactory()
        SpaceRequestFactory(state=SpaceRequest.ModerationState.APPROVED)
        assert SpaceRequest.objects.pending().count() == 1

    def describe_for_scope():
        def it_returns_everything_for_an_admin():
            SpaceRequestFactory()
            SpaceRequestFactory(kind=SpaceRequest.RequestKind.CUBBY)
            assert SpaceRequest.objects.for_scope(True).count() == 2

        def it_returns_only_a_leads_own_guild_cubbies():
            guild = GuildFactory()
            other = GuildFactory()
            mine = SpaceRequestFactory(space=SpaceFactory(sublet_guild=guild), kind=SpaceRequest.RequestKind.CUBBY)
            SpaceRequestFactory(space=SpaceFactory(sublet_guild=other), kind=SpaceRequest.RequestKind.CUBBY)
            # A studio lease in the same guild is still admin business, not the lead's.
            SpaceRequestFactory(space=SpaceFactory(sublet_guild=guild), kind=SpaceRequest.RequestKind.LEASE)
            scoped = SpaceRequest.objects.for_scope([guild])
            assert list(scoped) == [mine]
