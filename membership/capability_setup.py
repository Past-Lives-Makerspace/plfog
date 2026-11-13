"""Get Set Up cards (#1167, part 2) for the seven capabilities that have a page of their own.

Each is a :class:`~membership.permission_setup.PermissionSetupSpec`, as Tour Guide's is in
:mod:`tours.setup`: a reading step ends with Got It, and every other step ticks from a row the
member's own work already writes (a class review, a space request decision, an event). Billing
Administrator and Equipment Administrator do work nothing records by person, so their cards are
reading steps only and the badge comes on the last Got It. Every line is true in the code today.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from membership.models import AdminCapability
from membership.permission_setup import PermissionSetupSpec, SetupFacts, SetupStat, SetupStep

if TYPE_CHECKING:
    from membership.models import Member

Capability = AdminCapability.Capability

LEARN = "learn"
FIRST_REVIEW = "first_review"
FIRST_DECISION = "first_decision"
OWN_EVENT = "own_event"
FIRST_EVENT = "first_event"
ALERTS = "alerts"
BOOKINGS = "bookings"
FIRST_SPACE = "first_space"

#: Every finished card says where the badge shows; the rest of the line is the permission's own.
_ON_DIRECTORY = "Your badge now shows on your Member Directory card"
_DIGEST = "The email comes in the 8 AM staff digest, and is left out if someone has already decided."


def _facts(done: dict[str, int], stat_label: str = "", stat_key: str = "") -> SetupFacts:
    """Facts from per-step counts: a step is done once its count is above zero."""
    return SetupFacts(
        done=frozenset(key for key, count in done.items() if count),
        stats=(SetupStat(done[stat_key], stat_label),) if stat_label else (),
    )


def class_review_facts(member: Member) -> SetupFacts:
    """CMS Administrator: the admin side class reviews ``member`` has decided, in one query."""
    from classes.models import ClassApproval

    reviewed = 0
    if member.user_id is not None:
        reviewed = (
            ClassApproval.objects.filter(role=ClassApproval.Role.ADMIN, decided_by_id=member.user_id)
            .exclude(decision="")
            .count()
        )
    return _facts({FIRST_REVIEW: reviewed}, "Classes Reviewed", FIRST_REVIEW)


def space_request_facts(member: Member) -> SetupFacts:
    """Space & Cubby Administrator: the space and cubby requests ``member`` has approved or declined."""
    from membership.models import SpaceRequest

    decided = 0
    if member.user_id is not None:
        decided = SpaceRequest.objects.filter(reviewed_by_id=member.user_id).count()
    return _facts({FIRST_DECISION: decided}, "Requests Decided", FIRST_DECISION)


def calendar_admin_facts(member: Member) -> SetupFacts:
    """Calendar Administrator: proposals ``member`` reviewed and events they added, in two queries."""
    from membership.models import CommunityEvent

    reviewed = added = 0
    if member.user_id is not None:
        reviewed = CommunityEvent.objects.filter(reviewed_by_id=member.user_id).count()
        added = int(CommunityEvent.objects.filter(created_by_id=member.user_id).exists())
    return _facts({FIRST_REVIEW: reviewed, OWN_EVENT: added}, "Proposals Reviewed", FIRST_REVIEW)


def event_creator_facts(member: Member) -> SetupFacts:
    """Can create events: the events ``member`` has added or proposed."""
    from django.db.models import Q

    from membership.models import CommunityEvent

    added = 0
    if member.user_id is not None:
        added = CommunityEvent.objects.filter(
            Q(created_by_id=member.user_id) | Q(submitted_by_id=member.user_id)
        ).count()
    return _facts({FIRST_EVENT: added}, "Events Added", FIRST_EVENT)


def space_manager_facts(member: Member) -> SetupFacts:
    """Space Manager: the rooms and spaces ``member`` added, in one query.

    ``Equipment.added_by`` records it since #1167; a space added before then counts by the staff
    row Add Equipment gave a creator who could not otherwise manage it (granted by themself).
    """
    from django.db.models import F, Q

    from membership.models import Equipment

    added = (
        Equipment.objects.filter(kind__in=[Equipment.Kind.ROOM, Equipment.Kind.SPACE])
        .filter(
            Q(added_by=member)
            | Q(staff_memberships__member=member, staff_memberships__granted_by=F("staff_memberships__member"))
        )
        .distinct()
        .count()
    )
    return _facts({FIRST_SPACE: added}, "Spaces Added", FIRST_SPACE)


def reading_only_facts(member: Member) -> SetupFacts:
    """A card of reading steps only: nothing to read from the member's work."""
    return SetupFacts(done=frozenset())


CMS_ADMIN_SETUP = PermissionSetupSpec(
    permission=Capability.CLASS_APPROVER,
    title="Get Set Up to Review Classes",
    lead="Two short steps. Each one opens when you get to it.",
    page_url_name="classes:admin_classes",
    page_name="Manage All Classes",
    done_title="You're a CMS Administrator",
    done_line=f"{_ON_DIRECTORY}, so instructors know who reviews classes.",
    done_line_without_badge="You're set up to review classes.",
    celebration="Your CMS Administrator badge is on your Member Directory card.",
    facts=class_review_facts,
    steps=(
        SetupStep(
            key=LEARN,
            title="Learn How Class Review Goes",
            note="About a minute to read",
            why="A class is published only once an admin side reviewer approves it. That is you now.",
            email_line="Learn how class review goes",
            how=(
                "You review classes whose guild has no lead, and every class its guild has already approved.",
                f"Each request rings your bell at once with a Review link. {_DIGEST}",
                "Open a class to approve it, or send it back to the instructor with a note.",
            ),
            guide_section="How Review Goes",
            got_it=True,
        ),
        SetupStep(
            key=FIRST_REVIEW,
            title="Review Your First Class",
            note="Approve one, or send it back with a note",
            why="Pick the Awaiting admin chip to see what is waiting on you. Approve a class, or send it back "
            "with a note, and your CMS Administrator badge is yours.",
            email_line="Review your first class",
            action_label="See What Awaits You",
            action_url_name="classes:admin_classes",
            action_query="?status=awaiting_admin",
            guide_section="Reviewing a Class",
        ),
    ),
)

SPACE_ADMIN_SETUP = PermissionSetupSpec(
    permission=Capability.SPACE_APPROVER,
    title="Get Set Up to Review Space Requests",
    lead="Two short steps. Each one opens when you get to it.",
    page_url_name="hub_space_request_review_queue",
    page_name="Space Requests",
    done_title="You're a Space & Cubby Administrator",
    done_line=f"{_ON_DIRECTORY}, so members know who answers space requests.",
    done_line_without_badge="You're set up to review space requests.",
    celebration="Your Space & Cubby Administrator badge is on your Member Directory card.",
    facts=space_request_facts,
    steps=(
        SetupStep(
            key=LEARN,
            title="Learn How Requests Reach You",
            note="About a minute to read",
            why="You review every studio space and shelf request, site wide.",
            email_line="Learn how requests reach you",
            how=(
                f"Each request rings your bell at once with a link to it. {_DIGEST}",
                "Requests wait on this page until someone approves or declines them.",
                "A decline needs a note, which the member reads.",
            ),
            guide_section="How Requests Reach You",
            got_it=True,
        ),
        SetupStep(
            key=FIRST_DECISION,
            title="Decide Your First Request",
            note="Approve or decline one below",
            why="Approve or decline a request below, and your Space & Cubby Administrator badge is yours.",
            email_line="Decide your first request",
            guide_section="Deciding a Request",
        ),
    ),
)

CALENDAR_ADMIN_SETUP = PermissionSetupSpec(
    permission=Capability.EVENTS_APPROVER,
    title="Get Set Up to Review Calendar Proposals",
    lead="Three short steps. Each one opens when you get to it.",
    page_url_name="hub_admin_events",
    page_name="Manage Events",
    done_title="You're a Calendar Administrator",
    done_line=f"{_ON_DIRECTORY}, so members know who looks after the Calendar.",
    done_line_without_badge="You're set up to review Calendar proposals.",
    celebration="Your Calendar Administrator badge is on your Member Directory card.",
    facts=calendar_admin_facts,
    steps=(
        SetupStep(
            key=LEARN,
            title="Learn How Proposals Reach You",
            note="About a minute to read",
            why="You review Calendar proposals that are not for a guild. A guild's proposals go to its lead and staff.",
            email_line="Learn how proposals reach you",
            how=(
                f"Each proposal rings your bell at once. {_DIGEST}",
                "Manage Events lists every event. Open one to approve or decline it.",
                "You can edit or delete any event that is not a guild's.",
            ),
            guide_section="How Proposals Reach You",
            got_it=True,
        ),
        SetupStep(
            key=FIRST_REVIEW,
            title="Review Your First Proposal",
            note="Approve or decline one",
            why="Pick the Needs review chip to see what is waiting. Approve or decline a proposal, and that "
            "is the last step you need for your badge.",
            email_line="Review your first proposal",
            action_label="See What Needs Review",
            action_url_name="hub_admin_events",
            action_query="?status=needs_review",
            guide_section="Reviewing a Proposal",
        ),
        SetupStep(
            key=OWN_EVENT,
            title="Add an Event of Your Own",
            note="Yours are published straight away",
            why="Events you add are published straight away, with no review.",
            email_line="Add an event of your own (optional)",
            action_label="Add an Event",
            action_url_name="hub_event_add",
            guide_section="Adding Your Own Events",
            optional=True,
        ),
    ),
)

EVENT_CREATOR_SETUP = PermissionSetupSpec(
    permission=Capability.EVENT_CREATOR,
    badge_label="Event Creator",
    title="Get Set Up to Add Events",
    lead="Two short steps. Each one opens when you get to it.",
    page_url_name="hub_community_calendar",
    page_name="the Calendar",
    done_title="You're an Event Creator",
    done_line=f"{_ON_DIRECTORY}, so members know who to ask about putting an event on the Calendar.",
    done_line_without_badge="You're set up to add events.",
    celebration="Your Event Creator badge is on your Member Directory card.",
    facts=event_creator_facts,
    steps=(
        SetupStep(
            key=LEARN,
            title="Learn How Your Events Go Out",
            note="About a minute to read",
            why="You can add events to the Calendar whatever the member event setting says.",
            email_line="Learn how your events go out",
            how=(
                "Every event you save goes to an admin or a Calendar Administrator for a quick review first.",
                "It is announced once it is approved.",
            ),
            guide_section="How Your Events Go Out",
            got_it=True,
        ),
        SetupStep(
            key=FIRST_EVENT,
            title="Add Your First Event",
            note="Fill it in and save it",
            why="Press + Add event, fill in your event and save it, and your Event Creator badge is yours.",
            email_line="Add your first event",
            action_label="Add an Event",
            action_url_name="hub_event_add",
            guide_section="Adding an Event",
        ),
    ),
)

BILLING_ADMIN_SETUP = PermissionSetupSpec(
    permission=Capability.BILLING_APPROVER,
    title="Get Set Up on the Payments Dashboard",
    lead="Two short things to read. Each one opens when you get to it.",
    page_url_name="billing_admin_dashboard",
    page_name="the Payments Dashboard",
    done_title="You're a Billing Administrator",
    done_line=f"{_ON_DIRECTORY}, so members know who looks after payments.",
    done_line_without_badge="You're set up on the Payments Dashboard.",
    celebration="Your Billing Administrator badge is on your Member Directory card.",
    facts=reading_only_facts,
    steps=(
        SetupStep(
            key=LEARN,
            title="Learn the Payments Dashboard",
            note="About a minute to read",
            why="Here is what this page shows you.",
            email_line="Learn the Payments Dashboard",
            how=(
                "Payments lists every payment, with a date range and filters.",
                "While My Tab is on, Overview and Open Tabs show members' tabs too.",
                "Settings and Stripe stay with admins.",
            ),
            guide_section="The Dashboard",
            got_it=True,
        ),
        SetupStep(
            key=ALERTS,
            title="Know Which Alerts Come to You",
            note="About a minute to read",
            why="You get an alert when money needs a person to look at it.",
            email_line="Know which alerts come to you",
            how=(
                "A member's tab charge fails, or a refund fails.",
                "A payout to an instructor or orientor fails, or a refund's share cannot be taken back.",
                "A class is paid for after its registration lost the seat: refund it or re-seat them.",
                "A late fee or an orientation is paid online with nothing left to match it to.",
            ),
            guide_section="Alerts",
            got_it=True,
        ),
    ),
)

EQUIPMENT_ADMIN_SETUP = PermissionSetupSpec(
    permission=Capability.EQUIPMENT,
    title="Get Set Up to Manage Equipment",
    lead="Two short things to read. Each one opens when you get to it.",
    page_url_name="hub_equipment_index",
    page_name="Reservations",
    done_title="You're an Equipment Administrator",
    done_line=f"{_ON_DIRECTORY}, so members know who looks after the building's equipment.",
    done_line_without_badge="You're set up to manage equipment.",
    celebration="Your Equipment Administrator badge is on your Member Directory card.",
    facts=reading_only_facts,
    steps=(
        SetupStep(
            key=LEARN,
            title="Learn What You Can Change",
            note="About a minute to read",
            why="You can add equipment of every kind and manage any item in the building.",
            email_line="Learn what you can change",
            how=(
                "Add a tool, room or space with + Add on this page.",
                "Open any item from this page to change its details, staff, orientations and hours.",
            ),
            guide_section="What You Can Change",
            got_it=True,
        ),
        SetupStep(
            key=BOOKINGS,
            title="Learn Who Hears About Bookings",
            note="About a minute to read",
            why="An item's bookings go to its managers and its guild's leadership first.",
            email_line="Learn who hears about bookings",
            how=(
                "You hear about an item's bookings only when it has no managers and no guild leadership.",
                "Give an item managers on its Staff tab, and they hear instead.",
            ),
            guide_section="Who Hears About Bookings",
            got_it=True,
        ),
    ),
)

SPACE_MANAGER_SETUP = PermissionSetupSpec(
    permission=Capability.SPACE_MANAGER,
    title="Get Set Up to Add Spaces",
    lead="Two short steps. Each one opens when you get to it.",
    page_url_name="hub_equipment_index",
    page_name="Reservations",
    done_title="You're a Space Manager",
    done_line=f"{_ON_DIRECTORY}, so members know who looks after the rooms they reserve.",
    done_line_without_badge="You're set up to add spaces.",
    celebration="Your Space Manager badge is on your Member Directory card.",
    facts=space_manager_facts,
    steps=(
        SetupStep(
            key=FIRST_SPACE,
            title="Add Your First Space",
            note="A room or space members can reserve",
            why="Add a room or space members can reserve, such as the loading dock. Tools are not yours to add.",
            email_line="Add your first space",
            action_label="Add a Space",
            action_url_name="hub_equipment_add",
            guide_section="Adding a Space",
        ),
        SetupStep(
            key=LEARN,
            title="Learn How You Manage Your Spaces",
            note="About a minute to read",
            why="Saving a space opens its page, where you manage it from then on.",
            email_line="Learn how you manage your spaces",
            how=(
                "Manage each space you add from its own page: its details, hours and staff.",
                "Its bookings go to the people on its Staff tab and its guild's leadership, or, with neither, "
                "to the Equipment Administrators. Check you are on its Staff tab to hear about them.",
            ),
            guide_section="Managing Your Spaces",
            got_it=True,
        ),
    ),
)

#: The part 2 cards, in the order the Guide For picker lists them.
CAPABILITY_SETUPS: tuple[PermissionSetupSpec, ...] = (
    CMS_ADMIN_SETUP,
    SPACE_ADMIN_SETUP,
    CALENDAR_ADMIN_SETUP,
    EVENT_CREATOR_SETUP,
    BILLING_ADMIN_SETUP,
    EQUIPMENT_ADMIN_SETUP,
    SPACE_MANAGER_SETUP,
)
