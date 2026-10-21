"""Lee's Holiday Bizarre HQ as data: the hub, its nine doors, six goals, 21 volunteer shifts, the sponsor tiers, the
vendor setup windows, the Market Check-in's words, the flyer page's and the ledger's.

``manage.py seed_holiday_bizarre`` writes these rows for a published calendar event; correcting
a word here and re-running is the workflow, as with the help articles. Nothing in this module
touches the database: it is frozen dataclasses over the models' choice values, so a spec or a
doc can read Lee's sheet without one.

The nine doors live beside the model that seeds them (:data:`hq.models.DEFAULT_CARDS`, with the
words each was first seeded with, so a re-run can tell Lee's untouched words from an admin's);
:data:`CARDS` is that same tuple, so the two never drift.
The shifts are Lee's sign-up sheet, phase by phase, in her words; the two bartender shifts
need an Oregon OLCC service permit. The goals are her tracker, all six counted live from the pages
behind the doors: Raised is the event cash (sponsors received, bar sales and wine wall tickets; a
money goal, in whole dollars, as every ``HubGoal`` money figure is; Felix, 2026-10-20), and Member
tables and Guilds taking part (the guilds with a demo planned on the ledger, D6c) have no target, so each shows as a
plain count. The sponsor tiers are her draft "what they get" table on the Sponsorship Tracker, row for
row. The vendor setup windows are the Setup day choices on Vendor Tables and its band's note. The check-in
words are the Market Check-in's thank-you line and footer. The flyer words are her member event page's: the message
to send friends (the default an organiser edits), Coming soon, the line under Help spread the word, What to expect, the
members' table card and last year's. The ledger words are her ledger page's title, the line under it, the hint under the
receipt upload and the footer, and (D6c) her running tally's, the door clicker's and the No double entry lines, reworded
for the portal.
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
    GoalContent(label="Raised", kind=HubGoal.Kind.EVENT_CASH, target=10_000, is_money=True),
    GoalContent(label="Wine wall bottles", kind=HubGoal.Kind.BOTTLES, target=100),
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


# The two windows a vendor can set up in, keyed by ``VendorTable.Setup`` value: the Setup day choices read their
# labels here (``hq.models.VendorSetup``), and the band's note says the same in full.
VENDOR_SETUP_WINDOWS: dict[str, str] = {
    "thu": "Thu Dec 3, 3 to 7 PM",
    "fri": "Fri Dec 4, 3 to 5 PM",
}
VENDOR_SETUP_NOTE = "Setup: Thursday Dec 3, 3 to 7 PM, or Friday Dec 4, 3 to 5 PM. Sale runs Friday 5 to 10 PM."


# The Market Check-in's words, in Lee's: the hero's thank-you (``{on_date}`` is " on December 4" from the hub's
# event, blank without one) and the line at the foot of the page.
CHECKIN_THANKS = (
    "Thank you for being part of the 2nd Annual Holiday Bizarre{on_date}. Artists, add your market report. "
    "Volunteers, log your prep hours. Every entry helps us show what's possible when a community works together "
    "toward one goal!"
)
CHECKIN_FOOTER = "Questions? Email Lee at lee@pastlives.space."
# The hero's countdown on the day and after, in Lee's words (the days before read "N days to go" and "1 day to go").
CHECKIN_TODAY = "Today"
CHECKIN_TODAY_LINE = "It's market day!"
CHECKIN_DONE = "Done"
CHECKIN_DONE_LINE = "Thank you!"


# The event flyer and sharing page's words, in Lee's (her member event page). The share message is the default of
# ``HubFlyer.share_message``: an organiser edits it on the page, and the share link is added after it.
FLYER_SHARE_MESSAGE = (
    "You're invited to the Past Lives Holiday Bizarre! Friday, Dec 4, 5 to 10 PM at 2808 SE 9th Ave. Handmade gifts "
    "from local artists, live demos, the wine wall, drinks and more. RSVP for $5 and get $5 off a drink, and every "
    "dollar raised supports Past Lives' community programs."
)
FLYER_COMING_SOON = "Our 2026 flyer is being designed. Check back here to share and print it."
FLYER_SHARE_LEDE = "The more friends you bring, the better the night is for every artist selling."
FLYER_LAST_YEAR_NOTE = (
    "Our first Holiday Bizarre on December 5, 2025 brought the community together with a silent auction, artists' "
    "market and 36 donated items from local businesses."
)
# What to expect, under the message in Help spread the word. ``{ticket}`` is the ledger's single ticket price, which is
# also the least a bottle on the wall should be worth (the donors' Under $X flag reads the same price), so the bullet
# follows whatever the ticket is set to; Lee's two pages disagree on it ($20 here, $25 on the ledger).
FLYER_WHAT_TO_EXPECT: tuple[str, ...] = (
    "Member artists selling handmade work",
    "Live demos from our guilds, including sparks from the Metal Guild",
    "The wine wall: {ticket} a ticket, every bottle worth {ticket} or more",
    "Drinks, music, the Sasquatch and surprises",
)
# Members: want a table to sell your work? Lee's card: ``{email}`` is Kate's address (a mail link on the page), and the
# setup windows follow it from ``VENDOR_SETUP_NOTE``.
FLYER_TABLE_TITLE = "Members: want a table to sell your work?"
FLYER_TABLE_EMAIL = "visualartsguild@pastlives.space"
FLYER_TABLE_ASK = (
    "Tables are limited. Email Kate at {email} with your name, guild, what you sell and the table size you need."
)


# The PL Ledger's words, in Lee's (her ledger page): the title, the line under it and the hint under the receipt.
LEDGER_TITLE = "Holiday Bizarre Ledger"
LEDGER_INTRO = (
    "Here's our Holiday Bizarre ledger for tracking sponsors, wine wall donations, drink sales, volunteer hours and "
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
# Lee's flyer nudge stops once this many businesses have the flyer up.
TALLY_FLYER_NUDGE_UNDER = 25

# Attendance and vendors: Lee's line beside the door clicker, and her No double entry note in the portal's words (each
# list lives on its own door; the figures here are read from those doors as they change, never copied in).
# The clicker counts walk-ins only: an RSVP checks in on the event's Guest list, and attendance adds the two.
# ``{guest_list}`` is the Guest list's name, a link to Manage Events' Guest list tab when the viewer may open it.
DOOR_CLICKER_HINT = "Tap for each walk-in without an RSVP. RSVPs check in on the {guest_list}."
DOOR_GUEST_LIST = "Guest list"
NO_DOUBLE_ENTRY_TITLE = "No double entry: each list lives on one page"
