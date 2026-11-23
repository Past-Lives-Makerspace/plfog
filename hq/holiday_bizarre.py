"""Lee's Holiday Bizarre HQ as data: the hub, its four doors, five goals, 21 volunteer shifts, the vendor setup windows
and the ledger's words.

``manage.py seed_holiday_bizarre`` writes these rows for a published calendar event; correcting
a word here and re-running is the workflow, as with the help articles. Nothing in this module
touches the database: it is frozen dataclasses over the models' choice values, so a spec or a
doc can read Lee's sheet without one.

The doors live beside the model that seeds them (:data:`hq.models.DEFAULT_CARDS`, with the
words each was first seeded with, so a re-run can tell Lee's untouched words from an admin's);
:data:`CARDS` is that same tuple, so the two never drift.
The shifts are Lee's sign-up sheet, phase by phase, in her words; the two bartender shifts
need an Oregon OLCC service permit. The goals are her tracker, all five counted live from the pages
behind the doors: Raised is the event cash (the bar's sales since #1307; a money goal, in whole dollars, as every
``HubGoal`` money figure is), and Member tables and Guilds taking part (the guilds with a demo planned on the ledger,
D6c) have no target, so each shows as a plain count. The vendor setup windows are the Setup day choices on Vendor
Tables and its band's note. The ledger words are her ledger page's title, the line under it, the hint under the
receipt upload and the footer, and (D6c) her running tally's, the door clicker's and the No double entry lines,
reworded for the portal. #1307 removed the sponsor tiers, the Market Check-in's words and the flyer page's with their
doors.
"""

from __future__ import annotations

from dataclasses import dataclass

from hq.models import DEFAULT_CARDS, DoorContent, HubGoal, ShiftPhase


@dataclass(frozen=True)
class HubContent:
    """The hub row: its address and the words in and under the hero band."""

    slug: str
    title: str
    eyebrow: str
    intro: str
    footer_note: str


@dataclass(frozen=True)
class GoalContent:
    """One chip in the goal band. ``kind`` is a ``HubGoal.Kind`` value; ``target`` is whole dollars for a money goal."""

    label: str
    kind: str
    target: int
    is_money: bool = False


@dataclass(frozen=True)
class ShiftContent:
    """One job on the sign-up sheet. ``phase`` is a ``ShiftPhase`` value; the seed orders shifts as listed."""

    phase: str
    role: str
    when_text: str
    need: int
    description: str
    requires_olcc: bool = False


HUB = HubContent(
    slug="holiday-bizarre-2026",
    title="Holiday Bizarre HQ",
    eyebrow="Past Lives Makerspace · 2nd Annual",
    intro="Friday, December 4, 5 to 10 PM. First Friday on the SE Art Walk.",
    footer_note=(
        "Past Lives Makerspace is an Oregon nonprofit corporation. Gifts and sponsorships are not tax-deductible.\n"
        "This HQ is for guild officers: everyone who leads or staffs a guild opens every page here."
    ),
)

# Lee's doors, in her order: the four #1307 kept.
CARDS: tuple[DoorContent, ...] = DEFAULT_CARDS

GOALS: tuple[GoalContent, ...] = (
    GoalContent(label="Raised", kind=HubGoal.Kind.EVENT_CASH, target=10_000, is_money=True),
    GoalContent(label="Volunteer spots", kind=HubGoal.Kind.VOLUNTEER_SPOTS, target=82),
    GoalContent(label="RSVPs", kind=HubGoal.Kind.RSVPS, target=250),
    GoalContent(label="Member tables", kind=HubGoal.Kind.MEMBER_TABLES, target=0),
    GoalContent(label="Guilds taking part", kind=HubGoal.Kind.GUILDS, target=0),
)

# The 21 shifts of Lee's sheet. Their needs add up to the Volunteer spots goal (82).
SHIFTS: tuple[ShiftContent, ...] = (
    ShiftContent(
        phase=ShiftPhase.BEFORE,
        role="Flyer runner",
        when_text="Nov 1 to Dec 1, your schedule",
        need=6,
        description="Drop flyers at SE Portland businesses and log each one in the ledger.",
    ),
    ShiftContent(
        phase=ShiftPhase.BEFORE,
        role="Wine wall collector",
        when_text="November, your schedule",
        need=3,
        description="Ask local shops for a bottle worth $25 or more (higher is great!), pick up donations, log who gave.",
    ),
    ShiftContent(
        phase=ShiftPhase.BEFORE,
        role="Social media sharer",
        when_text="November, anytime",
        need=10,
        description="Share the event post and invite friends. Easy and it really helps.",
    ),
    ShiftContent(
        phase=ShiftPhase.BEFORE,
        role="Decorating crew",
        when_text="Thu Dec 3, 5 to 8 PM",
        need=6,
        description="Lights, garland, signage, wayfinding for the adventure map.",
    ),
    ShiftContent(
        phase=ShiftPhase.SETUP,
        role="Setup crew",
        when_text="Fri Dec 4, 12 to 4 PM",
        need=8,
        description="Tables, chairs, bar, wine wall, trash and recycling stations.",
    ),
    ShiftContent(
        phase=ShiftPhase.SETUP,
        role="Vendor load-in helper",
        when_text="Fri Dec 4, 3 to 5 PM",
        need=3,
        description="Greet member artists, show them their table, help carry.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Bartender, first half",
        when_text="5 to 7:30 PM",
        need=3,
        description="Pour beer, wine and NA drinks. OLCC service permit required.",
        requires_olcc=True,
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Bartender, second half",
        when_text="7:30 to 10 PM",
        need=3,
        description="Pour beer, wine and NA drinks. OLCC service permit required.",
        requires_olcc=True,
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Door and check-in, first half",
        when_text="5 to 7:30 PM",
        need=2,
        description="Welcome guests, check RSVPs, hand out drink coupons and maps, count attendance.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Door and check-in, second half",
        when_text="7:30 to 10 PM",
        need=2,
        description="Welcome guests, check RSVPs, hand out drink coupons and maps, count attendance.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Security and safety, first half",
        when_text="5 to 7:30 PM",
        need=2,
        description="Walk the floor, keep exits clear, check IDs at the bar, keep drinks inside.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Security and safety, second half",
        when_text="7:30 to 10 PM",
        need=2,
        description="Walk the floor, keep exits clear, check IDs at the bar, help with closing.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Wine wall tickets, first half",
        when_text="5 to 7:30 PM",
        need=2,
        description="Sell wine wall tickets ($25, or 2 for $40) and hand over bottles.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Wine wall tickets, second half",
        when_text="7:30 to 10 PM",
        need=2,
        description="Sell wine wall tickets ($25, or 2 for $40) and hand over bottles.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Tour guide and wayfinder",
        when_text="5 to 10 PM, split it",
        need=4,
        description="Point people through the self-guided adventure map and guild demos.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Guild demo helper",
        when_text="5 to 10 PM, split it",
        need=4,
        description="Support a guild demo station: crowd, safety line, questions.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Runner and restock",
        when_text="5 to 10 PM, split it",
        need=3,
        description="Ice, cups, change for the bar, messages between stations.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Clean as we go",
        when_text="5 to 10 PM, split it",
        need=3,
        description="Empty trash and recycling, wipe tables, check restrooms.",
    ),
    ShiftContent(
        phase=ShiftPhase.DURING,
        role="Photo and video",
        when_text="5 to 10 PM, any hour",
        need=2,
        description="Capture the night for Instagram and next year's sponsor deck.",
    ),
    ShiftContent(
        phase=ShiftPhase.AFTER,
        role="Teardown crew",
        when_text="Fri Dec 4, 10 PM to midnight",
        need=8,
        description="Break down tables and bar, pack decor, take out trash.",
    ),
    ShiftContent(
        phase=ShiftPhase.AFTER,
        role="Next-morning clean",
        when_text="Sat Dec 5, 10 AM to 1 PM",
        need=4,
        description="Sweep, mop, return borrowed items, reset the shop for members.",
    ),
)


# The two windows a vendor can set up in, keyed by ``VendorTable.Setup`` value: the Setup day choices read their
# labels here (``hq.models.VendorSetup``), and the band's note says the same in full.
VENDOR_SETUP_WINDOWS: dict[str, str] = {
    "thu": "Thu Dec 3, 3 to 7 PM",
    "fri": "Fri Dec 4, 3 to 5 PM",
}
VENDOR_SETUP_NOTE = "Setup: Thursday Dec 3, 3 to 7 PM, or Friday Dec 4, 3 to 5 PM. Sale runs Friday 5 to 10 PM."


# The PL Ledger's words, in Lee's (her ledger page): the title, the line under it and the hint under the receipt.
LEDGER_TITLE = "Holiday Bizarre Ledger"
LEDGER_INTRO = (
    "Here's our Holiday Bizarre ledger for tracking the drinks budget, sponsored supplies, drink sales, staff time and "
    "receipts."
)
LEDGER_RECEIPT_HINT = "On your phone, tap to take a photo of the receipt."
LEDGER_FOOTER = (
    "Past Lives Makerspace is an Oregon nonprofit corporation. Gifts and sponsorships are not tax-deductible."
)


# The running tally at the top of the ledger (D6c), in Lee's words: the eyebrow, the title (its last word in gold) and
# the countdown's labels. The days come from ``EventHub.countdown_parts``; the day-of lines read the event's start.
TALLY_EYEBROW = "The running tally"
TALLY_TITLE = "Look what we're building"
TALLY_TITLE_GOLD = "together"
TALLY_DAYS = "days to the Bizarre"
TALLY_TOMORROW = "day to go. Tomorrow!"
TALLY_DOORS_AT = "Doors at {time}"
TALLY_LIVE = "We're live!"
TALLY_DONE = "Done"
TALLY_THANKS = "Thank you, everyone"
# Attendance and vendors: Lee's line beside the door clicker, and her No double entry note in the portal's words (each
# list lives on its own door; the figures here are read from those doors as they change, never copied in).
# The clicker counts walk-ins only: an RSVP checks in on the event's Guest list, and attendance adds the two.
# ``{guest_list}`` is the Guest list's name, a link to Manage Events' Guest list tab when the viewer may open it.
DOOR_CLICKER_HINT = "Tap for each walk-in without an RSVP. RSVPs check in on the {guest_list}."
DOOR_GUEST_LIST = "Guest list"
NO_DOUBLE_ENTRY_TITLE = "No double entry: each list lives on one page"
