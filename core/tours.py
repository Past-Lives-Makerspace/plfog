"""The guided-tour registry — the in-code spine, sibling of :mod:`core.help_registry`.

Tour content is repo-authored (no DB model — YAGNI): frozen dataclasses plus the
module-level ``TOURS`` dict. Step targets are ``[data-help-key="…"]`` selectors
shared with the Info View annotations, so the template-walk drift test
(``tests/hub/help_keys_spec.py``) guards tour targets for free. Per-user state
lives in :class:`core.models.TourState`.

Tours **drive the browser**: a :class:`TourStep` may carry action fields
(``navigate`` to another page, ``tab_set`` to flip an Alpine tab, ``click`` a
control) so the runtime (``static/js/pl_tour.js``) can move the operator from
screen to screen, detect the new surface, and keep narrating. A step with none
of those fields set behaves exactly as a plain single-page highlight — the
backward-compatible default. :func:`tour_offer_context` owns every offer /
autostart guard; :func:`core.context_processors.tour_runtime` makes the payload
available on every page a tour can land on so a driven hop can re-hydrate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

from django.urls import reverse

if TYPE_CHECKING:
    from django.http import HttpRequest

    from membership.models import Member

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TourStep:
    """One spotlight stop, optionally with an action to reach it.

    ``target=None`` renders a centered (element-less) popover. The action fields
    are all optional; a step with none set is a plain highlight on the current
    page (backward compatible). At most one *reach* action is meaningful per
    step (``navigate`` for a page hop, ``tab_set``/``click`` for a same-page
    hop); the runtime applies whichever is present before showing the popover.
    """

    target: str | None  # CSS selector, usually '[data-help-key="…"]'; None = centered step
    title: str
    body: str  # ELI14, 1-2 sentences; no dashes in copy (arrow "->" allowed)
    navigate: str | None = None  # URL name (preferred) or literal path to load before this step
    navigate_kwargs: Callable[[Member], dict[str, Any]] | dict[str, Any] | None = None  # reverse() kwargs
    query: Callable[[Member], dict[str, str]] | dict[str, str] | None = None  # query string appended to navigate
    tab_set: tuple[str, str] | None = None  # (alpine_var, value) to flip an Alpine tab on the current page
    click: str | None = None  # selector to click before this step (documented fallback for tab_set/navigate)
    wait_for: str | None = None  # selector that must be visible before the popover shows; defaults to target


@dataclass(frozen=True)
class Tour:
    """One role tour — a driven itinerary of :class:`TourStep` s."""

    key: str  # slug, e.g. "member-welcome"
    title: str  # shown on the Help card and the offer
    entry_url_name: str  # url name (namespaced) of the page the offer/autostart lives on
    audience: Callable[[Member], bool]
    steps: tuple[TourStep, ...]
    opens_sidebar: bool = False  # member tour: open the (mobile/collapsed) sidebar before starting
    entry_url_kwargs: Callable[[Member], dict[str, Any]] | None = None  # e.g. guild pk for guild-lead


def _any_member(member: Member) -> bool:
    return True


def _guild_lead_or_staff(member: Member) -> bool:
    # Admins and guild officers can edit every guild page (membership.permissions),
    # so the guild-lead tour is theirs too. They need an active guild to run it on
    # (the entry URL is a guild edit page), so an empty-guilds site doesn't offer it.
    if member.is_guild_lead or member.is_guild_staff:
        return True
    if member.is_fog_admin or member.is_guild_officer:
        from membership.models import Guild

        return Guild.objects.filter(is_active=True).exists()
    return False


def _can_create_classes(member: Member) -> bool:
    # Instructor-tour audience: the teaching unlock. The rewritten
    # ``teaching_member_required`` 302s locked members off the teach overview, so
    # both auto-offers and manual starts must gate on the unlock.
    return member.can_create_classes


def _admin_audience(member: Member) -> bool:
    # The admin tour reaches site-wide tools: a full FOG admin, or any member who
    # holds even one AdminCapability (a scoped admin) is eligible.
    return member.is_fog_admin or member.admin_capabilities.exists()


def _first_staffed_guild_pk(member: Member) -> dict[str, Any]:
    # Any guild the member leads/staffs works: the tour targets the tab strip,
    # identical on every guild. Admins/officers staff no guild of their own, so
    # they fall back to the first active guild (they can edit every guild page).
    guild = member.staffed_guilds.first()
    if guild is None and (member.is_fog_admin or member.is_guild_officer):
        from membership.models import Guild

        guild = Guild.objects.filter(is_active=True).order_by("name").first()
    if guild is None:
        raise ValueError(f"Member {member.pk} passed the guild-lead audience but staffs no guild")
    return {"pk": guild.pk}


def _demo_guild_slug(member: Member) -> dict[str, Any]:
    # The member tour's orientation stop: prefer an active guild that already has
    # a future orientation slot (so the demo has something real to spotlight),
    # else the first active guild. No active guild -> drop the step.
    from django.utils import timezone

    from membership.models import Guild

    now = timezone.now()
    guild = (
        Guild.objects.filter(is_active=True, orientation_slots__starts_at__gte=now).order_by("name").distinct().first()
    )
    if guild is None:
        guild = Guild.objects.filter(is_active=True).order_by("name").first()
    if guild is None:
        raise ValueError("No active guild exists for the member tour orientation stop")
    return {"slug": guild.slug}


def _demo_class_slug(member: Member) -> dict[str, Any]:
    # The member tour's register stop: the soonest bookable (future, published,
    # non-private) class. None bookable -> drop the step.
    from classes.models import ClassOffering

    offering = ClassOffering.objects.bookable().first()
    if offering is None:
        raise ValueError("No bookable class exists for the member tour register stop")
    return {"slug": offering.slug}


def _instructor_class_pk(member: Member) -> dict[str, Any]:
    # The instructor tour's roster + waitlist stops: the instructor's newest
    # class. Owns none -> drop those stops (the tour shortens to create+submit).
    offering = member.classes.order_by("-created_at", "-pk").first()
    if offering is None:
        raise ValueError(f"Instructor {member.pk} owns no class for the roster stops")
    return {"pk": offering.pk}


def _instructor_class_audience(member: Member) -> dict[str, str]:
    # The instructor tour's compose stop: pre-aim the announcement at the
    # instructor's newest class and lock the picker. Owns none -> drop the step.
    offering = member.classes.order_by("-created_at", "-pk").first()
    if offering is None:
        raise ValueError(f"Instructor {member.pk} owns no class for the compose stop")
    return {"audience": f"class:{offering.pk}", "lock": "1"}


TOURS: dict[str, Tour] = {
    "member-welcome": Tour(
        key="member-welcome",
        title="The Member Hub",
        entry_url_name="hub_home",
        audience=_any_member,
        opens_sidebar=True,
        steps=(
            TourStep(
                target=None,
                title="Welcome to the Member Portal",
                body=(
                    "This is your Past Lives member hub. Want a quick lap? I will drive. "
                    "Use Next to move, or press Esc anytime to stop."
                ),
            ),
            TourStep(
                target='[data-help-key="nav.sidebar"]',
                title="Everything in One Place",
                body=(
                    "The sidebar gets you everywhere: guilds, classes, the calendar, your settings. "
                    "On a phone, the menu button opens it."
                ),
            ),
            TourStep(
                target='[data-help-key="home.get-started"]',
                title="Your Get Started List",
                body=(
                    "A short checklist to finish setting up, including your profile, photo, and "
                    "guild updates. It tidies itself away once you are done."
                ),
            ),
            TourStep(
                target='[data-help-key="catalog.filter"]',
                title="Browse Classes and Workshops",
                body="Every class lives here. Filter by guild or date, then open one to see the details.",
                navigate="classes:public_list",
            ),
            TourStep(
                target='[data-help-key="catalog.class-card"]',
                title="Each Card Is a Class",
                body="Click a card to read what you will make and to sign up.",
            ),
            TourStep(
                target='[data-help-key="calendar.filter"]',
                title="Community Calendar",
                body="Classes, guild meetups, and events all land here. Filter it or open any event.",
                navigate="hub_community_calendar",
            ),
            TourStep(
                target='[data-help-key="calendar.subscribe"]',
                title="Subscribe to the Calendar",
                body="Tap Subscribe to add the whole calendar to your own phone or laptop.",
            ),
            TourStep(
                target=None,
                title="It All Syncs to Discord",
                body=(
                    "Classes, events, and guild meetups post to our Discord automatically, so the "
                    "calendar and Discord always match. Nothing extra for you to do."
                ),
            ),
            TourStep(
                target='[data-help-key="voting.rank-guilds"]',
                title="Guild Voting",
                body=(
                    "Each month you rank your top three guilds to help decide funding. "
                    "Your picks save on their own and you can change them anytime."
                ),
                navigate="hub_guild_voting",
            ),
            TourStep(
                target='[data-help-key="spaces.map"]',
                title="The Spaces Map",
                body=(
                    "Studios, storage, parking, and desks live on this map. Click any open space to "
                    "ask about renting it and the team gets your request."
                ),
                navigate="hub_spaces",
            ),
            TourStep(
                target='[data-help-key="orientation.book-slot"]',
                title="Book an Orientation",
                body=(
                    "Before you work in a studio you book an orientation. Pick an open time here "
                    "and the guild confirms it."
                ),
                navigate="hub_guild_detail",
                navigate_kwargs=_demo_guild_slug,
            ),
            TourStep(
                target='[data-help-key="class.register"]',
                title="Register for a Class",
                body=(
                    "Ready to join a class? Hit Register, pick your date, and you are in. "
                    "If it is full you can grab a waitlist spot."
                ),
                navigate="classes:public_class_detail",
                navigate_kwargs=_demo_class_slug,
            ),
            TourStep(
                target='[data-help-key="nav.help"]',
                title="Help, Whenever",
                body=(
                    "Stuck later? The Help page has short guides for everything you just saw. "
                    "That is the lap. Go explore."
                ),
                navigate="hub_help",
            ),
        ),
    ),
    "guild-lead": Tour(
        key="guild-lead",
        title="Guild Lead Tools",
        entry_url_name="hub_guild_edit",
        audience=_guild_lead_or_staff,
        entry_url_kwargs=_first_staffed_guild_pk,
        # A same-page lap: each step flips the Alpine `section` var to a tab and
        # spotlights the now-visible panel. No navigation between steps, so it is
        # very robust: a flip that finds nothing simply skips.
        steps=(
            TourStep(
                target=None,
                title="Your Guild's Control Room",
                body="This page runs your guild. Every tab is one job and each saves on its own. I will flip through them.",
            ),
            TourStep(
                target='[data-help-key="guild.edit-page"]',
                title="Your Public Page",
                body="Basic Information is your public page: banner, overview, meeting times.",
                tab_set=("section", "basic"),
            ),
            TourStep(
                target='[data-help-key="guild.qr-codes"]',
                title="Share and Print",
                body="Print a flyer or grab a QR code for your guild page. Perfect for the shop wall or a table at an event.",
                tab_set=("section", "basic"),
            ),
            TourStep(
                target='[data-help-key="guild.run-orientations"]',
                title="Orientations",
                body=(
                    "Set your orientation hours and open slots here. Bookings show up on the "
                    "Orientations dashboard to confirm."
                ),
                tab_set=("section", "orientations"),
            ),
            TourStep(
                target='[data-help-key="guild.thankyou-email"]',
                title="The Thank You Email",
                body=(
                    "After someone finishes their orientation, this note goes out automatically. "
                    "Turn it on and make it yours."
                ),
                tab_set=("section", "orientations"),
            ),
            TourStep(
                target='[data-help-key="guild.manage-staff"]',
                title="Staff Share Full Authority",
                body="Anyone you add here gets full guild authority. Add people you trust.",
                tab_set=("section", "staff"),
            ),
            TourStep(
                target='[data-help-key="guild.announcements"]',
                title="Announcements",
                body="Write to your members here: draft, preview, send. Member proposals wait in your review queue.",
                tab_set=("section", "announcements"),
            ),
            TourStep(
                target='[data-help-key="guild.welcome-email"]',
                title="The Welcome Email",
                body="This is the welcome email new members get when they join your guild. Make it warm.",
                tab_set=("section", "welcome_email"),
            ),
            TourStep(
                target='[data-help-key="guild.studio-hours"]',
                title="Studio Hours",
                body="Post when your studio is open so members know when they can drop in.",
                tab_set=("section", "studio_hours"),
            ),
            TourStep(
                target='[data-help-key="guild.meeting-notes"]',
                title="Meeting Notes",
                body="Post agendas and recaps so members who miss a meeting can catch up.",
                tab_set=("section", "meeting_notes"),
            ),
            TourStep(
                target='[data-help-key="guild.events"]',
                title="Events",
                body="Post one-off events like a demo night or a field trip. They land on the calendar.",
                tab_set=("section", "events"),
            ),
            TourStep(
                target='[data-help-key="guild.photo-gallery"]',
                title="Photo Gallery",
                body="Add photos of your space and members' work to the gallery.",
                tab_set=("section", "images"),
            ),
            TourStep(
                target='[data-help-key="guild.manage-faq"]',
                title="The FAQ",
                body="Answer common questions in the FAQ so members can help themselves.",
                tab_set=("section", "content"),
            ),
            TourStep(
                target='[data-help-key="guild.manage-links"]',
                title="Links",
                body="Link out to your guides, sign-up sheets, and anything else members need.",
                tab_set=("section", "links"),
            ),
            TourStep(
                target='[data-help-key="guild.wishlist"]',
                title="Your Wishlist",
                body="List the tools and supplies your guild wants. Members and donors can see it.",
                tab_set=("section", "basic"),
            ),
            TourStep(
                target=None,
                title="Your Map, Whenever",
                body=(
                    "That is the lap. The Guild Lead Quickstart on the Help page details every tool, "
                    "and the Cartographers Guild is a full example to borrow from."
                ),
            ),
        ),
    ),
    "instructor": Tour(
        key="instructor",
        title="The Teaching Portal",
        entry_url_name="classes:teach_overview",
        audience=_can_create_classes,
        steps=(
            TourStep(
                target=None,
                title="The Teaching Portal",
                body="You are cleared to teach. I will walk you from a blank draft to a published class and its roster.",
            ),
            TourStep(
                target='[data-help-key="teach.create-class"]',
                title="Start a Class",
                body="Start here. This opens a private draft that nobody sees until it is reviewed.",
            ),
            TourStep(
                target='[data-help-key="teach.class-basics"]',
                title="Title and Description",
                body="Give it a clear title and describe what members will make and learn. Type your own here; I will wait.",
                navigate="classes:teach_class_create",
            ),
            TourStep(
                target='[data-help-key="teach.class-gallery"]',
                title="Add Photos",
                body="Drag in photos of the finished project and the space. Good images fill seats.",
            ),
            TourStep(
                target='[data-help-key="teach.class-schedule"]',
                title="Dates and Sessions",
                body="Set the dates and times. A series just adds more sessions.",
            ),
            TourStep(
                target='[data-help-key="teach.class-pricing"]',
                title="Price and Seats",
                body="Set the price, the number of seats, and the waitlist. This is what members see.",
            ),
            TourStep(
                target='[data-help-key="teach.submit-for-review"]',
                title="Submit for Review",
                body="When it is ready, submit for review. It goes to the guild lead and then an admin before it publishes.",
            ),
            TourStep(
                target='[data-help-key="teach.class-overview"]',
                title="Your Class Home Base",
                body=(
                    "This is one class in one place: who is teaching, the price, how many seats are filled, "
                    "and every session date. Everything below hangs off here."
                ),
                navigate="classes:teach_class_detail",
                navigate_kwargs=_instructor_class_pk,
            ),
            TourStep(
                target='[data-help-key="teach.roster-table"]',
                title="Your Roster",
                body="Here is who signed up. Open a person's menu to remove them and a seat frees up.",
                navigate="classes:teach_class_registrations",
                navigate_kwargs=_instructor_class_pk,
            ),
            TourStep(
                target='[data-help-key="teach.roster-waitlist"]',
                title="The Waitlist",
                body=(
                    "A seat opened up? Promote someone here. They take the spot, and for a paid class you can "
                    "send them a payment link to check out. No card is charged for them."
                ),
                navigate="classes:teach_class_waitlist",
                navigate_kwargs=_instructor_class_pk,
            ),
            TourStep(
                target='[data-help-key="teach.class-qr"]',
                title="Print a Flyer or QR",
                body=(
                    "Open a one page flyer or grab a QR code that links right to your sign up page. "
                    "Print it, post it, hand it out."
                ),
                navigate="classes:teach_class_edit",
                navigate_kwargs=_instructor_class_pk,
            ),
            TourStep(
                target='[data-help-key="announcements.compose"]',
                title="Message Your Class",
                body="Need to reach everyone in this class? Write an announcement here. It is already aimed at just this class.",
                navigate="hub_compose",
                query=_instructor_class_audience,
            ),
            TourStep(
                target=None,
                title="That Is the Teaching Lap",
                body="The Instructor Quickstart on the Help page covers welcome emails and more.",
                navigate="classes:teach_overview",
            ),
        ),
    ),
    "admin": Tour(
        key="admin",
        title="Admin Controls",
        entry_url_name="hub_admin_tools",
        audience=_admin_audience,
        steps=(
            TourStep(
                target=None,
                title="The Admin Controls",
                body="These are the admin controls. I will walk you through the tools you will reach for most.",
            ),
            TourStep(
                target='[data-help-key="announcements.compose"]',
                title="Site Wide Announcements",
                body="Send a site wide announcement here. Pick who gets it, write once, and it can go to email, push, and Discord.",
                navigate="hub_compose",
            ),
            TourStep(
                target='[data-help-key="admin.review-queue"]',
                title="Class Approvals",
                body="Classes waiting on you sit at the top. Review the details, then approve or send it back with a note.",
                navigate="classes:admin_overview",
            ),
            TourStep(
                target='[data-help-key="announcements.review-proposals"]',
                title="Announcement Approvals",
                body="Member proposed announcements wait here. Approve one to post it, or send it back with a note.",
                navigate="hub_guild_announcement_review_queue",
            ),
            TourStep(
                target='[data-help-key="admin.event-review"]',
                title="Event Approvals",
                body="Member proposed events wait here too. Approving one publishes it to the calendar and posts to Discord.",
                navigate="hub_event_review_queue",
            ),
            TourStep(
                target='[data-help-key="admin.refunds"]',
                title="Payments and Refunds",
                body="Every charge is here. Open one to issue a refund; the member gets it back and a receipt.",
                navigate="billing_admin_dashboard",
                query={"tab": "payments"},
            ),
            TourStep(
                target='[data-help-key="admin.reconciliation"]',
                title="Reconciliation",
                body=(
                    "This report breaks down exactly what each guild, instructor, and orientator is owed, "
                    "so you can tell the treasurer precisely what to transfer."
                ),
                navigate="billing_admin_dashboard",
                query={"tab": "reconciliation"},
            ),
            TourStep(
                target='[data-help-key="admin.discount-codes"]',
                title="Discount Codes",
                body="Create discount codes for a class or a promotion here, with limits and an expiry date.",
                navigate="classes:admin_discount_codes",
            ),
            TourStep(
                target='[data-help-key="orientation.dashboard"]',
                title="Orientations Dashboard",
                body="Every guild's orientation requests and completions live here so you can see who is booked and who is oriented.",
                navigate="hub_orientations_dashboard",
            ),
            TourStep(
                target='[data-help-key="admin.invite-member"]',
                title="Manage Members",
                body="See everyone, open a member to edit them, or invite someone new right here.",
                navigate="hub_admin_members",
            ),
            TourStep(
                target='[data-help-key="admin.activity"]',
                title="Site Activity",
                body="A running feed of sign ups, bookings, approvals, and emails, so you can see what just happened.",
                navigate="manage_activity",
            ),
            TourStep(
                target='[data-help-key="admin.quickstart-guides"]',
                title="Your Admin Home",
                body=(
                    "Every tool lives on this page, and the Guild Lead and Instructor Quickstart guides at the "
                    "bottom spell out each role. That is the lap."
                ),
                navigate="hub_admin_tools",
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


def _resolve_mapping(spec: Any, member: Member) -> dict[str, Any]:
    """Resolve a ``navigate_kwargs`` / ``query`` spec (callable, dict, or None) to a dict.

    A callable is invoked with the member; it may raise ``ValueError`` to signal
    "no eligible object" — the caller drops the step (graceful shorten).
    """
    if spec is None:
        return {}
    if callable(spec):
        return spec(member)
    return dict(spec)


def _step_href(navigate: str, kwargs: dict[str, Any], query: dict[str, Any]) -> str:
    """Resolve a step's ``navigate`` (URL name or literal path) + ``query`` to a relative href.

    Raises:
        ValueError: If the resolved href is absolute (has a scheme or host). Every
            tour target must stay on the members apex so ``sessionStorage`` resume
            survives the hop — a cross-origin target is a coding error, caught in
            tests, never shipped.
    """
    # A namespaced URL name (e.g. "classes:public_list") carries a colon but no
    # authority; an absolute URL carries "://". Only the latter is the coding
    # error we guard against — a cross-origin target would break sessionStorage
    # resume (different origin), so fail loud at build time.
    if "://" in navigate or navigate.startswith("//"):
        raise ValueError(f"Tour navigate {navigate!r} is a cross-origin URL; tours must stay relative")
    path = navigate if navigate.startswith("/") else reverse(navigate, kwargs=kwargs)
    if query:
        path = f"{path}?{urlencode(query)}"
    return path


def _tour_payload(tour: Tour, member: Member, *, autostart: bool) -> dict[str, Any]:
    """The json_script payload ``pl_tour.js`` consumes.

    Resolves each step's ``navigate`` to a concrete relative href (member-resolved
    kwargs + query), carries the current page path forward for the runtime's
    location assertion, and **drops** any step whose ``navigate_kwargs`` / ``query``
    resolver raises ``ValueError`` (no eligible object → a shorter, still-coherent
    tour rather than a 500).
    """
    entry_kwargs = tour.entry_url_kwargs(member) if tour.entry_url_kwargs is not None else {}
    current_path = reverse(tour.entry_url_name, kwargs=entry_kwargs)
    steps: list[dict[str, Any]] = []
    for step in tour.steps:
        try:
            nav_kwargs = _resolve_mapping(step.navigate_kwargs, member)
            query = _resolve_mapping(step.query, member)
        except ValueError:
            logger.warning("tour %s: dropping step %r (resolver raised)", tour.key, step.title)
            continue
        href = None
        if step.navigate is not None:
            href = _step_href(step.navigate, nav_kwargs, query)
            current_path = urlsplit(href).path
        steps.append(
            {
                "target": step.target,
                "title": step.title,
                "body": step.body,
                "navigate": href,
                "tab_set": list(step.tab_set) if step.tab_set is not None else None,
                "click": step.click,
                "wait_for": step.wait_for,
                "page_path": current_path,
            }
        )
    return {
        "key": tour.key,
        "title": tour.title,
        "steps": steps,
        "state_url": reverse("hub_tour_state", kwargs={"tour_key": tour.key}),
        "autostart": autostart,
        "opens_sidebar": tour.opens_sidebar,
        "resume_step": 0,
    }


def tour_offer_context(request: HttpRequest, tour_key: str) -> dict[str, Any]:
    """The one helper that owns every offer/autostart guard for a single tour.

    Returns ``{tour, tour_json, show_tour_offer, tour_autostart}``. Guard order:
    anonymous/no member → nothing; site-wide ``SiteConfiguration.guided_tours_enabled``
    off → nothing (the feature does not exist: no offers, no autostarts, and even a
    manual ``?tour=`` start is ignored — unlike the per-member toggle below, which
    only stops auto-offers); ``?tour=`` matching this page's tour for an
    eligible member → autostart (no ``offered`` row written, and dismissed/
    completed never block a manual start); otherwise auto-offer only when the
    member's toggle is on, the audience passes, the welcome modal isn't showing,
    and the ``TourState`` row is absent or still ``OFFERED`` (first eligible GET
    writes the ``offered`` row).

    Raises:
        KeyError: If ``tour_key`` isn't a registered tour (a coding error in the
            caller — fail loudly).
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
    from core.models import SiteConfiguration

    if not SiteConfiguration.load().guided_tours_enabled:
        # Site-wide kill switch: the feature does not exist — even a manual
        # ?tour= start is ignored (contrast Member.guided_tours_enabled, which
        # only suppresses auto-offers).
        return empty
    if request.GET.get("tour") == tour_key:
        return {
            "tour": tour,
            "tour_json": _tour_payload(tour, member, autostart=True),
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
        "tour_json": _tour_payload(tour, member, autostart=False),
        "show_tour_offer": True,
        "tour_autostart": False,
    }
