"""The guidance registry: what never changes about a feature between releases (#1007).

A changelog fragment says what a release changed and names the ``feature`` it belongs to; this
module holds the rest, once per feature key: a title, where the feature lives, the few steps a
member follows to use it, and the teaching materials a release may point at (a clip, #1008, and
a Guided Tour, :mod:`core.tours`). What's New reads it to offer "Open it", the New! badge and
the per feature button key on it, and the two CI scripts check a fragment's ``feature`` and
``guidance`` against it.

Keys are ``<area>.<slug>`` under :data:`core.help_registry.KEY_PATTERN`, like help keys and
tour targets, so one grammar covers every registry. ``guild`` covers ``guild.*`` when a
What's New button asks for a whole area.

Import-light on purpose: dataclasses and typing only, no Django at import, because
``.github/scripts/check_changelog_fragment.py`` and ``check_pr_description.py`` import it the
way they import :mod:`plfog.changelog`, with no settings. Anything that needs Django (URL
reversing, a member's guild) is imported inside the function that needs it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

if TYPE_CHECKING:
    from membership.models import Guild, Member


@dataclass(frozen=True)
class Guidance:
    """One feature as a member is taught it.

    ``url_name`` is where the feature lives; ``url_kwargs`` resolves the URL's arguments for one
    member (the :class:`core.tours.Tour` shape) and may raise ``ValueError`` when the member has
    nothing to open it on, in which case the caller hides the link. ``query`` lands on a page's
    tab, as the guild page reads ``?tab=``. ``steps`` are 2 to 5 plain lines, written for
    members (STANDARDS §13). ``clip`` and ``tour`` are the materials a fragment may declare
    with ``guidance = "clip"`` or ``"tour"``; ``help_key`` is the matching Info View key, when
    one exists.
    """

    key: str
    title: str
    url_name: str
    steps: tuple[str, ...]
    url_kwargs: Callable[[Member], dict[str, Any]] | None = None
    query: dict[str, str] | None = None
    clip: str | None = None
    tour: str | None = None
    help_key: str | None = None

    def url(self, kwargs: dict[str, Any]) -> str:
        """Where this feature lives, from ``url_kwargs`` already resolved for a member.

        Split from :func:`url_for` so a caller drawing many entries (What's New) can resolve a
        member's guild once per resolver and build each URL from it.
        """
        from django.urls import reverse

        url = reverse(self.url_name, kwargs=kwargs)
        return f"{url}?{urlencode(self.query)}" if self.query else url


def _a_guild(member: Member, *, staffed_only: bool) -> Guild:
    """The guild a member's guild entries open: one they staff, else one they belong to, else,
    for an admin or officer who can open every guild page, the first active one.

    ``staffed_only`` is for pages that need lead authority (the guild edit page), where a plain
    membership is not enough. Raises ``ValueError`` when there is no such guild.
    """
    from membership.models import Guild

    guild = member.staffed_guilds.order_by("name").first()
    if guild is None and not staffed_only:
        guild = Guild.objects.filter(memberships__member=member, is_active=True).order_by("name").first()
    if guild is None and (member.is_fog_admin or member.is_guild_officer):
        guild = Guild.objects.filter(is_active=True).order_by("name").first()
    if guild is None:
        raise ValueError(f"Member {member.pk} has no guild to open this guidance on")
    return guild


def _review_queue(member: Member) -> dict[str, Any]:
    """Manage Classes admits fog admins and CMS Administrators only (``classes_review_access_required``).

    A guild lead or Class Reviewer reaches a class through its signed review link instead, so
    for them there is no page to open and the caller hides the link.
    """
    from membership.models import AdminCapability

    if member.is_fog_admin or member.has_admin_capability(AdminCapability.Capability.CLASS_APPROVER):
        return {}
    raise ValueError(f"Member {member.pk} cannot open Manage Classes; guild reviewers review from the email")


def _guild_slug(member: Member) -> dict[str, Any]:
    return {"slug": _a_guild(member, staffed_only=False).slug}


def _staffed_guild_pk(member: Member) -> dict[str, Any]:
    return {"pk": _a_guild(member, staffed_only=True).pk}


def _guild_tab(key: str, title: str, tab: str, steps: tuple[str, ...], **extra: Any) -> Guidance:
    """A feature that is one tab of the guild page."""
    return Guidance(
        key=key,
        title=title,
        url_name="hub_guild_detail",
        url_kwargs=_guild_slug,
        query={"tab": tab},
        steps=steps,
        **extra,
    )


_GUIDANCE_LIST = (
    # Home
    # What's New itself ships in part 2 of #1007, on this same stack; registered now so part 2's fragment can name it.
    Guidance(
        key="home.whats-new",
        title="What's New",
        url_name="hub_home",
        steps=(
            "On Home, press What's New in the header.",
            "Pick the tab for your role: For Members, For Guild Staff or For Admins.",
            "Press Open it on an entry to go to that feature.",
        ),
    ),
    Guidance(
        key="home.door-code",
        title="Door Code",
        url_name="hub_home",
        steps=(
            "On Home, find the Door Code box beside the title on a wide screen, or the card under the quick links.",
            "Press Tap to reveal to see the code, and Tap to hide to mask it again.",
        ),
    ),
    Guidance(
        key="home.upcoming",
        title="Upcoming on Home",
        url_name="hub_home",
        steps=(
            "On Home, the Upcoming card rotates through classes, events, guild meetings, studio hours and your orientations.",
            "Use the arrows or the dots to move, and the pause button to hold it. A pause lasts until you press play or log out.",
            "Press Flyer on an event or class without a photo to open its printable flyer.",
        ),
    ),
    Guidance(
        key="home.spotlight",
        title="Spotlight Messages",
        url_name="hub_home",
        steps=(
            "Spotlight messages crawl across one line at the top of Home. Hover to hold them still and click a link.",
            "Press the X to hide the line until a new message arrives.",
            "Admins write them in Admin Tools under Spotlight, with start and end dates and a crawl speed slider.",
        ),
    ),
    Guidance(
        key="home.polls",
        title="Polls",
        url_name="hub_home",
        steps=(
            "While a poll is open, Home shows a Poll card near the bottom. Tap an answer to vote; the results show right away.",
            "Hide folds the card to one line once you have voted, and Show opens it again.",
            "See Past Polls under the poll lists every closed poll with its results and your answer.",
            "Admins post and close polls from Admin Tools under Polls.",
        ),
    ),
    Guidance(
        key="home.tour",
        title="Take the Tour",
        url_name="hub_home",
        tour="member-welcome",
        steps=(
            "New members see a Take the tour banner at the top of Home. Press Take the tour to start, or close it to skip.",
            "Take the tour again any time from the Guided Tours card on the Help page.",
        ),
    ),
    # Guilds
    Guidance(
        key="guild.page",
        title="Your Guild Page",
        url_name="hub_guild_detail",
        url_kwargs=_guild_slug,
        help_key="guild.join-leave",
        steps=(
            "Open Guilds in the sidebar and pick your guild.",
            "The Overview shows the guild's latest announcement, its Next Meeting card and its location lights.",
            "The tabs along the top hold the rest: Guild Calendar, Orientations, Meetings, Cubbies, Wiki and Polls, where the guild has them.",
        ),
    ),
    _guild_tab(
        "guild.cubbies",
        "Guild Cubbies",
        "cubbies",
        (
            "Open your guild's page and press the Cubbies tab.",
            "Tap an open cubby to rent it: pick what you pay each month, at or above the guild's minimum.",
            "Your Cubby shows your next charge and lets you change your amount, update your card or cancel.",
            "When every cubby is taken, press Notify Me When One Opens to join the waitlist.",
        ),
    ),
    Guidance(
        key="guild.voting",
        title="Guild Voting",
        url_name="hub_guild_voting",
        help_key="voting.rank-guilds",
        steps=(
            "Open Guild Voting in the sidebar.",
            "Rank your 1st, 2nd and 3rd choice guilds. Your ballot sticks and counts every month until you change it.",
        ),
    ),
    _guild_tab(
        "guild.orientations",
        "Orientations",
        "orientations",
        (
            "Open your guild's page and press the Orientations tab.",
            "Pick an open time to book. Your booking has a conversation for questions and photos, and your Orientor replies there.",
            "If a time no longer works for the Orientor, they move your booking or ask you first. Your payment stays with the booking.",
        ),
        help_key="orientation.book-slot",
    ),
    _guild_tab(
        "guild.reservations",
        "Equipment Reservations",
        "reservations",
        (
            "Open your guild's page and press the Reservations tab, or Reservations in the sidebar.",
            "Pick a piece of equipment and a time to reserve it. Any orientation it needs is listed on its page.",
            "Each reservation has a conversation with the equipment managers. A New message tag marks a reply you have not read.",
        ),
    ),
    _guild_tab(
        "guild.wiki",
        "Guild Wiki",
        "wiki",
        (
            "Open your guild's page and press the Wiki tab for the guild's own pages.",
            "Press Edit on a page to write. Print, History and the guild tools are in its More menu.",
        ),
    ),
    _guild_tab(
        "guild.meetings",
        "Guild Meetings",
        "meetings",
        (
            "Open your guild's page and press the Meetings tab.",
            "Meeting notes, files and links show newest meeting first.",
            "Guild leads post notes from the guild's edit page. Edit notes on the tab takes them there.",
        ),
        help_key="guild.meeting-notes",
    ),
    _guild_tab(
        "guild.polls",
        "Guild Polls",
        "polls",
        (
            "Open your guild's page and press the Polls tab. It shows once the guild has a poll.",
            "Tap an answer to vote once. The results show right away.",
            "Guild leads, staff, officers and admins post and close the guild's polls from the tab, one open poll at a time.",
        ),
    ),
    Guidance(
        key="guild.staff",
        title="Guild Staff",
        url_name="hub_guild_edit",
        url_kwargs=_staffed_guild_pk,
        query={"tab": "staff"},
        help_key="guild.manage-staff",
        tour="guild-lead",
        steps=(
            "Open your guild's edit page and press the Staff tab.",
            "Add co-leads, secretaries, treasurers and Orientors. Every staff role carries lead authority.",
            "Switch Class Reviewer on for the staff who review the guild's classes. Review requests go only to the lead and those reviewers.",
        ),
    ),
    Guidance(
        key="wiki.home",
        title="Member Wiki",
        url_name="hub_wiki_home",
        help_key="nav.wiki",
        steps=(
            "Open Member Wiki in the sidebar.",
            "Pages Waiting For You lists pages with their sections ready and nothing written yet. Tap one to start writing.",
            "In the editor, the Panel menu adds Info, Tip, Warning and Danger boxes. There are buttons for a checklist, a settings box and a divider too.",
        ),
    ),
    Guidance(
        key="calendar.events",
        title="Community Calendar",
        url_name="hub_community_calendar",
        help_key="calendar.filter",
        steps=(
            "Open Calendar in the sidebar.",
            "Every event has its own page with the photo, the date, the place, Get directions and how many people are going.",
            "RSVP with one tap and say how many you are bringing. Anyone can RSVP with a name and an email.",
        ),
    ),
    # Classes
    Guidance(
        key="classes.catalog",
        title="Classes",
        url_name="classes:public_list",
        help_key="class.register",
        steps=(
            "Open Classes in the sidebar to browse every class you can book.",
            "A class with several dates lists each date as its own run. Pick the date that works for you.",
            "Register from the class page.",
        ),
    ),
    Guidance(
        key="classes.teach",
        title="Teaching",
        url_name="classes:teach_overview",
        help_key="teach.create-class",
        tour="instructor",
        steps=(
            "Open Teaching in the sidebar to see the classes you host.",
            "Write a class in the builder, step by step. On a phone the steps are one button showing where you are.",
            "Submit it for review. The conversation on the class page is where you and the reviewers talk, with photos.",
            "Press Remind lead while it waits, Duplicate to run it again, or Delete a draft from the editor.",
        ),
    ),
    Guidance(
        key="classes.email-past-students",
        title="Email My Past Students",
        url_name="hub_compose",
        query={"audience": "students"},
        steps=(
            "Open Teaching, then My Classes, and press Email Students. It starts on My Past Students.",
            "Everyone who took any of your classes gets it once, and you can uncheck anyone first. To email one class instead, pick it under Your students.",
            "Under Feature a class, pick one of your classes to show its photo, date, price and a Register button.",
            "Each email ends with a link that stops your emails to that person.",
        ),
    ),
    Guidance(
        key="classes.review",
        title="Class Review",
        url_name="classes:admin_classes",
        url_kwargs=_review_queue,
        help_key="admin.review-queue",
        steps=(
            "Admins open Manage Classes from Admin Tools. A guild lead or Class Reviewer presses Review in the review email, or under Needs Attention on Guild Settings.",
            "Approve, request changes or deny each class waiting on you. A request for changes starts a conversation with the instructors.",
            "One email at 8 AM lists every class still waiting on you. A class waiting 3 days or more shows in red on Manage Classes and on Home for admins.",
        ),
    ),
    # Admin and guild tools
    Guidance(
        key="admin.tools",
        title="Admin Tools",
        url_name="hub_admin_tools",
        help_key="admin.tools-grid",
        tour="admin",
        steps=(
            "Open Admin Tools, or Guild Tools if you help run a guild, from the sidebar.",
            "Cards are grouped into Manage, Tools, Reports and Webmaster. You see only the cards you can use.",
            "If you help run more than one guild, pick the guild at the top of the page.",
        ),
    ),
    Guidance(
        key="admin.payment-report",
        title="Payment Report",
        url_name="classes:admin_registrations",
        steps=(
            "Open Registrations from Admin Tools and set the date range.",
            "Press Payment Report to open Reconciliation on the same dates.",
        ),
    ),
    Guidance(
        key="admin.reconciliation",
        title="Reconciliation",
        url_name="billing_admin_reports",
        help_key="admin.reconciliation",
        steps=(
            "Open Reconciliation from Admin Tools.",
            "Owed now is what to pay instructors, Orientors and equipment managers by hand. Not yet due lists shares whose class has not happened yet.",
            "A transfer Stripe is retrying shows in its own column. Do not pay it by hand.",
        ),
    ),
    Guidance(
        key="admin.email-members",
        title="Email Members",
        url_name="hub_announcements",
        help_key="announcements.compose",
        steps=(
            "Open Email Members from Admin Tools or Guild Tools, or the Email Members button on a guild page.",
            "Write one email for a class, a guild, the guild leads, all instructors or everyone. It also sends an app notification and, for a guild or everyone, a Discord post.",
            "Instructors can pick My Past Students to email everyone who took any of their classes.",
            "The Mailing Lists tab opens your guild's mailing list.",
        ),
    ),
    Guidance(
        key="admin.manage-members",
        title="Manage Members",
        url_name="hub_admin_members",
        steps=(
            "Open Manage Members from Admin Tools and pick a member.",
            "The Permissions tab lists every guild they lead or help run, with a Class Reviewer switch for each, and the Can create events switch.",
            "Add them to a guild's staff, change a title or make them a guild lead from the same tab.",
        ),
    ),
    Guidance(
        key="admin.events",
        title="Manage Events",
        url_name="hub_admin_events",
        help_key="admin.event-review",
        steps=(
            "Open Manage Events from Admin Tools.",
            "Events are listed by what is waiting for a review, scheduled, live and past. Each has its own page with Approve, Request changes, Decline, Duplicate, Unpublish and Delete.",
            "An event published without an announcement is marked Not announced, with Announce now for when it is time.",
        ),
    ),
    Guidance(
        key="admin.site-activity",
        title="Site Activity",
        url_name="manage_activity",
        help_key="admin.activity",
        steps=(
            "Open Site Activity from Admin Tools.",
            "The Email Queue tab shows what waits for the 8 AM review email, the reminders going out in the next 24 hours, and the morning emails sent this week.",
        ),
    ),
    Guidance(
        key="admin.discount-codes",
        title="Discount Codes",
        url_name="classes:admin_discount_codes",
        help_key="admin.discount-codes",
        steps=(
            "Open Discount Codes from Admin Tools.",
            "Re-assign a code to another class run. Each run shows its date and whether it is a draft or waiting for review.",
        ),
    ),
    # Feedback and account
    Guidance(
        key="feedback.requests",
        title="Feedback",
        url_name="hub_beta_feedback",
        steps=(
            "Open Feedback in the sidebar.",
            "Each of your requests has a conversation. When the team replies you get a notice, and you can reply back with photos.",
            "A closed request says what to do instead. You can still reply if you need more help.",
        ),
    ),
    Guidance(
        key="account.notifications",
        title="Notification Settings",
        url_name="hub_user_settings",
        query={"tab": "notifications"},
        help_key="settings.notifications",
        steps=(
            "Open Settings from your profile menu and press Notifications.",
            "Turn push on with Push On This Device. In the iPhone and Android apps it works once the app is up to date.",
        ),
    ),
)

#: Every registered feature by key. ``tests/core/guidance_spec.py`` holds each key to
#: ``KEY_PATTERN``, each ``tour`` to ``TOURS``, each ``help_key`` to ``HELP_KEYS`` and each
#: ``url_name`` to the URLconf.
GUIDANCE: dict[str, Guidance] = {guidance.key: guidance for guidance in _GUIDANCE_LIST}


def covers(key: str, feature: str) -> bool:
    """Whether ``key`` is ``feature`` or an area over it (``guild`` covers ``guild.cubbies``)."""
    return feature == key or feature.startswith(key + ".")


def registered(key: str) -> bool:
    """Whether ``key`` is a registered feature or an area with one under it: what a badge or a button may name."""
    return any(covers(key, found) for found in GUIDANCE)


#: What an area key is called where a title is needed (a button's label, the modal's title); any other area keeps its own name.
AREA_TITLES = {"admin": "Admin Tools", "guild": "Guilds", "home": "Home", "classes": "Classes"}


def title_for(key: str) -> str:
    """What ``key`` is called: the registered title, :data:`AREA_TITLES` for an area, else the key's last segment in Title Case."""
    if key in GUIDANCE:
        return GUIDANCE[key].title
    return AREA_TITLES.get(key) or key.rsplit(".", 1)[-1].replace("-", " ").title()


def url_for(key: str, member: Member) -> str:
    """Where ``key`` lives for ``member``, resolved lazily so this module imports without Django.

    Raises ``KeyError`` for an unregistered key and ``ValueError`` when the member has nothing to
    open it on (a guild tab for a member in no guild); the caller hides the link.
    """
    guidance = GUIDANCE[key]
    return guidance.url(guidance.url_kwargs(member) if guidance.url_kwargs is not None else {})
