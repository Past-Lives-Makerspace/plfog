"""Tour Guide's Get Set Up card (#1167), the worked example of :mod:`membership.permission_setup`.

Four steps on the Tours page: connect a Google Calendar (optional, and unavailable while Google
sign in is not set up here), set tour hours, read how a tour day goes (Got It), and give a first
tour, done when one of the guide's tours is marked Attended. Once done the card folds to the tours
they have given, the visitors on them, and how a guide is paid; the pay itself is never tracked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Count, Sum

from membership.models import AdminCapability
from membership.permission_setup import PermissionSetupSpec, SetupFacts, SetupStat, SetupStep

if TYPE_CHECKING:
    from membership.models import Member

CALENDAR = "calendar"
HOURS = "hours"
LEARN = "learn"
FIRST_TOUR = "first_tour"

#: The one rule the folded card states. Felix: stated, never tracked or totalled.
PAY_RULE = "You're paid $50 when a visitor you toured becomes a member."


def tour_guide_facts(member: Member) -> SetupFacts:
    """What ``member`` has done towards Tour Guide's steps, in three queries."""
    from tours import google
    from tours.models import TourBooking, TourCalendarConnection, TourHours

    done: set[str] = set()
    notes: dict[str, str] = {}
    connection = TourCalendarConnection.objects.healthy().filter(guide=member).only("google_email").first()
    if connection is not None:
        done.add(CALENDAR)
        if connection.google_email:
            notes[CALENDAR] = connection.google_email
    if TourHours.objects.filter(guide=member).exists():
        done.add(HOURS)
    given = TourBooking.objects.filter(guide=member, status=TourBooking.Status.ATTENDED).aggregate(
        tours=Count("pk"), visitors=Sum("party_size")
    )
    if given["tours"]:
        done.add(FIRST_TOUR)
    return SetupFacts(
        done=frozenset(done),
        unavailable=frozenset() if google.is_configured() else frozenset({CALENDAR}),
        notes=notes,
        stats=(SetupStat(given["tours"], "Tours Given"), SetupStat(given["visitors"] or 0, "Visitors")),
    )


TOUR_GUIDE_SETUP = PermissionSetupSpec(
    permission=AdminCapability.Capability.TOUR_GUIDE,
    title="Get Set Up to Give Tours",
    lead="Four short steps. Each one opens when you get to it, so there is only ever one thing to read.",
    milestone=(
        HOURS,
        "Visitors Can Book You Now",
        "Your hours are live: visitors can book them online and at the lobby kiosk.",
    ),
    page_url_name="tours:upcoming",
    page_name="the Tours page",
    guide_url_name="tours:guide",
    done_title="You're a Tour Guide",
    done_line="Your badge now shows on your Member Directory card, so members know who to ask about tours.",
    rule_line=PAY_RULE,
    celebration="Your Tour Guide badge is on your Member Directory card.",
    facts=tour_guide_facts,
    steps=(
        SetupStep(
            key=CALENDAR,
            title="Connect Your Google Calendar",
            note="Your tours land on it, and busy times are not offered",
            why="Your tours land on your calendar, and any time it shows you busy is not offered to visitors. "
            "You never double book yourself.",
            email_line="Connect your Google Calendar (optional)",
            action_label="Connect on My Hours",
            action_url_name="tours:hours",
            how=(
                "Each booked tour becomes an event titled with the visitor's name and party size.",
                "Busy times are read about every 15 minutes. A tour already booked is never taken away; "
                "you get a bell if your calendar later clashes.",
                "Disconnect any time on My Hours. Events already written stay on your calendar.",
            ),
            guide_section="Setting Up",
            unavailable_note="Not available yet: Google sign in is not set up here",
            optional=True,
        ),
        SetupStep(
            key=HOURS,
            title="Set Your Tour Hours",
            note="Visitors can book you once this is saved",
            why="Visitors book the times inside your hours. Set them Every Week, or on dates you pick under "
            "Available On.",
            email_line="Set your tour hours",
            action_label="Set Your Hours",
            action_url_name="tours:hours",
            how=(
                "Each window is carved into back to back tours of the length you choose, each with 1 to 50 seats.",
                "Away for a while? Add Days Off on My Hours. Your hours offer nothing on those days.",
            ),
            guide_section="Setting Up",
        ),
        SetupStep(
            key=LEARN,
            title="Learn How a Tour Day Goes",
            note="About a minute to read",
            why="Here is what happens between a booking and the tour.",
            email_line="Learn how a tour day goes",
            how=(
                "Bookings are confirmed at once, unless you pick Confirm First under Confirming Bookings on "
                "My Hours. Then you confirm or decline each request on Upcoming by 24 hours before the tour.",
                "You get an email, a bell and a push for each booking, and hear again if the visitor moves or cancels.",
                "Visitors check in at the lobby kiosk by their email or name, and your phone pings: "
                '"Your 10:00 tour is here."',
                "Afterwards, mark it Attended on the Past tab. That is the record of who you toured.",
            ),
            more_title="Moving or Cancelling a Tour",
            more=(
                "Reschedule or Cancel Tour on the Upcoming tab. The visitor is emailed, with your note if you add one.",
                "Away for a while? Add Days Off on My Hours. Tours already booked stay booked.",
            ),
            guide_section="A Tour Day",
            got_it=True,
        ),
        SetupStep(
            key=FIRST_TOUR,
            title="Give Your First Tour",
            note="Then mark it Attended on the Past tab, and your Tour Guide badge is yours",
            why="Meet your visitors in the lobby. Afterwards, mark the tour Attended on the Past tab, and "
            "your Tour Guide badge is yours.",
            email_line="Give your first tour",
            action_label="Open the Past Tab",
            action_url_name="tours:past",
            guide_section="A Tour Day",
        ),
    ),
)
