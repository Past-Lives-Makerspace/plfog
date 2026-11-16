"""Home's Needs Attention shows a guide their own tour requests, answered from Home (#1213)."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import AnnouncementDraft, Member
from polls.models import QuestionKind, QuestionSpec
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
)
from tests.polls.factories import sent_email_poll
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import TourBooking

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")
HTMX_FROM_HOME = {"HTTP_HX_REQUEST": "true", "HTTP_HX_CURRENT_URL": f"http://testserver{HOME}"}


def _block(html: str) -> str:
    """The Needs Attention card's markup."""
    return html.split('data-block="needs-attention"')[1].split("</section>")[0]


def _rows(html: str) -> list[str]:
    return re.findall(r'data-tour-request="(\d+)"', html)


def _request(guide: Member, *, days: int = 3, hour: int = 16, **kwargs: Any) -> TourBooking:
    """A request on ``guide``'s tour ``days`` out, past every cutoff, with hours that yield it."""
    day = timezone.localdate() + timedelta(days=days)
    TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
    defaults: dict[str, Any] = {"status": TourBooking.Status.PENDING, "visitor_name": "Zorblax Visitor"}
    return TourBookingFactory(guide=guide, starts_at=local(day, hour), **{**defaults, **kwargs})


@pytest.fixture
def guide() -> Member:
    return make_guide(full_legal_name="Gina Guide")


@pytest.fixture
def pending(guide: Member) -> TourBooking:
    return _request(guide, party_size=3, visitor_email="zorblax@example.com")


def describe_the_tour_requests_group():
    def it_shows_the_guide_their_requests_soonest_first(guide: Member, pending: TourBooking):
        sooner = _request(guide, days=2, visitor_name="Sooner Zorblax", party_size=1)
        html = signed_in(guide).get(HOME).content.decode()

        block = _block(html)
        assert "Tour Requests Waiting on You" in block
        assert _rows(block) == [str(sooner.pk), str(pending.pk)]
        row = block.split(f'data-tour-request="{pending.pk}"')[1].split("</li>")[0]
        when = timezone.localtime(pending.starts_at)
        by = timezone.localtime(pending.confirm_by)
        assert "Zorblax Visitor · 3 people" in row
        assert f"{when:%a, %b} {when.day} · {when:%-I:%M %p}" in row
        assert f"Confirm by {by:%a, %b} {by.day}, {by:%-I:%M %p}" in row
        assert f'hx-post="{reverse("tours:booking_confirm", args=[pending.pk])}"' in row
        assert f"open-confirm', 'tour-decline-{pending.pk}'" in row
        assert f'hx-post="{reverse("tours:booking_decline", args=[pending.pk])}"' in row
        assert "Message to the visitor (optional)" in row

    def it_shows_no_row_to_another_guide_or_an_admin(pending: TourBooking):
        other = make_guide(full_legal_name="Olive Other")
        admin = MemberFactory(full_legal_name="Ada Admin", fog_role=Member.FogRole.ADMIN)
        for member in (other, admin):
            client = signed_in(member)
            assert 'data-block="needs-attention"' not in client.get(HOME).content.decode()
            confirm = client.post(reverse("tours:booking_confirm", args=[pending.pk]), **HTMX_FROM_HOME)
            decline = client.post(reverse("tours:booking_decline", args=[pending.pk]), **HTMX_FROM_HOME)
            assert (confirm.status_code, decline.status_code) == (403, 403)
        pending.refresh_from_db()
        assert pending.is_request

    @pytest.mark.parametrize(
        "status",
        [
            TourBooking.Status.BOOKED,
            TourBooking.Status.CANCELLED,
            TourBooking.Status.ATTENDED,
            TourBooking.Status.NO_SHOW,
        ],
    )
    def it_leaves_out_every_answered_status(guide: Member, status: str):
        _request(guide, status=status)
        assert 'data-block="needs-attention"' not in signed_in(guide).get(HOME).content.decode()

    def it_leaves_out_a_request_past_its_confirm_by_time_before_the_lapse_runs(guide: Member):
        lapsed = _request(guide)
        TourBooking.objects.filter(pk=lapsed.pk).update(starts_at=timezone.now() + timedelta(hours=23))
        assert 'data-block="needs-attention"' not in signed_in(guide).get(HOME).content.decode()

    def it_costs_one_query_however_many_requests(guide: Member, pending: TourBooking):
        client = signed_in(guide)
        client.get(HOME)

        def count() -> int:
            with CaptureQueriesContext(connection) as queries:
                client.get(HOME)
            return len(queries)

        one = count()
        for hour in (10, 11, 12):
            _request(guide, hour=hour)
        assert count() == one


def describe_answering_from_home():
    def it_confirms_returns_home_with_the_toast_and_drops_the_row(
        guide: Member, pending: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        client = signed_in(guide)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(reverse("tours:booking_confirm", args=[pending.pk]), **HTMX_FROM_HOME)
        assert (response.status_code, response["HX-Redirect"]) == (204, HOME)
        pending.refresh_from_db()
        assert pending.status == TourBooking.Status.BOOKED
        (email,) = [message for message in mail.outbox if message.to == ["zorblax@example.com"]]
        assert email.subject.startswith("Your Past Lives tour is confirmed:")
        assert "METHOD:REQUEST" in str(email.attachments[0][1])
        home = client.get(HOME).content.decode()
        assert "Zorblax Visitor&#x27;s tour on" in home and "is confirmed. We emailed them." in home
        assert f'data-tour-request="{pending.pk}"' not in home

    def it_declines_with_the_message_returns_home_and_drops_the_row(
        guide: Member, pending: TourBooking, django_capture_on_commit_callbacks: Any
    ):
        client = signed_in(guide)
        url = reverse("tours:booking_decline", args=[pending.pk])
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, {"message": "Zorblax is away."}, **HTMX_FROM_HOME)
        assert (response.status_code, response["HX-Redirect"]) == (204, HOME)
        pending.refresh_from_db()
        assert pending.status == TourBooking.Status.CANCELLED
        (email,) = [message for message in mail.outbox if message.to == ["zorblax@example.com"]]
        assert "Zorblax is away." in email.body
        home = client.get(HOME).content.decode()
        assert "is declined. We emailed them." in home
        assert f'data-tour-request="{pending.pk}"' not in home

    def it_returns_to_the_tours_page_from_another_sites_home(guide: Member, pending: TourBooking):
        headers = {"HTTP_HX_REQUEST": "true", "HTTP_HX_CURRENT_URL": f"https://elsewhere.example{HOME}"}
        response = signed_in(guide).post(reverse("tours:booking_confirm", args=[pending.pk]), **headers)
        assert response["HX-Redirect"] == reverse("tours:upcoming")


def describe_polls_and_requests_together():
    def it_shows_each_group_alone_and_both_side_by_side(guide: Member):
        client = signed_in(guide)
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=guide)
        draft = AnnouncementDraftFactory(
            kind=AnnouncementDraft.Kind.POLL, audience=AnnouncementDraft.Audience.GUILD, guild=guild
        )
        poll = sent_email_poll(QuestionSpec("Any zorblax ideas?", QuestionKind.OPEN), now=timezone.now(), draft=draft)
        alone = _block(client.get(HOME).content.decode())
        assert f'data-needs-attention-poll="{poll.pk}"' in alone and "data-needs-attention-tours" not in alone

        booking = _request(guide)
        both = _block(client.get(HOME).content.decode())
        assert f'data-needs-attention-poll="{poll.pk}"' in both and _rows(both) == [str(booking.pk)]
        assert both.index("data-needs-attention-polls-link") < both.index("data-needs-attention-tours")

        poll.answer(member=guide, answers={poll.questions.get().pk: "Done"}, now=timezone.now())
        only = _block(client.get(HOME).content.decode())
        assert "data-needs-attention-poll=" not in only and _rows(only) == [str(booking.pk)]
