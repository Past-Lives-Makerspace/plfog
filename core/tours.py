"""The guided-tour registry — Spec C's in-code spine, sibling of :mod:`core.help_registry`.

Tour content is repo-authored (no DB model — YAGNI): frozen dataclasses plus the
module-level ``TOURS`` dict. Step targets are ``[data-help-key="…"]`` selectors
shared with Spec B's Info View annotations, so the template-walk drift test
(``tests/hub/help_keys_spec.py``) guards tour targets for free. Per-user state
lives in :class:`core.models.TourState`; ``tour_offer_context`` below owns every
offer/autostart guard so entry-page views stay thin.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.urls import reverse

if TYPE_CHECKING:
    from django.http import HttpRequest

    from membership.models import Member


@dataclass(frozen=True)
class TourStep:
    """One spotlight stop. ``target=None`` renders a centered (element-less) popover."""

    target: str | None  # CSS selector, usually '[data-help-key="…"]'; None = centered step
    title: str
    body: str  # ELI14, 1-2 sentences


@dataclass(frozen=True)
class Tour:
    """One single-page tour. Multi-page tours are consciously not built (Spec C §1)."""

    key: str  # slug, e.g. "member-welcome"
    title: str  # shown on the Help card and the offer
    entry_url_name: str  # url name of the single page the tour runs on
    audience: Callable[[Member], bool]
    steps: tuple[TourStep, ...]
    opens_sidebar: bool = False  # member tour: open the (mobile/collapsed) sidebar before starting
    entry_url_kwargs: Callable[[Member], dict[str, Any]] | None = None  # e.g. guild pk for guild-lead


def _any_member(member: Member) -> bool:
    return True


def _guild_lead_or_staff(member: Member) -> bool:
    return member.is_guild_lead or member.is_guild_staff


def _active_member(member: Member) -> bool:
    # Instructor-tour audience TODAY: any active member — mirrors
    # classes.views.teaching_member_required, which admits any active member.
    # When Spec D (instructor-orientation unlock) ships, this becomes
    # ``member.can_create_classes`` — D's rewritten decorator 302s locked members
    # off the teach overview, so both auto-offers and manual starts must gate on
    # the unlock or they dead-loop. D flips this in its own build.
    return member.status == member.Status.ACTIVE


def _first_staffed_guild_pk(member: Member) -> dict[str, Any]:
    # Any guild the member leads/staffs works: the tour targets the tab strip,
    # identical on every guild. Audience gating guarantees at least one exists.
    guild = member.staffed_guilds.first()
    if guild is None:
        raise ValueError(f"Member {member.pk} passed the guild-lead audience but staffs no guild")
    return {"pk": guild.pk}


TOURS: dict[str, Tour] = {
    "member-welcome": Tour(
        key="member-welcome",
        title="The member hub",
        entry_url_name="hub_home",
        audience=_any_member,
        opens_sidebar=True,
        steps=(
            TourStep(
                target=None,
                title="Welcome to FOG",
                body=(
                    "This is the member hub for Past Lives. Want a 30-second look around? "
                    "Use Next — or press Esc anytime to stop."
                ),
            ),
            TourStep(
                target='[data-help-key="nav.sidebar"]',
                title="Everything in one place",
                body=(
                    "The sidebar gets you everywhere: guilds, classes, the calendar, your settings. "
                    "On a phone, the menu button opens it."
                ),
            ),
            TourStep(
                target='[data-help-key="nav.guilds"]',
                title="Guilds",
                body=(
                    "Guilds are the craft groups that run each studio. Join as many as you like, "
                    "then book an orientation to get working in the space."
                ),
            ),
            TourStep(
                target='[data-help-key="nav.calendar"]',
                title="Community calendar",
                body=(
                    "Classes, guild meetups, and events all land here. Filter it, open any event, "
                    "or subscribe from your own calendar app."
                ),
            ),
            TourStep(
                target='[data-help-key="voting.rank-guilds"]',
                title="Guild voting",
                body=(
                    "Each month, members rank their top 3 guilds to help decide funding. "
                    "Your picks save automatically and you can change them anytime."
                ),
            ),
            TourStep(
                target='[data-help-key="home.get-started"]',
                title="Your Get started list",
                body=(
                    "A short checklist to finish setting up — profile, photo, first guild. "
                    "It disappears on its own when you're done."
                ),
            ),
            TourStep(
                target='[data-help-key="nav.help"]',
                title="Help, whenever",
                body=(
                    "Stuck later? The Help page has short guides for everything you just saw. "
                    "That's the tour — go explore."
                ),
            ),
        ),
    ),
    "guild-lead": Tour(
        key="guild-lead",
        title="Guild lead tools",
        entry_url_name="hub_guild_edit",
        audience=_guild_lead_or_staff,
        entry_url_kwargs=_first_staffed_guild_pk,
        # Steps target the tab BUTTONS (always visible) — tab panels are Alpine
        # x-show and hidden content can't be spotlighted.
        steps=(
            TourStep(
                target=None,
                title="Your guild's control room",
                body="This page runs your guild. Each tab below covers one job — here's the quick lap.",
            ),
            TourStep(
                target='[data-help-key="guild.edit-tabs"]',
                title="One tab per job",
                body=(
                    "Basic Information is your public page: banner, overview, meeting times. "
                    "Every other tab saves on its own, so switching tabs never loses work."
                ),
            ),
            TourStep(
                target='[data-help-key="guild.manage-staff"]',
                title="Staff — heads up",
                body=(
                    "Anyone you add here gets full guild authority: edit this page, run orientations, "
                    "approve classes, send announcements. Every role has the same powers, so add people you trust."
                ),
            ),
            TourStep(
                target='[data-help-key="guild.run-orientations"]',
                title="Orientations",
                body=(
                    "Set your orientation hours and open slots here. Member bookings show up on the "
                    "Orientations dashboard in the sidebar, where you confirm them and mark them complete."
                ),
            ),
            TourStep(
                target='[data-help-key="guild.announcements"]',
                title="Announcements",
                body=(
                    "Write to your members with the compose wizard — draft, preview, send. Members can propose "
                    "announcements too; they wait in your review queue until you approve them."
                ),
            ),
        ),
    ),
    "instructor": Tour(
        key="instructor",
        title="The teaching portal",
        entry_url_name="classes:teach_overview",
        audience=_active_member,
        # Step 1's copy ("Any active member can create a class here") is retouched by
        # Spec D's build when the unlock ships (§7.3) — it becomes a statement about
        # the unlocked member ("You're cleared to create classes here").
        steps=(
            TourStep(
                target=None,
                title="The teaching portal",
                body="Any active member can create a class here. Here's the 20-second version of how it works.",
            ),
            TourStep(
                target='[data-help-key="teach.create-class"]',
                title="Start a class",
                body=(
                    "This opens a draft: title, description, dates, price. Drafts are private — "
                    "nothing goes public until it's been reviewed."
                ),
            ),
            # Spec B already stamped the "Needs your attention" card as
            # teach.review-pipeline — reuse it rather than minting a duplicate key.
            TourStep(
                target='[data-help-key="teach.review-pipeline"]',
                title="Draft → review → published",
                body=(
                    "When you submit a draft, it goes to the guild's lead (if the category has one) and then "
                    "an admin. Anything waiting on you, or waiting on review, shows up right here."
                ),
            ),
            TourStep(
                target='[data-help-key="teach.roster"]',
                title="Your classes and rosters",
                body=(
                    "Click any class to see who signed up, manage the waitlist, email your registrants, "
                    "or export the roster as a CSV."
                ),
            ),
        ),
    ),
}


def tours_for(member: Member) -> list[Tour]:
    """The tours this member's roles make them eligible for (Help-page card rows)."""
    return [tour for tour in TOURS.values() if tour.audience(member)]


def entry_url_for(tour: Tour, member: Member) -> str:
    """The manual-start URL for ``tour`` — its entry page plus ``?tour=<key>``."""
    kwargs = tour.entry_url_kwargs(member) if tour.entry_url_kwargs is not None else {}
    return f"{reverse(tour.entry_url_name, kwargs=kwargs)}?tour={tour.key}"


def help_card_rows(member: Member) -> list[dict[str, Any]]:
    """Rows for the Help-page "Guided tours" card: eligible tours + taken state, one status query."""
    from core.models import TourState

    user = member.user
    statuses = TourState.objects.statuses_for(user) if user is not None else {}
    return [
        {
            "tour": tour,
            "completed": statuses.get(tour.key) == TourState.Status.COMPLETED,
            "url": entry_url_for(tour, member),
        }
        for tour in tours_for(member)
    ]


def _tour_payload(tour: Tour, *, autostart: bool) -> dict[str, Any]:
    """The json_script payload pl_tour.js consumes (Spec C §6.2)."""
    return {
        "key": tour.key,
        "title": tour.title,
        "steps": [{"target": step.target, "title": step.title, "body": step.body} for step in tour.steps],
        "state_url": reverse("hub_tour_state", kwargs={"tour_key": tour.key}),
        "autostart": autostart,
        "opens_sidebar": tour.opens_sidebar,
    }


def tour_offer_context(request: HttpRequest, tour_key: str) -> dict[str, Any]:
    """The one helper entry-page views call — owns every offer/autostart guard.

    Returns ``{tour, tour_json, show_tour_offer, tour_autostart}``. Guard order
    (Spec C §5): anonymous/no member → nothing; ``?tour=`` matching this page's
    tour for an eligible member → autostart (no ``offered`` row written, and
    dismissed/completed never block a manual start); otherwise auto-offer only
    when the toggle is on, the audience passes, the welcome modal isn't showing,
    and the ``TourState`` row is absent or still ``OFFERED`` (first eligible GET
    writes the ``offered`` row).

    Raises:
        KeyError: If ``tour_key`` isn't a registered tour (a coding error in the
            calling view — fail loudly).
    """
    from core.models import TourState

    tour = TOURS[tour_key]
    empty: dict[str, Any] = {"tour": None, "tour_json": None, "show_tour_offer": False, "tour_autostart": False}
    if not request.user.is_authenticated:
        return empty
    member = getattr(request.user, "member", None)
    if member is None or not tour.audience(member):
        # Ineligible visitors hitting a ?tour= URL get the page normally — the
        # param is simply ignored (no error).
        return empty
    if request.GET.get("tour") == tour_key:
        return {
            "tour": tour,
            "tour_json": _tour_payload(tour, autostart=True),
            "show_tour_offer": False,
            "tour_autostart": True,
        }
    show_welcome_modal = member.welcome_dismissed_at is None and not member.has_started_profile
    if not member.guided_tours_enabled or show_welcome_modal:
        return empty
    status = TourState.objects.status_for(request.user, tour_key)
    if status is None:
        # The row records "we showed the offer"; the card keeps rendering while
        # the row stays OFFERED — ignoring is not declining.
        TourState.objects.mark_offered(request.user, tour_key)
    elif status != TourState.Status.OFFERED:
        return empty
    return {
        "tour": tour,
        "tour_json": _tour_payload(tour, autostart=False),
        "show_tour_offer": True,
        "tour_autostart": False,
    }
