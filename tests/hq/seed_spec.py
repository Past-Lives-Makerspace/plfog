"""The seed_holiday_bizarre command (Track C, part 4): Lee's hub, its four doors (#1307), five goals and 21 shifts,
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
    ShiftSignupFactory,
    VendorTableFactory,
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
    def it_is_the_hub_four_doors_five_goals_and_twenty_one_shifts():
        assert HUB.slug == "holiday-bizarre-2026"
        assert CARDS is DEFAULT_CARDS
        assert len(CARDS) == 4
        assert [goal.label for goal in GOALS] == [
            "Raised",
            "Volunteer spots",
            "RSVPs",
            "Member tables",
            "Guilds taking part",
        ]
        assert len(SHIFTS) == 21

    def it_asks_for_as_many_volunteers_as_the_spots_goal():
        spots = next(goal for goal in GOALS if goal.kind == HubGoal.Kind.VOLUNTEER_SPOTS)
        assert sum(shift.need for shift in SHIFTS) == spots.target == 82

    def it_counts_the_raised_goal_itself_in_whole_dollars():
        raised = GOALS[0]
        assert (raised.label, raised.kind, raised.is_money, raised.target) == (
            "Raised",
            HubGoal.Kind.EVENT_CASH,
            True,
            10_000,
        )

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
            ("PL Ledger", "Enter the ledger"),
            ("Vendor Tables", "Enter vendor tables"),
        ]
        assert all(len(door.bullets.splitlines()) <= HubCard.MAX_BULLETS for door in CARDS)

    def it_names_no_removed_door_on_the_ledger_card():
        ledger = next(door for door in CARDS if door.door == HubCard.Kind.LEDGER)

        assert ledger.bullets.splitlines() == [
            "Running tally and goals",
            "$900 drinks budget and receipts",
            "Drink sales, the door count, vendors",
            "Guild demos, staff time, board summary",
        ]
        # The words before #1307, so a card still carrying them takes the new ones.
        assert ledger.old_bullets.splitlines()[2:] == [
            "Drink sales, wine wall, vendors",
            "Guild demos, flyers, board summary",
        ]

    def it_seeds_the_four_doors_live_and_no_removed_one():
        assert BUILT_DOORS == frozenset({HubCard.Kind.VENDOR_TABLES, HubCard.Kind.LEDGER})
        assert [door.kind for door in CARDS] == [
            HubCard.Kind.RSVP,
            HubCard.Kind.VOLUNTEERS,
            HubCard.Kind.LEDGER,
            HubCard.Kind.VENDOR_TABLES,
        ]
        assert set(HubCard.Kind.values) == {"rsvp", "volunteers", "vendor_tables", "ledger", "placeholder"}

    def it_tells_a_new_hub_the_hq_is_for_guild_officers():
        assert HUB.footer_note.endswith(
            "This HQ is for guild officers: everyone who leads or staffs a guild opens every page here."
        )
        assert "lee@" not in HUB.footer_note

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
        assert [card.sort_order for card in cards] == list(range(4))
        assert not any(card.is_hidden for card in cards)
        goals = list(hub.goals.all())
        assert [(goal.label, goal.kind, goal.target, goal.is_money, goal.current_value) for goal in goals] == [
            ("Raised", HubGoal.Kind.EVENT_CASH, 10_000, True, 0),
            ("Volunteer spots", HubGoal.Kind.VOLUNTEER_SPOTS, 82, False, 0),
            ("RSVPs", HubGoal.Kind.RSVPS, 250, False, 0),
            ("Member tables", HubGoal.Kind.MEMBER_TABLES, 0, False, 0),
            ("Guilds taking part", HubGoal.Kind.GUILDS, 0, False, 0),
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
        assert "Cards: 4 created, 0 kept as edited." in out
        assert "Goals: 5 created, 0 kept as edited." in out
        assert "Shifts: 21 created, 0 kept as edited." in out
        assert (
            f"Seeded for event {event.pk} (Holiday Bizarre): hub created; 4 cards, 5 goals, 21 shifts created; "
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
        rsvp.save(update_fields=["title"])
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

        assert _counts() == (1, 4, 5, 20)
        hub.refresh_from_db()
        assert (hub.title, hub.intro, hub.is_open, hub.event) == ("Lee's HQ", "Doors at five.", False, event)
        assert len(before[2]) == 21
        assert (
            set(hub.cards.values_list("pk", flat=True)),
            set(hub.goals.values_list("pk", flat=True)),
            set(hub.shifts.values_list("pk", flat=True)),
        ) == (before[0], before[1], before[2] - {photo_pk})
        rsvp.refresh_from_db()
        assert rsvp.title == "Renamed by Lee"
        assert not hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).exists()
        assert hub.cards.get(kind=HubCard.Kind.LEDGER).title == "The Ledger"
        assert not hub.cards.filter(title="PL Ledger").exists()
        assert sorted(hub.goals.values_list("label", flat=True)) == [
            "Guilds taking part",
            "Member tables",
            "People going",
            "Raised",
            "Volunteer spots",
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
            "Cards: 0 created, 4 kept as edited. The hub already held its placeholders, so none was added and they were left as edited."
        ) in out
        assert "Goals: 0 created, 5 kept as edited." in out
        assert "Shifts: 0 created, 20 kept as edited." in out
        assert "hub kept; 0 cards, 0 goals, 0 shifts created; 4 cards, 5 goals, 20 shifts kept." in out

    def it_moves_an_existing_hub_to_the_event_named_and_says_so(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        hub.title = "Lee's HQ"
        hub.save(update_fields=["title"])
        other = CommunityEventFactory(community=True, title="Holiday Bizarre 2027")

        out = _run("--event", str(other.pk))

        hub.refresh_from_db()
        assert (hub.title, hub.event) == ("Lee's HQ", other)
        assert _counts() == (1, 4, 5, 21)
        assert (
            f"Hub holiday-bizarre-2026 (Lee's HQ): kept as edited, event changed to {other.pk} (Holiday Bizarre 2027)."
            in out
        )
        assert f"Seeded for event {other.pk} (Holiday Bizarre 2027): hub kept, event changed; 0 cards" in out

    def it_adds_a_missing_live_door_without_touching_the_placeholders(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        hub.cards.get(kind=HubCard.Kind.VOLUNTEERS).delete()
        ledger = hub.cards.get(title="PL Ledger")
        ledger.title = "The Ledger"
        ledger.save(update_fields=["title"])

        out = _run("--event", str(event.pk))

        assert hub.cards.count() == 4
        assert hub.cards.filter(kind=HubCard.Kind.VOLUNTEERS).count() == 1
        assert not hub.cards.filter(title="PL Ledger").exists()
        assert "Cards: 1 created, 3 kept as edited." in out

    def it_adds_only_the_counted_goals_to_a_hub_that_holds_some_and_no_shifts_to_one_that_holds_any(event):
        hub = EventHubFactory(slug=HUB.slug, title="Lee's HQ", event=event)
        HubGoalFactory(hub=hub, label="Money in", kind=HubGoal.Kind.MANUAL, sort_order=7)
        VolunteerShiftFactory(hub=hub, role="Flyer runner")

        out = _run("--event", str(event.pk))

        assert _counts() == (1, 4, 6, 1)
        assert list(hub.goals.values_list("label", "sort_order")) == [
            ("Money in", 7),
            ("Raised", 8),
            ("Volunteer spots", 9),
            ("RSVPs", 10),
            ("Member tables", 11),
            ("Guilds taking part", 12),
        ]
        # "Money in" is no Raised, so it is left typed in and the counted Raised is added beside it.
        assert hub.goals.get(label="Money in").kind == HubGoal.Kind.MANUAL
        assert hub.goals.get(label="Raised").kind == HubGoal.Kind.EVENT_CASH
        assert "Cards: 4 created, 0 kept as edited." in out
        assert (
            "Goals: 5 created, 1 kept as edited. "
            "Added: Raised, Volunteer spots, RSVPs, Member tables, Guilds taking part."
        ) in out
        assert "Shifts: 0 created, 1 kept as edited." in out

    def it_gives_a_track_c_hub_its_member_tables_and_guilds_goals_once(event):
        hub = EventHubFactory(slug=HUB.slug, title="Lee's HQ", event=event)
        for position, spec in enumerate(GOALS[:3]):
            HubGoalFactory(hub=hub, label=spec.label, kind=spec.kind, target=spec.target, sort_order=position)
        hub.goals.filter(kind=HubGoal.Kind.RSVPS).update(label="People going")

        out = _run("--event", str(event.pk))
        again = _run("--event", str(event.pk))

        assert list(hub.goals.values_list("label", flat=True)) == [
            "Raised",
            "Volunteer spots",
            "People going",
            "Member tables",
            "Guilds taking part",
        ]
        assert "Goals: 2 created, 3 kept as edited. Added: Member tables, Guilds taking part." in out
        assert "Goals: 0 created, 5 kept as edited." in again

    def it_still_seeds_a_door_whose_page_is_not_built_as_a_placeholder(event, monkeypatch):
        """The rail for a door not yet built stays: with the ledger taken out of the built doors, a new hub gets it as a
        placeholder, and a hub that holds cards but not that placeholder is not given one."""
        monkeypatch.setattr("hq.models.BUILT_DOORS", BUILT_DOORS - {HubCard.Kind.LEDGER})

        _run("--event", str(event.pk))

        hub = EventHub.objects.get(slug=HUB.slug)
        assert list(hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).values_list("title", flat=True)) == ["PL Ledger"]
        assert "Cards: 0 created, 4 kept as edited." in _run("--event", str(event.pk))
        hub.cards.get(title="PL Ledger").delete()

        out = _run("--event", str(event.pk))

        assert not hub.cards.filter(title="PL Ledger").exists()
        assert "Cards: 0 created, 3 kept as edited." in out

    def it_adds_no_placeholder_and_keeps_one_an_admin_made_and_a_renamed_door(event):
        """Every one of Lee's doors is live now (D6a built the last), so a seeded hub holds no placeholder; one an
        admin made stays as made, and a renamed door is still found by its kind."""
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        assert not hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).exists()
        ledger = hub.cards.get(title="PL Ledger")
        ledger.title = "The Ledger"
        ledger.sort_order = 40
        ledger.save(update_fields=["title", "sort_order"])
        rsvp = hub.cards.get(kind=HubCard.Kind.RSVP)
        rsvp.title = "Coming?"
        rsvp.save(update_fields=["title"])
        HubCardFactory(hub=hub, kind=HubCard.Kind.PLACEHOLDER, title="Coming next year")

        out = _run("--event", str(event.pk))

        assert list(hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).values_list("title", flat=True)) == [
            "Coming next year"
        ]
        assert hub.cards.get(kind=HubCard.Kind.LEDGER).title == "The Ledger"
        assert not hub.cards.filter(title="PL Ledger").exists()
        assert hub.cards.filter(kind=HubCard.Kind.RSVP).get().title == "Coming?"
        assert "Cards: 0 created, 5 kept as edited. The hub already held its placeholders" in out


def _track_c_hub(event: CommunityEvent) -> EventHub:
    """Lee's hub as the Track C seed left it, for the doors #1307 kept: two live doors and two placeholders, all in the
    old words."""
    hub = EventHubFactory(slug=HUB.slug, title=HUB.title, footer_note="The old footer.", event=event, is_open=True)
    first_doors = {HubCard.Kind.RSVP, HubCard.Kind.VOLUNTEERS}
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
    def it_turns_the_vendor_tables_placeholder_into_the_live_door_in_place(event):
        hub = _track_c_hub(event)
        placeholder = hub.cards.get(title="Vendor Tables")
        placeholder.is_hidden = True
        placeholder.sort_order = 1
        placeholder.save(update_fields=["is_hidden", "sort_order"])

        out = _run("--event", str(event.pk))

        card = HubCard.objects.get(pk=placeholder.pk)
        assert card.kind == HubCard.Kind.VENDOR_TABLES
        assert (card.title, card.is_hidden, card.sort_order) == ("Vendor Tables", True, 1)
        assert hub.cards.count() == 4
        assert not hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).exists()
        assert "Cards: 0 created, 2 kept as edited. Opened as live doors: PL Ledger, Vendor Tables." in out

    def it_gives_every_untouched_door_lees_latest_words(event):
        hub = _track_c_hub(event)

        out = _run("--event", str(event.pk))

        for door in CARDS:
            card = hub.cards.get(title=door.title)
            assert (card.bullets, card.button_text) == (door.bullets, door.button_text), door.title
        assert "4 took Lee's latest words." in out

    def it_keeps_words_an_admin_edited_and_replaces_only_the_untouched_field(event):
        hub = _track_c_hub(event)
        vendors = hub.cards.get(title="Vendor Tables")
        vendors.bullets = "Lee's own bullet"
        vendors.save(update_fields=["bullets"])
        ledger = hub.cards.get(title="PL Ledger")
        ledger.button_text = "Open it"
        ledger.save(update_fields=["button_text"])
        rsvp = hub.cards.get(kind=HubCard.Kind.RSVP)
        rsvp.title = "Coming?"
        rsvp.save(update_fields=["title"])

        _run("--event", str(event.pk))

        vendors.refresh_from_db()
        ledger.refresh_from_db()
        rsvp.refresh_from_db()
        assert (vendors.kind, vendors.bullets, vendors.button_text) == (
            HubCard.Kind.VENDOR_TABLES,
            "Lee's own bullet",
            "Enter vendor tables",
        )
        assert (ledger.bullets, ledger.button_text) == (CARDS[2].bullets, "Open it")
        assert (rsvp.title, rsvp.bullets) == ("Coming?", CARDS[0].bullets)

    def it_moves_the_ledgers_who_line_from_lee_to_guild_officers_unless_an_officer_edited_it(event):
        hub = _track_c_hub(event)
        ledger = hub.cards.get(title="PL Ledger")
        ledger.who_for = "Lee and the core team"
        ledger.save(update_fields=["who_for"])
        vendors = hub.cards.get(title="Vendor Tables")
        vendors.who_for = "Kate's crew"
        vendors.save(update_fields=["who_for"])

        first = hub.seed_default_cards()
        again = hub.seed_default_cards()

        ledger.refresh_from_db()
        vendors.refresh_from_db()
        assert (CARDS[2].who_for, CARDS[2].old_who_for) == ("Guild officers", "Lee and the core team")
        assert ledger.who_for == "Guild officers"
        assert vendors.who_for == "Kate's crew"
        assert first.reworded >= 1
        assert again.reworded == 0

    def it_never_fills_a_who_line_an_officer_cleared(event):
        hub = _track_c_hub(event)
        rsvp = hub.cards.get(kind=HubCard.Kind.RSVP)
        rsvp.who_for = ""
        rsvp.save(update_fields=["who_for"])

        hub.seed_default_cards()

        rsvp.refresh_from_db()
        assert rsvp.who_for == ""

    def it_matches_old_words_whatever_the_spacing_of_the_lines(event):
        hub = _track_c_hub(event)
        ledger = hub.cards.get(title="PL Ledger")
        ledger.bullets = "\n  ".join(CARDS[2].old_bullets.splitlines()) + "\n\n"
        ledger.save(update_fields=["bullets"])

        _run("--event", str(event.pk))

        ledger.refresh_from_db()
        assert ledger.bullets == CARDS[2].bullets

    def it_does_nothing_the_second_time(event):
        hub = _track_c_hub(event)
        _run("--event", str(event.pk))
        pks = set(hub.cards.values_list("pk", flat=True))
        VendorTableFactory(hub=hub)

        out = _run("--event", str(event.pk))

        assert set(hub.cards.values_list("pk", flat=True)) == pks
        assert hub.cards.filter(kind=HubCard.Kind.VENDOR_TABLES).count() == 1
        assert "Cards: 0 created, 4 kept as edited. The hub already held its placeholders" in out
        assert "Opened as live doors" not in out
        assert "latest words" not in out
        assert hub.vendor_tables.count() == 1

    def it_makes_the_live_door_when_neither_the_kind_nor_its_placeholder_exists(event):
        hub = _track_c_hub(event)
        hub.cards.get(title="Vendor Tables").delete()

        out = _run("--event", str(event.pk))

        card = hub.cards.get(kind=HubCard.Kind.VENDOR_TABLES)
        assert (card.title, card.bullets, card.is_hidden, card.sort_order) == (
            "Vendor Tables",
            CARDS[-1].bullets,
            False,
            3,
        )
        assert hub.cards.count() == 4
        assert "Cards: 1 created, 2 kept as edited." in out

    def it_does_not_adopt_a_renamed_placeholder_and_makes_the_live_door_beside_it(event):
        """A placeholder is matched by its title alone: one renamed is no longer the door's, so the door is made."""
        hub = _track_c_hub(event)
        renamed = hub.cards.get(title="Vendor Tables")
        renamed.title = "Getting the word out"
        renamed.save(update_fields=["title"])

        _run("--event", str(event.pk))

        renamed.refresh_from_db()
        assert renamed.kind == HubCard.Kind.PLACEHOLDER
        assert hub.cards.filter(kind=HubCard.Kind.VENDOR_TABLES).count() == 1

    def it_brings_back_no_placeholder_an_admin_deleted(event):
        hub = _track_c_hub(event)
        hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).delete()

        hub.seed_default_cards()
        result = hub.seed_default_cards()

        assert not hub.cards.filter(kind=HubCard.Kind.PLACEHOLDER).exists()
        assert hub.cards.filter(kind=HubCard.Kind.VENDOR_TABLES).count() == 1
        assert result.summary == "The doors are up to date."

    def it_names_a_live_door_made_beside_a_renamed_placeholder(event):
        hub = _track_c_hub(event)
        hub.cards.filter(title="Vendor Tables").update(title="Getting the word out")

        summary = hub.seed_default_cards().summary

        assert "added 1 door;" in summary
        assert summary.endswith(
            "New: Vendor Tables. If an old Not shared yet card for it is still on the page, delete that one."
        )

    def it_leaves_a_live_door_alone_when_a_placeholder_shares_its_title(event):
        hub = _track_c_hub(event)
        _run("--event", str(event.pk))
        stray = HubCardFactory(hub=hub, kind=HubCard.Kind.PLACEHOLDER, title="Vendor Tables")

        _run("--event", str(event.pk))

        stray.refresh_from_db()
        assert stray.kind == HubCard.Kind.PLACEHOLDER
        assert hub.cards.filter(kind=HubCard.Kind.VENDOR_TABLES).count() == 1

    def it_never_writes_the_footer_on_an_existing_hub(event):
        hub = _track_c_hub(event)

        _run("--event", str(event.pk))

        hub.refresh_from_db()
        assert hub.footer_note == "The old footer."


REMOVED_DOOR_TITLES = (
    "Log a wine bottle",
    "Sponsorship Tracker",
    "Market Check-in",
    "Event flyer and sharing",
    "Marketing Tracker",
)


def describe_the_removed_doors():
    """#1307 AC5: the wine wall, both trackers, the Market Check-in and the flyer page are gone, and no seed brings
    them back."""

    def it_seeds_a_new_hub_with_the_four_doors_and_nothing_else(event):
        _run("--event", str(event.pk))

        hub = EventHub.objects.get(slug=HUB.slug)
        assert list(hub.cards.values_list("kind", flat=True)) == [
            HubCard.Kind.RSVP,
            HubCard.Kind.VOLUNTEERS,
            HubCard.Kind.LEDGER,
            HubCard.Kind.VENDOR_TABLES,
        ]
        assert not hub.cards.filter(title__in=REMOVED_DOOR_TITLES).exists()

    def it_never_makes_or_adopts_a_removed_door_on_a_reseed(event):
        hub = _track_c_hub(event)
        for title in REMOVED_DOOR_TITLES:
            HubCardFactory(hub=hub, kind=HubCard.Kind.PLACEHOLDER, title=title)

        _run("--event", str(event.pk))
        hub.seed_default_cards()

        live = set(hub.cards.exclude(kind=HubCard.Kind.PLACEHOLDER).values_list("kind", flat=True))
        assert live == {HubCard.Kind.RSVP, HubCard.Kind.VOLUNTEERS, HubCard.Kind.LEDGER, HubCard.Kind.VENDOR_TABLES}
        # A placeholder an old seed left under a removed door's title stays a placeholder: nothing adopts it.
        assert sorted(hub.cards.filter(title__in=REMOVED_DOOR_TITLES).values_list("kind", flat=True)) == [
            HubCard.Kind.PLACEHOLDER
        ] * len(REMOVED_DOOR_TITLES)
        assert hub.cards.count() == 4 + len(REMOVED_DOOR_TITLES)

    def it_adds_no_wine_wall_goal(event):
        _run("--event", str(event.pk))

        assert not HubGoal.objects.filter(label="Wine wall bottles").exists()
        assert {goal.kind for goal in GOALS} == {
            HubGoal.Kind.EVENT_CASH,
            HubGoal.Kind.VOLUNTEER_SPOTS,
            HubGoal.Kind.RSVPS,
            HubGoal.Kind.MEMBER_TABLES,
            HubGoal.Kind.GUILDS,
        }


def describe_the_card_seed_summary():
    def it_says_the_doors_are_up_to_date_when_nothing_changed():
        assert CardSeed(kept=4).summary == "The doors are up to date."

    def it_names_what_opened_was_added_and_reworded():
        assert CardSeed(adopted=["Vendor Tables"], created=1, reworded=1).summary == (
            "Doors updated: opened Vendor Tables; added 1 door; gave 1 card Lee's latest words."
        )
        assert CardSeed(adopted=["A", "B"], created=2, reworded=9).summary == (
            "Doors updated: opened A, B; added 2 doors; gave 9 cards Lee's latest words."
        )


def describe_refusals():
    def it_seeds_with_no_event_when_none_is_named():
        out = _run()

        hub = EventHub.objects.get(slug=HUB.slug)
        assert hub.event is None
        assert _counts() == (1, 4, 5, 21)
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
            "4 cards, 5 goals, 21 shifts created; 0 cards, 0 goals, 0 shifts kept."
        ) in out

    def it_leaves_an_existing_hub_as_it_is(event):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)
        hub.title = "Edited by hand"
        hub.save(update_fields=["title"])

        out = _run("--event", str(event.pk), "--dry-run")

        hub.refresh_from_db()
        assert hub.title == "Edited by hand"
        assert _counts() == (1, 4, 5, 21)
        assert (
            "rolled back: hub would be kept; 0 cards, 0 goals, 0 shifts created; 4 cards, 5 goals, 21 shifts kept."
            in out
        )


def describe_the_seeded_hq():
    def it_renders_for_an_admin_with_four_doors_five_goals_and_the_countdown(event, admin_client: Client):
        _run("--event", str(event.pk))
        hub = EventHub.objects.get(slug=HUB.slug)

        body = admin_client.get(hub.get_absolute_url()).content.decode()

        assert body.count("data-hq-card-pk=") == 4
        assert body.count("data-hq-goal>") == 5
        assert 'data-hq-countdown="7 days to go"' in body
        assert "data-hq-footnote" in body
