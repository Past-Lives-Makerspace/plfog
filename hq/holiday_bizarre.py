"""Lee's Holiday Bizarre HQ as data: the hub, its nine doors, four goals, 21 volunteer shifts and the sponsor tiers.

``manage.py seed_holiday_bizarre`` writes these rows for a published calendar event; correcting
a word here and re-running is the workflow, as with the help articles. Nothing in this module
touches the database: it is frozen dataclasses over the models' choice values, so a spec or a
doc can read Lee's sheet without one.

The nine doors live beside the model that seeds them (:data:`hq.models.DEFAULT_CARDS`, with the
words each was first seeded with, so a re-run can tell Lee's untouched words from an admin's);
:data:`CARDS` is that same tuple, so the two never drift.
The shifts are Lee's sign-up sheet, phase by phase, in her words; the two bartender shifts
need an Oregon OLCC service permit. The goals are her tracker: Raised is typed in by an admin
(a money goal, in whole dollars, as every ``HubGoal`` money figure is), the other three are
counted live from the pages behind the doors. The sponsor tiers are her draft "what they get"
table on the Sponsorship Tracker, row for row.
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
        "Each page opens only for people Lee has added. Can't get in? Email lee@pastlives.space."
    ),
)

# Lee's nine doors, in her order. A door whose page is not built yet is seeded as a placeholder.
CARDS: tuple[DoorContent, ...] = DEFAULT_CARDS

GOALS: tuple[GoalContent, ...] = (
    GoalContent(label="Raised", kind=HubGoal.Kind.MANUAL, target=10_000, is_money=True),
    GoalContent(label="Wine wall bottles", kind=HubGoal.Kind.BOTTLES, target=100),
    GoalContent(label="Volunteer spots", kind=HubGoal.Kind.VOLUNTEER_SPOTS, target=82),
    GoalContent(label="RSVPs", kind=HubGoal.Kind.RSVPS, target=250),
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


@dataclass(frozen=True)
class SponsorTierContent:
    """One row of the sponsor tiers table: the tier, its amount and how many, and what they get, one item each."""

    name: str
    amount: str
    how_many: str
    benefits: tuple[str, ...]


# The In-kind row's last benefit; the page links it to the wine wall when that door is open to the viewer.
WINE_WALL_BENEFIT = "Wine counts toward the wine wall"

# Lee's draft tiers, in her words: what each sponsor gets.
SPONSOR_TIERS: tuple[SponsorTierContent, ...] = (
    SponsorTierContent(
        name="Presenting",
        amount="$5,000",
        how_many="1",
        benefits=(
            '"Holiday Bizarre presented by" on the flyer, adventure map, signage and website',
            "Top logo placement",
            "Private team-building workshop at PLM for up to 12",
            "10 wine wall tickets",
            "Instagram reel and member newsletter feature",
            "Thank-you from the stage",
        ),
    ),
    SponsorTierContent(
        name="Gold",
        amount="$2,500",
        how_many="2",
        benefits=(
            "Logo on flyer, adventure map and signage",
            'Name a demo or zone (e.g. "Sparks Stage presented by")',
            "6 wine wall tickets",
            "Instagram feature post and newsletter mention",
            "25% off a team workshop",
        ),
    ),
    SponsorTierContent(
        name="Silver",
        amount="$1,000",
        how_many="3",
        benefits=(
            "Logo on signage and the adventure map",
            "4 wine wall tickets",
            "Group Instagram thank-you",
            "Newsletter mention",
        ),
    ),
    SponsorTierContent(
        name="Bar sponsor",
        amount="$900",
        how_many="1",
        benefits=(
            '"Drinks courtesy of" at the bar',
            "Logo at the bar",
            "4 wine wall tickets",
            "Group Instagram thank-you",
        ),
    ),
    SponsorTierContent(
        name="Community",
        amount="$500",
        how_many="4",
        benefits=("Name on the sponsor board and adventure map", "Group Instagram thank-you"),
    ),
    SponsorTierContent(
        name="In-kind",
        amount="Goods",
        how_many="Any",
        benefits=("Wine, kegs, gift cards and products", "Name on the sponsor board", WINE_WALL_BENEFIT),
    ),
)
