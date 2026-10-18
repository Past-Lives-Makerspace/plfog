"""BDD specs for Manage Events, the list (#831, part 2): who may open it, the lifecycle chips,
search, My Events, the columns, the studio-hours exclusion, a flat query count, and the
reviewer queue sending admins and Calendar Administrators here."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, CommunityEvent, Member
from tests.membership.factories import (
    CommunityEventFactory,
    EventRSVPFactory,
    GuildFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

States = CommunityEvent.ModerationState
LIST_URL = reverse("hub_admin_events")
NEEDS_REVIEW_URL = f"{LIST_URL}?status=needs_review"


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def _admin(client: Client, username: str = "events_admin") -> User:
    user = _user_with_role(username, fog_role=Member.FogRole.ADMIN)
    client.login(username=username, password="pass")
    return user


def _approver(client: Client, username: str = "cal_admin") -> User:
    user = _user_with_role(username)
    user.member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)
    client.login(username=username, password="pass")
    return user


def _lead(client: Client, username: str = "lead") -> User:
    user = _user_with_role(username)
    GuildFactory(guild_lead=user.member)
    client.login(username=username, password="pass")
    return user


def _rows(resp: object) -> list[int]:
    """The event pks on the page, in order."""
    return [event.pk for event in resp.context["page"]]  # type: ignore[attr-defined]


def _counts(resp: object) -> dict[str, int]:
    return {row.key: row.count for row in resp.context["facets"]}  # type: ignore[attr-defined]


def describe_the_gate():
    def it_opens_for_an_admin(client: Client):
        _admin(client)
        assert client.get(LIST_URL).status_code == 200

    def it_opens_for_a_calendar_administrator(client: Client):
        _approver(client)
        assert client.get(LIST_URL).status_code == 200

    def it_refuses_a_guild_lead(client: Client):
        _lead(client)
        assert client.get(LIST_URL).status_code == 403

    def it_refuses_a_plain_member(client: Client):
        _user_with_role("plain")
        client.login(username="plain", password="pass")
        assert client.get(LIST_URL).status_code == 403

    def it_sends_a_visitor_to_sign_in(client: Client):
        resp = client.get(LIST_URL)
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]


def describe_the_chips():
    def _one_of_each() -> dict[str, CommunityEvent]:
        now = timezone.now()
        return {
            "upcoming": CommunityEventFactory(community=True, title="Potluck"),
            "past": CommunityEventFactory(community=True, title="Old Potluck", starts_at=now - timedelta(days=3)),
            "pending": CommunityEventFactory(community=True, pending=True, title="Proposed Potluck"),
            "scheduled": CommunityEventFactory(
                community=True,
                title="Parked Potluck",
                moderation_state=States.SCHEDULED,
                publish_at=now + timedelta(days=2),
            ),
            "declined": CommunityEventFactory(community=True, declined=True, title="Declined Potluck"),
            "draft": CommunityEventFactory(community=True, title="Draft Potluck", moderation_state=States.DRAFT),
            "studio_hours": CommunityEventFactory(studio_hours=True, title="Glass Studio Hours"),
        }

    def it_counts_every_state_but_studio_hours(client: Client):
        _admin(client)
        _one_of_each()
        resp = client.get(LIST_URL)
        assert _counts(resp) == {
            "": 6,
            "upcoming": 1,
            "needs_review": 1,
            "scheduled": 1,
            "past": 1,
            "declined": 1,
        }

    def it_never_lists_studio_hours(client: Client):
        _admin(client)
        events = _one_of_each()
        resp = client.get(LIST_URL)
        assert events["studio_hours"].pk not in _rows(resp)
        assert f'data-event-row="{events["studio_hours"].pk}"' not in resp.content.decode()
        assert events["draft"].pk in _rows(resp)

    @pytest.mark.parametrize(
        ("status", "key"),
        [("upcoming", "upcoming"), ("needs_review", "pending"), ("scheduled", "scheduled"), ("past", "past")],
    )
    def it_narrows_the_table_to_the_chip(client: Client, status: str, key: str):
        _admin(client)
        events = _one_of_each()
        resp = client.get(f"{LIST_URL}?status={status}")
        assert _rows(resp) == [events[key].pk]
        assert resp.context["selected_status"] == status

    def it_shows_the_declined_chip_and_treats_junk_as_all(client: Client):
        _admin(client)
        events = _one_of_each()
        assert _rows(client.get(f"{LIST_URL}?status=declined")) == [events["declined"].pk]
        resp = client.get(f"{LIST_URL}?status=bogus")
        assert len(_rows(resp)) == 6
        assert resp.context["selected_status"] == ""

    def it_marks_the_selected_chip(client: Client):
        _admin(client)
        body = client.get(NEEDS_REVIEW_URL).content.decode()
        assert 'data-facet="needs_review"' in body
        assert body.index('hub-btn--primary" href="?status=needs_review"') > 0


def describe_search():
    def it_matches_the_title_the_guild_and_the_location(client: Client):
        _admin(client)
        guild = GuildFactory(name="Woodshop Collective")
        by_title = CommunityEventFactory(community=True, title="Lathe Night")
        by_guild = CommunityEventFactory(guild=guild, title="Meeting")
        by_place = CommunityEventFactory(community=True, title="Social", location="Lathe room")
        CommunityEventFactory(community=True, title="Unrelated")
        assert _rows(client.get(f"{LIST_URL}?q=Woodshop")) == [by_guild.pk]
        assert sorted(_rows(client.get(f"{LIST_URL}?q=lathe"))) == sorted([by_title.pk, by_place.pk])

    def it_shows_an_empty_state_for_no_match(client: Client):
        _admin(client)
        CommunityEventFactory(community=True, title="Potluck")
        body = client.get(f"{LIST_URL}?q=zzz").content.decode()
        assert "data-events-empty" in body
        assert "data-event-row" not in body


def describe_my_events():
    def it_keeps_what_the_signed_in_user_created_or_proposed(client: Client):
        user = _approver(client)
        mine = CommunityEventFactory(community=True, created_by=user)
        proposed = CommunityEventFactory(community=True, pending=True, submitted_by=user)
        CommunityEventFactory(community=True)
        resp = client.get(f"{LIST_URL}?mine=1")
        assert sorted(_rows(resp)) == sorted([mine.pk, proposed.pk])
        assert resp.context["mine_active"] is True
        assert resp.context["mine_count"] == 2
        assert resp.context["mine_toggle_url"] == ""

    def it_strips_a_bogus_mine_value(client: Client):
        _admin(client)
        CommunityEventFactory(community=True)
        resp = client.get(f"{LIST_URL}?mine=yes&status=upcoming")
        assert resp.context["mine_active"] is False
        assert resp.context["mine_toggle_url"] == "status=upcoming&mine=1"
        assert len(_rows(resp)) == 1


def describe_the_columns():
    def it_renders_every_cell_from_the_row(client: Client):
        user = _admin(client)
        user.member.preferred_name = "Lee"
        user.member.save(update_fields=["preferred_name"])
        guild = GuildFactory(name="Fiber Arts")
        event = CommunityEventFactory(
            guild_hosted=True,
            guild=guild,
            title="Spin In",
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            created_by=user,
        )
        EventRSVPFactory(event=event)
        EventRSVPFactory(event=event)
        body = client.get(LIST_URL).content.decode()
        assert f'href="{reverse("hub_admin_event", args=[event.pk])}">Spin In</a>' in body
        assert (
            '<td class="pl-events-admin__kind">Public event<span class="pl-events-admin__guild" data-event-guild>Fiber Arts</span></td>'
            in body
        )
        assert "data-recurrence-pill" in body
        assert 'data-event-status="published"' in body
        assert "<td data-rsvp-count>2</td>" in body
        assert '<td class="pl-events-admin__eventbrite" data-eventbrite-cell>' in body
        assert '<td class="pl-events-admin__creator">Lee</td>' in body

    def it_keeps_the_seven_columns_inside_the_content_column(client: Client):
        # The widths live in events-admin.css: the Eventbrite header is an icon with its name for
        # screen readers, and Created by carries the class the stylesheet hides on narrow viewports.
        _admin(client)
        CommunityEventFactory(community=True, recurrence=CommunityEvent.Recurrence.WEEKLY)
        body = client.get(LIST_URL).content.decode()
        assert "css/events-admin.css" in body
        assert '<th class="pl-events-admin__eventbrite">' in body
        assert '<span class="sr-only">Eventbrite</span></th>' in body
        assert '<th class="pl-events-admin__creator">Created by</th>' in body
        assert 'class="pl-events-admin__dates">' in body
        assert '<td class="pl-events-admin__status">' in body

    def it_sorts_newest_first_and_falls_back_on_a_bogus_key(client: Client):
        _admin(client)
        later = CommunityEventFactory(community=True, starts_at=timezone.now() + timedelta(days=30))
        sooner = CommunityEventFactory(community=True, starts_at=timezone.now() + timedelta(days=1))
        assert _rows(client.get(LIST_URL)) == [later.pk, sooner.pk]
        assert _rows(client.get(f"{LIST_URL}?sort=starts_at&dir=asc")) == [sooner.pk, later.pk]
        assert _rows(client.get(f"{LIST_URL}?sort=bogus")) == [later.pk, sooner.pk]

    def it_links_add_event_to_the_composer(client: Client):
        _admin(client)
        assert f'href="{reverse("hub_event_add")}"' in client.get(LIST_URL).content.decode()


def describe_the_query_count():
    def _rows_with_everything(n: int) -> None:
        for i in range(n):
            creator = _user_with_role(f"creator{i}_{n}")
            event = CommunityEventFactory(guild_hosted=True, created_by=creator, submitted_by=creator)
            EventRSVPFactory(event=event)

    def it_stays_flat_as_rows_grow(client: Client):
        _admin(client)
        _rows_with_everything(2)
        # The viewer's first hub page load creates their billing tab (three queries once);
        # the steady state is what the list is measured on.
        client.get(LIST_URL)
        with CaptureQueriesContext(connection) as small:
            assert len(_rows(client.get(LIST_URL))) == 2
        _rows_with_everything(6)
        with CaptureQueriesContext(connection) as large:
            assert len(_rows(client.get(LIST_URL))) == 8
        assert len(large.captured_queries) == len(small.captured_queries)


def describe_the_reviewer_queue():
    QUEUE_URL = reverse("hub_event_review_queue")

    def it_sends_an_admin_to_the_needs_review_chip(client: Client):
        _admin(client)
        resp = client.get(QUEUE_URL)
        assert resp.status_code == 302
        assert resp["Location"] == NEEDS_REVIEW_URL

    def it_sends_a_calendar_administrator_there_too(client: Client):
        _approver(client)
        assert client.get(QUEUE_URL)["Location"] == NEEDS_REVIEW_URL

    def it_keeps_the_scoped_queue_for_a_guild_lead(client: Client):
        user = _user_with_role("queue_lead")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="queue_lead", password="pass")
        CommunityEventFactory(guild=guild, pending=True)
        resp = client.get(QUEUE_URL)
        assert resp.status_code == 200
        assert resp.templates[0].name == "hub/event_review_queue.html"

    def it_still_refuses_a_plain_member(client: Client):
        _user_with_role("plain2")
        client.login(username="plain2", password="pass")
        assert client.get(QUEUE_URL).status_code == 403
