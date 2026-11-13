"""Get Set Up cards (#1167, part 3) for the three grants that are not capabilities.

Instructor (``Member.grant_instructor``: the teaching portal), Admin (``fog_role``) and Class
Reviewer (``GuildStaffMembership.can_review_classes``, per guild) have no :class:`AdminCapability`
row, so each spec says whether a member holds it (``grant_of``). Nothing records who granted any of
them, so the card says "Ask an admin", or a Class Reviewer's guild lead. Revoking each takes the
badge back (``PermissionSetup.withdraw`` from ``revoke_teaching``, the role setters and the Class
Reviewer switch).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import reverse

from membership.capability_setup import FIRST_REVIEW, LEARN, facts_from_counts
from membership.models import SetupPermission
from membership.permission_setup import PermissionSetupSpec, SetupFacts, SetupStat, SetupStep

if TYPE_CHECKING:
    from membership.models import Guild, Member

PROFILE = "profile"
FIRST_CLASS = "first_class"
SUBMITTED = "submitted"
DUTY = "duty"

_ON_DIRECTORY = "Your badge now shows on your Member Directory card"


def instructor_grant(member: Member) -> str | None:
    """Instructor is held while the teaching portal is open to ``member``; nothing records who opened it."""
    return "" if member.can_create_classes else None


def instructor_facts(member: Member) -> SetupFacts:
    """Instructor: a photo and bio, a class they made, and one of theirs sent for review, in two queries."""
    from django.db.models import Count, Q

    from classes.models import ClassOffering

    made = ClassOffering.objects.filter(created_by=member).aggregate(
        made=Count("pk", distinct=True),
        sent=Count("pk", distinct=True, filter=Q(approvals__isnull=False) | Q(status=ClassOffering.Status.PUBLISHED)),
        published=Count("pk", distinct=True, filter=Q(status=ClassOffering.Status.PUBLISHED)),
    )
    facts = facts_from_counts(
        {
            PROFILE: int(bool(member.profile_photo) and bool(member.instructor_bio.strip())),
            FIRST_CLASS: made["made"],
            SUBMITTED: made["sent"],
        }
    )
    return SetupFacts(done=facts.done, stats=(SetupStat(made["published"], "Classes Published"),))


def admin_grant(member: Member) -> str | None:
    """Admin is held while ``member``'s role is Admin; nothing records who made them one."""
    return "" if member.fog_role == member.FogRole.ADMIN else None


def admin_facts(member: Member) -> SetupFacts:
    """Admin: whether ``member`` holds any admin duty (a capability), however it was turned on."""
    return facts_from_counts({DUTY: int(member.admin_capabilities.exists())})


def _reviewed_guilds(member: Member) -> list[Guild]:
    from membership.models import Guild

    return list(
        Guild.objects.filter(staff_memberships__member=member, staff_memberships__can_review_classes=True)
        .select_related("guild_lead")
        .distinct()
        .order_by("name")
    )


def class_reviewer_grant(member: Member) -> str | None:
    """Class Reviewer is held while ``member`` reviews classes for any guild; questions go to its lead."""
    guilds = _reviewed_guilds(member)
    if not guilds:
        return None
    lead = guilds[0].guild_lead
    return lead.display_name if lead is not None and lead.pk != member.pk else ""


def class_reviewer_page(member: Member) -> str:
    """The guild page of the first guild ``member`` reviews for, where its review queue is."""
    guilds = _reviewed_guilds(member)
    return reverse("hub_guild_edit", args=[guilds[0].pk]) if guilds else reverse("hub_guild_directory")


def class_reviewer_facts(member: Member) -> SetupFacts:
    """Class Reviewer: the guild side class reviews ``member`` has decided, in one query."""
    from classes.models import ClassApproval

    reviewed = 0
    if member.user_id is not None:
        reviewed = (
            ClassApproval.objects.filter(role=ClassApproval.Role.GUILD_LEAD, decided_by_id=member.user_id)
            .exclude(decision="")
            .count()
        )
    return facts_from_counts({FIRST_REVIEW: reviewed}, "Classes Reviewed", FIRST_REVIEW)


INSTRUCTOR_SETUP = PermissionSetupSpec(
    permission=SetupPermission.INSTRUCTOR,
    grant_of=instructor_grant,
    title="Get Set Up to Teach",
    lead="Three short steps. Each one opens when you get to it.",
    page_url_name="classes:teach_overview",
    page_name="your Teaching Overview",
    done_title="You're an Instructor",
    done_line=f"{_ON_DIRECTORY}, so members know you teach.",
    done_line_without_badge="You're set up to teach.",
    celebration="Your Instructor badge is on your Member Directory card.",
    facts=instructor_facts,
    steps=(
        SetupStep(
            key=PROFILE,
            title="Add Your Photo and Bio",
            note="They show on your public instructor page",
            why="Your public instructor page shows your photo, bio and links. Add a photo and a bio.",
            email_line="Add your photo and bio",
            action_label="Open Instructor Profile",
            action_url_name="classes:teach_profile",
            guide_section="Your Instructor Page",
        ),
        SetupStep(
            key=FIRST_CLASS,
            title="Create Your First Class",
            note="It starts as a draft",
            why="Press + New Class and fill in your class. It saves as a draft until you send it for review. "
            "Show Me Around on the Overview walks you through it.",
            email_line="Create your first class",
            action_label="Start a Class",
            action_url_name="classes:teach_class_create",
            guide_section="Creating a Class",
        ),
        SetupStep(
            key=SUBMITTED,
            title="Send It for Review",
            note="Press Submit for Review on your class",
            why="Open your class on My Classes and press Submit for Review, and your Instructor badge is yours.",
            email_line="Send it for review",
            action_label="Open My Classes",
            action_url_name="classes:teach_dashboard",
            how=(
                "A class goes to its guild's lead first when the guild has one, then to an admin.",
                "It is published once the admin approves it.",
                "A class sent back with a note shows on the Overview, under what needs your attention.",
            ),
            guide_section="How Review Goes",
        ),
    ),
)

ADMIN_SETUP = PermissionSetupSpec(
    permission=SetupPermission.ADMIN_ROLE,
    grant_of=admin_grant,
    title="Get Set Up as an Admin",
    lead="Two short steps. Each one opens when you get to it.",
    page_url_name="hub_admin_tools",
    page_name="Admin Tools",
    done_title="You're an Admin",
    done_line=f"{_ON_DIRECTORY}, so members know who runs the Member Portal.",
    done_line_without_badge="You're set up as an Admin.",
    celebration="Your Admin badge is on your Member Directory card.",
    facts=admin_facts,
    steps=(
        SetupStep(
            key=LEARN,
            title="Learn Where Admin Pages Live",
            note="About a minute to read",
            why="Admin Tools, this page, is your starting point for every admin page.",
            email_line="Learn where admin pages live",
            how=(
                "Admin duties such as reviewing classes or billing alerts are separate permissions.",
                "You hear about a duty only once you turn it on for yourself.",
            ),
            guide_section="Admin Tools",
            got_it=True,
        ),
        SetupStep(
            key=DUTY,
            title="Turn On the Duties You Want",
            note="From the View As menu at the top of any page",
            why="Open the View As menu at the top of any page and turn on the duties you want to hear about. "
            "Your Admin badge comes with the first one.",
            email_line="Turn on the duties you want",
            guide_section="Your Duties",
        ),
    ),
)

CLASS_REVIEWER_SETUP = PermissionSetupSpec(
    permission=SetupPermission.CLASS_REVIEWER,
    grant_of=class_reviewer_grant,
    page_url_for=class_reviewer_page,
    title="Get Set Up to Review Your Guild's Classes",
    lead="Two short steps. Each one opens when you get to it.",
    page_url_name="hub_guild_directory",
    page_name="your guild's page",
    done_title="You're a Class Reviewer",
    done_line=f"{_ON_DIRECTORY}, so instructors know who reviews their classes.",
    done_line_without_badge="You're set up to review your guild's classes.",
    celebration="Your Class Reviewer badge is on your Member Directory card.",
    facts=class_reviewer_facts,
    steps=(
        SetupStep(
            key=LEARN,
            title="Learn How Guild Review Goes",
            note="About a minute to read",
            why="Classes in your guild come to its lead and its Class Reviewers first.",
            email_line="Learn how guild review goes",
            how=(
                "When a class in the guild needs review you get a bell and an email with a Review link. The "
                "email comes in the 8 AM staff digest, and is left out if someone has already decided.",
                "A class you approve then goes to an admin, who publishes it.",
            ),
            guide_section="How Guild Review Goes",
            got_it=True,
        ),
        SetupStep(
            key=FIRST_REVIEW,
            title="Review Your First Class",
            note="Approve one, or send it back with a note",
            why="Classes waiting on you show under Needs Attention, Waiting on Your Review, on this page. "
            "Review one and your Class Reviewer badge is yours.",
            email_line="Review your first class",
            guide_section="Reviewing a Class",
        ),
    ),
)

#: The cards for grants that are not capabilities.
GRANT_SETUPS: tuple[PermissionSetupSpec, ...] = (INSTRUCTOR_SETUP, ADMIN_SETUP, CLASS_REVIEWER_SETUP)
