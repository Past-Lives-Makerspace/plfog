"""The seed_holiday_bizarre command (Track C, part 4): Lee's hub, its nine doors, four goals and 21 shifts,
written from hq.holiday_bizarre for a published calendar event, idempotently, with a dry run that writes nothing."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone

from hq.holiday_bizarre import CARDS, GOALS, HUB, SHIFTS
from hq.models import DEFAULT_CARDS, EventHub, HubCard, HubGoal, ShiftPhase, ShiftSignup, VolunteerShift
from membership.models import CommunityEvent
from tests.hq.factories import ShiftSignupFactory
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db


def _run(*args: str) -> str:
    out = StringIO()
    call_command("seed_holiday_bizarre", *args, stdout=out)
    return out.getvalue()


def _counts() -> tuple[int, int, int, int]:
    return (
        EventHub.objects.count(),
        HubCard.objects.count(),
        HubGoal.objects.count(),
        VolunteerShift.objects.count(),
    )


@pytest.fixture
def event(db) -> CommunityEvent:
    """The published Holiday Bizarre, a week out."""
    return CommunityEventFactory(community=True, title="Holiday Bizarre")


def describe_lees_content():
    def it_is_the_hub_nine_doors_four_goals_and_twenty_one_shifts():
        assert HUB.slug == "holiday-bizarre-2026"
        assert CARDS is DEFAULT_CARDS
        assert len(CARDS) == 9
        assert [goal.label for goal in GOALS] == ["Raised", "Wine wall bottles", "Volunteer spots", "RSVPs"]
        assert len(SHIFTS) == 21

    def it_asks_for_as_many_volunteers_as_the_spots_goal():
        spots = next(goal for goal in GOALS if goal.kind == HubGoal.Kind.VOLUNTEER_SPOTS)
        assert sum(shift.need for shift in SHIFTS) == spots.target == 82

    def it_counts_the_raised_goal_in_whole_dollars():
        raised = GOALS[0]
        assert (raised.kind, raised.is_money, raised.target) == (HubGoal.Kind.MANUAL, True, 10_000)

    def it_needs_a_permit_on_the_bartender_shifts_only():
        with_permit = [shift.role for shift in SHIFTS if shift.requires_olcc]
        assert with_permit == ["Bartender, first half", "Bartender, second half"]

    def it_keys_every_shift_on_a_distinct_role_and_when():
        keys = [(shift.role, shift.when_text) for shift in SHIFTS]
        assert len(keys) == len(set(keys))

    def it_spreads_the_shifts_over_the_four_phases():
        by_phase = {phase: sum(1 for shift in SHIFTS if shift.phase == phase) for phase in ShiftPhase}
        assert by_phase == {ShiftPhase.BEFORE: 4, ShiftPhase.SETUP: 2, ShiftPhase.DURING: 13, ShiftPhase.AFTER: 2}


def describe_seeding():
    def it_makes_the_hub_its_doors_goals_and_shifts_for_a_published_event(event):
        out = _run("--event", str(event.pk))

        hub = EventHub.objects.get(slug=HUB.slug)
        assert (hub.title, hub.eyebrow, hub.intro, hub.footer_note) == (
            HUB.title,
            HUB.eyebrow,
            HUB.intro,
            HUB.footer_note,
        )
        assert hub.event == event
        assert hub.is_open is True
        cards = list(hub.cards.all())
        assert [card.kind for card in cards] == [spec["kind"] for spec in CARDS]
        assert [card.title for card in cards] == [spec["title"] for spec in CARDS]
        assert [card.sort_order for card in cards] == list(range(9))
        assert {card.audience for card in cards} == {HubCard.Audience.ADMINS}
        goals = list(hub.goals.all())
        assert [(goal.label, goal.kind, goal.target, goal.is_money, goal.current_value) for goal in goals] == [
            ("Raised", HubGoal.Kind.MANUAL, 10_000, True, 0),
            ("Wine wall bottles", HubGoal.Kind.BOTTLES, 100, False, 0),
            ("Volunteer spots", HubGoal.Kind.VOLUNTEER_SPOTS, 82, False, 0),
            ("RSVPs", HubGoal.Kind.RSVPS, 250, False, 0),
        ]
        shifts = list(hub.shifts.all())
        assert [(shift.role, shift.when_text, shift.need) for shift in shifts] == [
            (spec.role, spec.when_text, spec.need) for spec in SHIFTS
        ]
        assert [shift.sort_order for shift in shifts if shift.phase == ShiftPhase.DURING] == list(range(13))
        assert [shift.role for shift in shifts if shift.requires_olcc] == [
            "Bartender, first half",
            "Bartender, second half",
        ]
        assert hub.shifts.get(role="Flyer runner").description.startswith("Drop flyers at SE Portland businesses")
        assert "Hub holiday-bizarre-2026 (Holiday Bizarre HQ): created, open." in out
        assert "Cards: 9 created, 0 kept as edited." in out
        assert "Goals: 4 created, 0 updated." in out
        assert "Shifts: 21 created, 0 updated." in out
        assert (
            f"Seeded for event {event.pk} (Holiday Bizarre): hub created; 9 cards, 4 goals, 21 shifts created; "
            "0 cards kept; 0 goals, 0 shifts updated."
        ) in out

    def it_leaves_the_new_hub_closed_while_another_hq_is_open(event, open_hub):
        out = _run("--event", str(event.pk))

        hub = EventHub.objects.get(slug=HUB.slug)
        assert hub.is_open is False
        open_hub.refresh_from_db()
        assert open_hub.is_open is True
        assert f"created, closed ({open_hub.slug} is open; open this one from Edit HQ)." in out

    def it_creates_nothing_the_second_time_and_keeps_what_admins_changed(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        before = (
            set(hub.cards.values_list("pk", flat=True)),
            set(hub.goals.values_list("pk", flat=True)),
            set(hub.shifts.values_list("pk", flat=True)),
        )
        rsvp = hub.cards.get(kind=HubCard.Kind.RSVP)
        rsvp.title = "Renamed by Lee"
        rsvp.audience = HubCard.Audience.PUBLIC
        rsvp.save(update_fields=["title", "audience"])
        ledger = hub.cards.get(title="PL Ledger")
        ledger.title = "The Ledger"
        ledger.save(update_fields=["title"])
        raised = hub.goals.get(label="Raised")
        raised.current_value = 2_500
        raised.save(update_fields=["current_value"])
        signup = ShiftSignupFactory(shift=hub.shifts.get(role="Flyer runner"))

        out = _run("--event", str(event.pk))

        assert _counts() == (1, 9, 4, 21)
        hub.refresh_from_db()
        assert hub.is_open is True
        assert (
            set(hub.cards.values_list("pk", flat=True)),
            set(hub.goals.values_list("pk", flat=True)),
            set(hub.shifts.values_list("pk", flat=True)),
        ) == before
        rsvp.refresh_from_db()
        assert (rsvp.title, rsvp.audience) == ("Renamed by Lee", HubCard.Audience.PUBLIC)
        assert hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).count() == 6
        assert not hub.cards.filter(title="PL Ledger").exists()
        raised.refresh_from_db()
        assert raised.current_value == 2_500
        assert ShiftSignup.objects.filter(pk=signup.pk).exists()
        assert "Hub holiday-bizarre-2026 (Holiday Bizarre HQ): updated." in out
        assert (
            "Cards: 0 created, 9 kept as edited. The six placeholders were already on the hub and were left as edited."
        ) in out
        assert "Goals: 0 created, 4 updated." in out
        assert "Shifts: 0 created, 21 updated." in out
        assert "hub updated; 0 cards, 0 goals, 0 shifts created; 9 cards kept; 4 goals, 21 shifts updated." in out

    def it_refreshes_the_words_goals_and_shifts_it_owns(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        hub.title = "Something else"
        hub.event = None
        hub.save(update_fields=["title", "event"])
        bottles = hub.goals.get(label="Wine wall bottles")
        bottles.target = 50
        bottles.save(update_fields=["target"])
        flyers = hub.shifts.get(role="Flyer runner")
        flyers.need = 2
        flyers.description = "Changed"
        flyers.save(update_fields=["need", "description"])

        _run("--event", str(event.pk))

        hub.refresh_from_db()
        assert (hub.title, hub.event) == (HUB.title, event)
        bottles.refresh_from_db()
        assert bottles.target == 100
        flyers.refresh_from_db()
        assert (flyers.need, flyers.description) == (6, SHIFTS[0].description)

    def it_adds_a_missing_live_door_without_touching_the_placeholders(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        hub.cards.get(kind=HubCard.Kind.WINE_WALL).delete()
        ledger = hub.cards.get(title="PL Ledger")
        ledger.title = "The Ledger"
        ledger.save(update_fields=["title"])

        out = _run("--event", str(event.pk))

        assert hub.cards.count() == 9
        assert hub.cards.filter(kind=HubCard.Kind.WINE_WALL).count() == 1
        assert not hub.cards.filter(title="PL Ledger").exists()
        assert "Cards: 1 created, 8 kept as edited." in out


def describe_refusals():
    def it_refuses_without_an_event(event):
        with pytest.raises(CommandError, match=r"--event <pk> is required") as excinfo:
            _run()

        assert f"Published upcoming events: {event.pk} Holiday Bizarre (" in str(excinfo.value)
        assert _counts() == (0, 0, 0, 0)

    def it_lists_only_published_upcoming_events_to_pick_from(event):
        CommunityEventFactory(community=True, title="Last Winter", starts_at=timezone.now() - timedelta(days=300))
        CommunityEventFactory(community=True, title="Still Pending", pending=True)

        with pytest.raises(CommandError) as excinfo:
            _run("--event", "999999")

        text = str(excinfo.value)
        assert text.startswith("No calendar event has pk 999999.")
        assert f"{event.pk} Holiday Bizarre (" in text
        assert "Last Winter" not in text
        assert "Still Pending" not in text

    def it_says_where_to_make_the_event_when_none_is_published():
        with pytest.raises(CommandError, match=r"make the Holiday Bizarre at /events/add/ first"):
            _run()

    def it_refuses_an_event_that_is_not_published():
        pending = CommunityEventFactory(community=True, title="Not Yet", pending=True)

        with pytest.raises(CommandError, match=rf"Event {pending.pk} \(Not Yet\) is pending review, not published"):
            _run("--event", str(pending.pk))

        assert _counts() == (0, 0, 0, 0)


def describe_a_dry_run():
    def it_writes_nothing_and_reports_the_counts(event):
        out = _run("--event", str(event.pk), "--dry-run")

        assert _counts() == (0, 0, 0, 0)
        assert "Hub holiday-bizarre-2026 (Holiday Bizarre HQ): created, open." in out
        assert (
            f"Dry run for event {event.pk} (Holiday Bizarre), rolled back: hub would be created; "
            "9 cards, 4 goals, 21 shifts created; 0 cards kept; 0 goals, 0 shifts updated."
        ) in out

    def it_leaves_an_existing_hub_as_it_is(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        hub.title = "Edited by hand"
        hub.save(update_fields=["title"])

        out = _run("--event", str(event.pk), "--dry-run")

        hub.refresh_from_db()
        assert hub.title == "Edited by hand"
        assert _counts() == (1, 9, 4, 21)
        assert "rolled back: hub would be updated; 0 cards, 0 goals, 0 shifts created; 9 cards kept" in out


def describe_the_seeded_hq():
    def it_renders_for_an_admin_with_nine_doors_four_goals_and_the_countdown(event, admin_client: Client):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)

        body = admin_client.get(hub.get_absolute_url()).content.decode()

        assert body.count("data-hq-card-pk=") == 9
        assert body.count("data-hq-goal>") == 4
        assert 'data-hq-countdown="7 days to go"' in body
        assert "data-hq-footnote" in body
