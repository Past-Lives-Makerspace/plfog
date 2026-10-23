"""BDD specs for #894 part 3: the other staff requests wait for the 8 AM email.

Each request event is a digest event with its own still-open check, read from the request
named in its emit period, and a bell link unique to the request, which its decision
methods clear and a delete clears through ``pre_delete``. A class change request has no
decision: it is always included and nothing clears it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    DiscountCodeFactory,
    DiscountCodeRequestFactory,
    InstructorFactory,
    UserFactory,
)
from classes.models import ClassOffering
from core.events import resolvers
from core.events.registry import EmailDelivery, get_event
from core.models import EventDelivery, Notification
from core.services.email_queue import DigestQueue
from core.urls_util import member_absolute_url, notification_click_url
from membership.models import (
    AdminCapability,
    CommunityEvent,
    GuildAnnouncement,
    Member,
    SpaceRequest,
    WikiPage,
    WikiReport,
)
from tests.core.events.digest_support import digest_emails
from tests.membership.factories import (
    CommunityEventFactory,
    GuildAnnouncementFactory,
    MeetingFactory,
    MeetingItemProposalFactory,
    MemberFactory,
    SpaceRequestFactory,
    WikiPageFactory,
    WikiReportFactory,
)

pytestmark = pytest.mark.django_db

PART_3_EVENTS = (
    "space.lease_requested",
    "space.cubby_requested",
    "discount_code.requested",
    "event.submitted",
    "guild_announcement.submitted",
    "meeting.item_proposed",
    "wiki.page_reported",
    "wiki.page_proposed",
    "instructor_application_received",
    "class_change_requested",
)


def _approver() -> User:
    return UserFactory(username="lee@example.com", email="lee@example.com")


def _bell(request: Any, event_key: str, user: User) -> Notification:
    """The bell row the request's emit wrote: its link is the request's own."""
    return Notification.objects.create(
        user=user, trigger=event_key, title="Waiting", body="", url=request.request_bell_path
    )


def _queued(request: Any, event_key: str, user: User, period: str) -> EventDelivery:
    """The morning email line the request's emit queued for ``user``."""
    return EventDelivery.objects.create(
        event_key=event_key,
        target_ref=f"user:{user.pk}",
        channel="digest",
        period=period,
        status=EventDelivery.Status.PENDING,
        title="Waiting",
        url=member_absolute_url(request.request_bell_path),
    )


def _is_open(event_key: str, row: EventDelivery) -> bool:
    return row.url in resolvers.open_request_urls(event_key, [row])


def _read(bell: Notification) -> bool:
    bell.refresh_from_db()
    return bell.read_at is not None


def _emitted(fake: Any, event_key: str) -> dict[str, Any]:
    """The kwargs of the captured emit for ``event_key``."""
    (call,) = [call for call in fake.call_args_list if call.args[0] == event_key]
    return call.kwargs


def _assert_lifecycle(request: Any, event_key: str, period: str, decide: Any) -> None:
    """Open while undecided; deciding closes it and marks the approvers' bell rows read."""
    user = _approver()
    bell = _bell(request, event_key, user)
    row = _queued(request, event_key, user, period)
    assert _is_open(event_key, row)
    decide()
    assert not _is_open(event_key, row)
    assert _read(bell)


def _assert_delete_clears(request: Any, event_key: str) -> None:
    bell = _bell(request, event_key, UserFactory(username="sam@example.com", email="sam@example.com"))
    request.delete()
    assert _read(bell)


def describe_the_part_3_requests():
    def it_sends_each_one_in_the_8am_email_with_a_still_open_check():
        for key in PART_3_EVENTS:
            assert get_event(key).email_delivery is EmailDelivery.DIGEST, key
            assert resolvers.has_open_check(key), key

    def it_keeps_orientation_requests_instant():
        # Felix (2026-10-23): a booking is often for a slot under a day away.
        assert get_event("orientation_requested").email_delivery is EmailDelivery.INSTANT


def describe_space_requests():
    def it_is_open_while_pending_and_approving_clears_the_bell():
        request = SpaceRequestFactory()
        _assert_lifecycle(
            request,
            "space.lease_requested",
            f"spacereq:{request.pk}:submitted",
            lambda: request.approve(reviewer=_approver_admin()),
        )

    def it_links_each_request_to_its_own_anchor_in_the_review_queue():
        with patch("core.events.emit.emit") as fake:
            request = SpaceRequestFactory(kind=SpaceRequest.RequestKind.CUBBY)
            request._notify_submitted()
        sent = _emitted(fake, "space.cubby_requested")
        assert notification_click_url(sent["url"]) == request.request_bell_path
        assert request.request_bell_path.endswith(f"#request-{request.pk}")

    def it_clears_the_bell_on_delete():
        _assert_delete_clears(SpaceRequestFactory(), "space.cubby_requested")


def _approver_admin() -> User:
    user = UserFactory(username="admin.lee@example.com", email="admin.lee@example.com")
    Member.objects.filter(user=user).update(fog_role=Member.FogRole.ADMIN)
    return user


def describe_discount_code_requested():
    def it_is_open_while_a_code_is_unapproved_and_approving_clears_the_bell():
        code = DiscountCodeFactory(is_approved=False)
        _assert_lifecycle(code, "discount_code.requested", f"discount:{code.pk}:requested", code.approve)

    def it_is_open_while_a_request_is_pending_and_declining_clears_the_bell():
        request = DiscountCodeRequestFactory()
        _assert_lifecycle(
            request,
            "discount_code.requested",
            f"discount_request:{request.pk}:requested",
            lambda: request.decline(_approver_admin(), "Not this term."),
        )

    def it_anchors_an_unapproved_codes_link_on_the_code_and_clears_both_on_delete():
        with patch("core.events.emit.emit") as fake:
            code = DiscountCodeFactory(is_approved=False, class_offering=ClassOfferingFactory())
        assert notification_click_url(_emitted(fake, "discount_code.requested")["url"]) == code.request_bell_path
        assert code.request_bell_path.endswith(f"#code-{code.pk}")
        _assert_delete_clears(code, "discount_code.requested")
        _assert_delete_clears(DiscountCodeRequestFactory(), "discount_code.requested")


def describe_event_submitted():
    def it_is_open_while_pending_and_declining_clears_the_bell():
        event = CommunityEventFactory(moderation_state=CommunityEvent.ModerationState.PENDING)
        _assert_lifecycle(
            event,
            "event.submitted",
            f"event:{event.pk}:submitted:1.0",
            lambda: event.decline(reviewer=_approver_admin(), notes="Pick another night."),
        )

    def it_links_the_bell_to_the_event_in_the_review_queue_and_clears_it_on_delete():
        event = CommunityEventFactory(moderation_state=CommunityEvent.ModerationState.CHANGES_REQUESTED)
        with patch("core.events.emit.emit") as fake:
            event.submit_for_review(submitted_by=UserFactory(username="pat@example.com", email="pat@example.com"))
        assert notification_click_url(_emitted(fake, "event.submitted")["url"]) == event.request_bell_path
        _assert_delete_clears(event, "event.submitted")


def describe_guild_announcement_submitted():
    def it_is_open_while_pending_and_declining_clears_the_bell():
        announcement = GuildAnnouncementFactory(pending=True)
        _assert_lifecycle(
            announcement,
            "guild_announcement.submitted",
            f"announcement:{announcement.pk}:submitted:1.0",
            lambda: announcement.decline(reviewer=_approver_admin(), notes="Too long."),
        )

    def it_links_the_bell_to_the_announcement_in_the_review_queue_and_clears_it_on_delete():
        announcement = GuildAnnouncementFactory(changes_requested=True)
        with patch("core.events.emit.emit") as fake:
            announcement.submit_for_review(
                submitted_by=UserFactory(username="pat@example.com", email="pat@example.com")
            )
        sent = _emitted(fake, "guild_announcement.submitted")
        assert notification_click_url(sent["url"]) == announcement.request_bell_path
        assert announcement.moderation_state == GuildAnnouncement.ModerationState.PENDING
        _assert_delete_clears(announcement, "guild_announcement.submitted")


def describe_meeting_item_proposed():
    def it_is_open_while_pending_and_declining_clears_the_bell():
        proposal = MeetingItemProposalFactory()
        _assert_lifecycle(
            proposal,
            "meeting.item_proposed",
            f"meeting-proposal:{proposal.pk}:submitted",
            lambda: proposal.decline(reviewer=_approver_admin()),
        )

    def it_anchors_each_proposals_bell_on_the_meeting_page_and_clears_it_with_the_meeting():
        meeting = MeetingFactory()
        with patch("core.events.emit.emit") as fake:
            proposal = meeting.propose_item(
                by=UserFactory(username="pat@example.com", email="pat@example.com"), title="Dust", why="It piles up."
            )
        assert notification_click_url(_emitted(fake, "meeting.item_proposed")["url"]) == proposal.request_bell_path
        assert proposal.request_bell_path == f"{reverse('hub_meeting', args=[meeting.pk])}#proposal-{proposal.pk}"
        bell = _bell(proposal, "meeting.item_proposed", _approver())
        meeting.delete()
        assert _read(bell)


def describe_wiki_page_reported():
    def it_is_open_until_resolved_and_resolving_clears_the_bell():
        report = WikiReportFactory()
        _assert_lifecycle(
            report, "wiki.page_reported", f"wiki_report:{report.pk}", lambda: report.resolve(by=MemberFactory())
        )

    def it_gives_the_bell_a_link_to_the_report_in_the_review_queue():
        page = WikiPageFactory()
        with patch("core.events.emit.emit") as fake:
            report = WikiReport.file(page=page, reporter=MemberFactory(), reason="The bit size is wrong.")
        assert notification_click_url(_emitted(fake, "wiki.page_reported")["url"]) == report.request_bell_path
        assert report.request_bell_path == f"{reverse('hub_wiki_review')}#report-{report.pk}"
        _assert_delete_clears(report, "wiki.page_reported")


def describe_wiki_page_proposed():
    def it_is_open_while_held_and_publishing_clears_the_bell():
        page = WikiPageFactory(guild=None, is_published=False, status=WikiPage.Status.OFFICIAL)
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        _assert_lifecycle(
            page,
            "wiki.page_proposed",
            f"wiki_proposed:{page.pk}",
            lambda: page.publish_proposal(by=admin, as_official=True),
        )

    def it_stays_open_after_being_sent_back_as_the_review_queue_keeps_it():
        page = WikiPageFactory(guild=None, is_published=False, status=WikiPage.Status.OFFICIAL)
        row = _queued(page, "wiki.page_proposed", _approver(), f"wiki_proposed:{page.pk}")
        page.decline_proposal(by=MemberFactory(fog_role=Member.FogRole.ADMIN), note="Add the guard step.")
        assert _is_open("wiki.page_proposed", row)

    def it_gives_the_bell_a_link_to_the_page_in_the_review_queue():
        page = WikiPageFactory(guild=None, is_published=False, status=WikiPage.Status.OFFICIAL)
        with patch("core.events.emit.emit") as fake:
            page.notify_scope_of_proposal(by=MemberFactory())
        assert notification_click_url(_emitted(fake, "wiki.page_proposed")["url"]) == page.request_bell_path
        _assert_delete_clears(page, "wiki.page_proposed")


def describe_instructor_application_received():
    def _applicant() -> Member:
        member = MemberFactory()
        with patch("core.events.emit.emit"):
            member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
        return member

    def it_is_open_until_declined_and_declining_clears_the_bell():
        member = _applicant()
        _assert_lifecycle(
            member,
            "instructor_application_received",
            f"member:{member.pk}:teaching_application:x",
            lambda: member.decline_teaching(decided_by=None, reason="Do the orientation first."),
        )

    def it_clears_the_bell_when_teaching_is_granted_or_given_by_promotion():
        member = _applicant()
        bell = _bell(member, "instructor_application_received", _approver())
        member.grant_teaching(granted_by=None)
        assert _read(bell)
        promoted = _applicant()
        bell = _bell(promoted, "instructor_application_received", _approver_admin())
        promoted.apply_admin_role(Member.ADMIN_ROLE_INSTRUCTOR)
        assert _read(bell)

    def it_names_the_applicant_in_the_bell_link():
        member = MemberFactory()
        with patch("core.events.emit.emit") as fake:
            member.apply_to_teach("Wheel throwing.", contact_method="email", contact_detail="reach@example.com")
        sent = _emitted(fake, "instructor_application_received")
        assert notification_click_url(sent["url"]) == member.request_bell_path
        assert f"?applicant={member.pk}#teaching-applications" in member.request_bell_path

    def it_clears_the_bell_when_the_member_is_deleted():
        _assert_delete_clears(_applicant(), "instructor_application_received")


def describe_class_change_requested():
    def it_is_always_included_and_each_request_is_its_own_line():
        instructor = InstructorFactory(user=UserFactory(username="alex@example.com", email="alex@example.com"))
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        with patch("core.events.emit.emit") as fake:
            offering.request_change(instructor, "Move it to Friday.")
        url = _emitted(fake, "class_change_requested")["url"]
        assert url.startswith(f"{reverse('classes:admin_class_edit', kwargs={'pk': offering.pk})}#change-")
        row = EventDelivery(event_key="class_change_requested", url=url, period="offering:1:change_request:2")
        assert resolvers.open_request_urls("class_change_requested", [row]) == {url}


def describe_the_8am_email_for_a_part_3_request():
    @pytest.fixture
    def holder() -> User:
        user = UserFactory(username="dana@example.com", email="dana@example.com", first_name="Dana", last_name="Ross")
        Member.objects.get(user=user).admin_capabilities.create(capability=AdminCapability.Capability.DISCOUNT_APPROVER)
        return user

    def it_queues_instead_of_mailing_and_the_morning_email_links_with_a_host(holder: User):
        request = DiscountCodeRequestFactory()
        assert [m for m in mail.outbox if m.to == ["dana@example.com"]] == []
        (email,) = digest_emails("dana@example.com")
        assert email.subject == "Action Needed: 1 Request Awaiting You"
        assert member_absolute_url(request.request_bell_path) in email.body
        assert member_absolute_url(request.request_bell_path).startswith("http")

    def it_leaves_a_request_decided_before_8am_out(holder: User):
        request = DiscountCodeRequestFactory()
        request.decline(holder, "Not this term.")
        assert digest_emails("dana@example.com") == []

    def it_shows_on_the_email_queue_tab_with_its_pill(holder: User):
        DiscountCodeRequestFactory()
        (dana,) = [person for person in DigestQueue.load(timezone.now()).people if person.email == "dana@example.com"]
        assert [(item.label, item.pill_label) for item in dana.items] == [
            (get_event("discount_code.requested").label, "Still waiting")
        ]
        staff = Client()
        staff.force_login(UserFactory(username="staff@example.com", email="staff@example.com", is_staff=True))
        html = staff.get(reverse("manage_activity"), {"tab": "queue"}).content.decode()
        assert "A discount code was requested" in html
        assert "Still waiting" in html


def describe_member_absolute_url():
    def it_puts_a_path_on_the_members_host_and_leaves_an_absolute_link_alone(settings: Any):
        settings.MEMBER_BASE_URL = "https://members.example.com/"
        assert member_absolute_url("/a/b/#c") == "https://members.example.com/a/b/#c"
        assert member_absolute_url("https://classes.example.com/x/") == "https://classes.example.com/x/"
        assert member_absolute_url("") == ""
