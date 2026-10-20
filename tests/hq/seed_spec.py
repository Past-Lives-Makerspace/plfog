"""The seed_holiday_bizarre command (Track C, part 4): Lee's hub, its nine doors, four goals and 21 shifts,
written from hq.holiday_bizarre for a published calendar event. It runs twice without duplicates and never
touches an admin's edits; a dry run writes nothing."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone

from hq.holiday_bizarre import CARDS, GOALS, HUB, SHIFTS
from hq.models import (
    BUILT_DOORS,
    DEFAULT_CARDS,
    CardSeed,
    EventHub,
    HubCard,
    HubGoal,
    ShiftPhase,
    ShiftSignup,
    VolunteerShift,
)
from membership.models import CommunityEvent
from tests.hq.factories import (
    EventHubFactory,
    HubCardFactory,
    HubGoalFactory,
    MarketingActionFactory,
    ShiftSignupFactory,
    VolunteerShiftFactory,
)
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

    def it_carries_lees_current_words_on_every_door():
        assert [(door.title, door.button_text) for door in CARDS] == [
            ("Going to the Bizarre?", "I'm going"),
            ("Volunteer Sign-up", "Sign up to volunteer"),
            ("Log a wine bottle", "Enter a bottle"),
            ("PL Ledger", "Enter the ledger"),
            ("Sponsorship Tracker", "Enter sponsorships"),
            ("Market Check-in", "Enter check-in"),
            ("Vendor Tables", "Enter vendor tables"),
            ("Event flyer and sharing", "See the flyer"),
            ("Marketing Tracker", "Enter marketing"),
        ]
        marketing = CARDS[-1]
        assert marketing.bullets.splitlines() == [
            "Businesses with flyers",
            "Instagram videos and posts",
            "Emails and event listings",
            "Every action, with views and links",
        ]
        assert all(len(door.bullets.splitlines()) <= HubCard.MAX_BULLETS for door in CARDS)

    def it_seeds_a_built_door_live_and_the_rest_as_placeholders():
        assert BUILT_DOORS == frozenset({HubCard.Kind.MARKETING, HubCard.Kind.SPONSORSHIP})
        assert [door.kind for door in CARDS] == [
            HubCard.Kind.RSVP,
            HubCard.Kind.VOLUNTEERS,
            HubCard.Kind.WINE_WALL,
            HubCard.Kind.PLACEHOLDER,
            HubCard.Kind.SPONSORSHIP,
            HubCard.Kind.PLACEHOLDER,
            HubCard.Kind.PLACEHOLDER,
            HubCard.Kind.PLACEHOLDER,
            HubCard.Kind.MARKETING,
        ]
        assert [door.door for door in CARDS][3:8] == [
            HubCard.Kind.LEDGER,
            HubCard.Kind.SPONSORSHIP,
            HubCard.Kind.MARKET_CHECKIN,
            HubCard.Kind.VENDOR_TABLES,
            HubCard.Kind.FLYER,
        ]

    def it_tells_a_new_hub_who_to_ask_for_a_way_in():
        assert HUB.footer_note.endswith(
            "Each page opens only for people Lee has added. Can't get in? Email lee@pastlives.space."
        )

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
        assert [card.kind for card in cards] == [door.kind for door in CARDS]
        assert [card.title for card in cards] == [door.title for door in CARDS]
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
        assert "Goals: 4 created, 0 kept as edited." in out
        assert "Shifts: 21 created, 0 kept as edited." in out
        assert (
            f"Seeded for event {event.pk} (Holiday Bizarre): hub created; 9 cards, 4 goals, 21 shifts created; "
            "0 cards, 0 goals, 0 shifts kept."
        ) in out

    def it_leaves_the_new_hub_closed_while_another_hq_is_open(event, open_hub):
        out = _run("--event", str(event.pk))

        hub = EventHub.objects.get(slug=HUB.slug)
        assert hub.is_open is False
        open_hub.refresh_from_db()
        assert open_hub.is_open is True
        assert f"created, closed ({open_hub.slug} is open; open this one from Edit HQ)." in out

    def it_creates_nothing_the_second_time_and_keeps_every_edit(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        before = (
            set(hub.cards.values_list("pk", flat=True)),
            set(hub.goals.values_list("pk", flat=True)),
            set(hub.shifts.values_list("pk", flat=True)),
        )
        hub.title = "Lee's HQ"
        hub.intro = "Doors at five."
        hub.is_open = False
        hub.save(update_fields=["title", "intro", "is_open"])
        rsvp = hub.cards.get(kind=HubCard.Kind.RSVP)
        rsvp.title = "Renamed by Lee"
        rsvp.audience = HubCard.Audience.PUBLIC
        rsvp.save(update_fields=["title", "audience"])
        ledger = hub.cards.get(title="PL Ledger")
        ledger.title = "The Ledger"
        ledger.save(update_fields=["title"])
        rsvps = hub.goals.get(label="RSVPs")
        rsvps.label = "People going"
        rsvps.save(update_fields=["label"])
        raised = hub.goals.get(label="Raised")
        raised.current_value = 2_500
        raised.save(update_fields=["current_value"])
        setup = hub.shifts.get(role="Setup crew")
        setup.when_text = "Fri Dec 4, noon to 4"
        setup.save(update_fields=["when_text"])
        flyers = hub.shifts.get(role="Flyer runner")
        flyers.need = 10
        flyers.save(update_fields=["need"])
        photo = hub.shifts.get(role="Photo and video")
        photo_pk = photo.pk
        photo.delete()
        signup = ShiftSignupFactory(shift=flyers)

        out = _run("--event", str(event.pk))

        assert _counts() == (1, 9, 4, 20)
        hub.refresh_from_db()
        assert (hub.title, hub.intro, hub.is_open, hub.event) == ("Lee's HQ", "Doors at five.", False, event)
        assert len(before[2]) == 21
        assert (
            set(hub.cards.values_list("pk", flat=True)),
            set(hub.goals.values_list("pk", flat=True)),
            set(hub.shifts.values_list("pk", flat=True)),
        ) == (before[0], before[1], before[2] - {photo_pk})
        rsvp.refresh_from_db()
        assert (rsvp.title, rsvp.audience) == ("Renamed by Lee", HubCard.Audience.PUBLIC)
        assert hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).count() == 4
        assert not hub.cards.filter(title="PL Ledger").exists()
        assert sorted(hub.goals.values_list("label", flat=True)) == [
            "People going",
            "Raised",
            "Volunteer spots",
            "Wine wall bottles",
        ]
        raised.refresh_from_db()
        assert raised.current_value == 2_500
        assert hub.shifts.filter(role="Setup crew").count() == 1
        flyers.refresh_from_db()
        assert flyers.need == 10
        assert not hub.shifts.filter(role="Photo and video").exists()
        assert ShiftSignup.objects.filter(pk=signup.pk).exists()
        assert "Hub holiday-bizarre-2026 (Lee's HQ): kept as edited." in out
        assert (
            "Cards: 0 created, 9 kept as edited. The hub already held its placeholders, so none was added and they were left as edited."
        ) in out
        assert "Goals: 0 created, 4 kept as edited." in out
        assert "Shifts: 0 created, 20 kept as edited." in out
        assert "hub kept; 0 cards, 0 goals, 0 shifts created; 9 cards, 4 goals, 20 shifts kept." in out

    def it_moves_an_existing_hub_to_the_event_named_and_says_so(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        hub.title = "Lee's HQ"
        hub.save(update_fields=["title"])
        other = CommunityEventFactory(community=True, title="Holiday Bizarre 2027")

        out = _run("--event", str(other.pk))

        hub.refresh_from_db()
        assert (hub.title, hub.event) == ("Lee's HQ", other)
        assert _counts() == (1, 9, 4, 21)
        assert (
            f"Hub holiday-bizarre-2026 (Lee's HQ): kept as edited, event changed to {other.pk} (Holiday Bizarre 2027)."
            in out
        )
        assert f"Seeded for event {other.pk} (Holiday Bizarre 2027): hub kept, event changed; 0 cards" in out

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

    def it_adds_goals_and_shifts_only_to_a_hub_that_holds_none(event):
        hub = EventHubFactory(slug=HUB.slug, title="Lee's HQ", event=event)
        HubGoalFactory(hub=hub, label="Raised")
        VolunteerShiftFactory(hub=hub, role="Flyer runner")

        out = _run("--event", str(event.pk))

        assert _counts() == (1, 9, 1, 1)
        assert "Cards: 9 created, 0 kept as edited." in out
        assert "Goals: 0 created, 1 kept as edited." in out
        assert "Shifts: 0 created, 1 kept as edited." in out

    def it_adds_no_placeholder_once_the_hub_holds_any_even_renamed_or_deleted(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        hub.cards.get(title="Vendor Tables").delete()
        ledger = hub.cards.get(title="PL Ledger")
        ledger.title = "The Ledger"
        ledger.sort_order = 40
        ledger.save(update_fields=["title", "sort_order"])
        rsvp = hub.cards.get(kind=HubCard.Kind.RSVP)
        rsvp.title = "Coming?"
        rsvp.save(update_fields=["title"])

        out = _run("--event", str(event.pk))

        assert hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).count() == 3
        assert not hub.cards.filter(title__in=["PL Ledger", "Vendor Tables"]).exists()
        assert hub.cards.filter(kind=HubCard.Kind.RSVP).get().title == "Coming?"
        assert "Cards: 0 created, 8 kept as edited. The hub already held its placeholders" in out


def _track_c_hub(event: CommunityEvent) -> EventHub:
    """Lee's hub as the Track C seed left it: three live doors and six placeholders, all in the old words."""
    hub = EventHubFactory(slug=HUB.slug, title=HUB.title, footer_note="The old footer.", event=event, is_open=True)
    first_doors = {HubCard.Kind.RSVP, HubCard.Kind.VOLUNTEERS, HubCard.Kind.WINE_WALL}
    for position, door in enumerate(CARDS):
        HubCardFactory(
            hub=hub,
            kind=door.door if door.door in first_doors else HubCard.Kind.PLACEHOLDER,
            who_for=door.who_for,
            title=door.title,
            bullets=door.old_bullets,
            button_text=door.old_button_text,
            sort_order=position,
        )
    return hub


def describe_adopting_a_built_door():
    def it_turns_the_marketing_placeholder_into_the_live_door_in_place(event, member):
        hub = _track_c_hub(event)
        placeholder = hub.cards.get(title="Marketing Tracker")
        placeholder.audience = HubCard.Audience.MEMBERS
        placeholder.is_hidden = True
        placeholder.sort_order = 2
        placeholder.save(update_fields=["audience", "is_hidden", "sort_order"])
        placeholder.named_members.set([member])

        out = _run("--event", str(event.pk))

        card = HubCard.objects.get(pk=placeholder.pk)
        assert card.kind == HubCard.Kind.MARKETING
        assert (card.title, card.audience, card.is_hidden, card.sort_order) == (
            "Marketing Tracker",
            HubCard.Audience.MEMBERS,
            True,
            2,
        )
        assert list(card.named_members.all()) == [member]
        assert hub.cards.count() == 9
        assert hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).count() == 4
        assert (
            "Cards: 0 created, 7 kept as edited. Opened as live doors: Sponsorship Tracker, Marketing Tracker." in out
        )

    def it_gives_every_untouched_door_lees_latest_words(event):
        hub = _track_c_hub(event)

        out = _run("--event", str(event.pk))

        for door in CARDS:
            card = hub.cards.get(title=door.title)
            assert (card.bullets, card.button_text) == (door.bullets, door.button_text), door.title
        assert "9 took Lee's latest words." in out

    def it_keeps_words_an_admin_edited_and_replaces_only_the_untouched_field(event):
        hub = _track_c_hub(event)
        marketing = hub.cards.get(title="Marketing Tracker")
        marketing.bullets = "Lee's own bullet"
        marketing.save(update_fields=["bullets"])
        ledger = hub.cards.get(title="PL Ledger")
        ledger.button_text = "Open it"
        ledger.save(update_fields=["button_text"])
        rsvp = hub.cards.get(kind=HubCard.Kind.RSVP)
        rsvp.title = "Coming?"
        rsvp.save(update_fields=["title"])

        _run("--event", str(event.pk))

        marketing.refresh_from_db()
        ledger.refresh_from_db()
        rsvp.refresh_from_db()
        assert (marketing.kind, marketing.bullets, marketing.button_text) == (
            HubCard.Kind.MARKETING,
            "Lee's own bullet",
            "Enter marketing",
        )
        assert (ledger.bullets, ledger.button_text) == (CARDS[3].bullets, "Open it")
        assert (rsvp.title, rsvp.bullets) == ("Coming?", CARDS[0].bullets)

    def it_matches_old_words_whatever_the_spacing_of_the_lines(event):
        hub = _track_c_hub(event)
        ledger = hub.cards.get(title="PL Ledger")
        ledger.bullets = "\n  ".join(CARDS[3].old_bullets.splitlines()) + "\n\n"
        ledger.save(update_fields=["bullets"])

        _run("--event", str(event.pk))

        ledger.refresh_from_db()
        assert ledger.bullets == CARDS[3].bullets

    def it_does_nothing_the_second_time(event):
        hub = _track_c_hub(event)
        _run("--event", str(event.pk))
        pks = set(hub.cards.values_list("pk", flat=True))
        MarketingActionFactory(hub=hub)

        out = _run("--event", str(event.pk))

        assert set(hub.cards.values_list("pk", flat=True)) == pks
        assert hub.cards.filter(kind=HubCard.Kind.MARKETING).count() == 1
        assert "Cards: 0 created, 9 kept as edited. The hub already held its placeholders" in out
        assert "Opened as live doors" not in out
        assert "latest words" not in out
        assert hub.marketing_actions.count() == 1

    def it_makes_the_live_door_when_neither_the_kind_nor_its_placeholder_exists(event):
        hub = _track_c_hub(event)
        hub.cards.get(title="Marketing Tracker").delete()

        out = _run("--event", str(event.pk))

        card = hub.cards.get(kind=HubCard.Kind.MARKETING)
        assert (card.title, card.bullets, card.audience, card.sort_order) == (
            "Marketing Tracker",
            CARDS[-1].bullets,
            HubCard.Audience.ADMINS,
            8,
        )
        assert hub.cards.count() == 9
        assert "Cards: 1 created, 7 kept as edited." in out

    def it_does_not_adopt_a_renamed_placeholder_and_makes_the_live_door_beside_it(event):
        """A placeholder is matched by its title alone: one renamed is no longer the door's, so the door is made."""
        hub = _track_c_hub(event)
        renamed = hub.cards.get(title="Marketing Tracker")
        renamed.title = "Getting the word out"
        renamed.save(update_fields=["title"])

        _run("--event", str(event.pk))

        renamed.refresh_from_db()
        assert renamed.kind == HubCard.Kind.PLACEHOLDER
        assert hub.cards.filter(kind=HubCard.Kind.MARKETING).count() == 1

    def it_brings_back_no_placeholder_an_admin_deleted(event):
        hub = _track_c_hub(event)
        hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).delete()

        hub.seed_default_cards()
        result = hub.seed_default_cards()

        assert not hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).exists()
        assert hub.cards.filter(kind=HubCard.Kind.MARKETING).count() == 1
        assert result.summary == "The doors are up to date."

    def it_names_a_live_door_made_beside_a_renamed_placeholder(event):
        hub = _track_c_hub(event)
        hub.cards.filter(title="Marketing Tracker").update(title="Getting the word out")

        summary = hub.seed_default_cards().summary

        assert "added 1 door;" in summary
        assert summary.endswith(
            "New and admins only until you open it: Marketing Tracker."
            " If an old Not shared yet card for it is still on the page, delete that one."
        )

    def it_leaves_a_live_door_alone_when_a_placeholder_shares_its_title(event):
        hub = _track_c_hub(event)
        _run("--event", str(event.pk))
        stray = HubCardFactory(hub=hub, kind=HubCard.Kind.PLACEHOLDER, title="Marketing Tracker")

        _run("--event", str(event.pk))

        stray.refresh_from_db()
        assert stray.kind == HubCard.Kind.PLACEHOLDER
        assert hub.cards.filter(kind=HubCard.Kind.MARKETING).count() == 1

    def it_never_writes_the_footer_on_an_existing_hub(event):
        hub = _track_c_hub(event)

        _run("--event", str(event.pk))

        hub.refresh_from_db()
        assert hub.footer_note == "The old footer."


def describe_the_card_seed_summary():
    def it_says_the_doors_are_up_to_date_when_nothing_changed():
        assert CardSeed(kept=9).summary == "The doors are up to date."

    def it_names_what_opened_was_added_and_reworded():
        assert CardSeed(adopted=["Marketing Tracker"], created=1, reworded=1).summary == (
            "Doors updated: opened Marketing Tracker; added 1 door; gave 1 card Lee's latest words."
        )
        assert CardSeed(adopted=["A", "B"], created=2, reworded=9).summary == (
            "Doors updated: opened A, B; added 2 doors; gave 9 cards Lee's latest words."
        )


def describe_refusals():
    def it_seeds_with_no_event_when_none_is_named():
        out = _run()

        hub = EventHub.objects.get(slug=HUB.slug)
        assert hub.event is None
        assert _counts() == (1, 9, 4, 21)
        assert "Seeded for no event" in out

    def it_leaves_an_existing_hubs_event_alone_when_none_is_named(event):
        _run("--event", str(event.pk))

        _run()

        assert EventHub.objects.get(slug=HUB.slug).event_id == event.pk

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
            _run("--event", "999999")

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
            "9 cards, 4 goals, 21 shifts created; 0 cards, 0 goals, 0 shifts kept."
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
        assert (
            "rolled back: hub would be kept; 0 cards, 0 goals, 0 shifts created; 9 cards, 4 goals, 21 shifts kept."
            in out
        )


def describe_the_seeded_hq():
    def it_renders_for_an_admin_with_nine_doors_four_goals_and_the_countdown(event, admin_client: Client):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)

        body = admin_client.get(hub.get_absolute_url()).content.decode()

        assert body.count("data-hq-card-pk=") == 9
        assert body.count("data-hq-goal>") == 4
        assert 'data-hq-countdown="7 days to go"' in body
        assert "data-hq-footnote" in body
