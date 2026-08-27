"""The Help Center's example guild — the Cartographers Guild, seeded content data.

A permanent, fictional guild that exercises every guild-page feature so the
"Running a Guild" help guides have a living page to point at. It is unlisted by
design: ``is_active=False`` keeps it out of the sidebar, the guild directory,
voting, and the My Guilds grid — only its direct URL (``/guilds/cartographers-guild/``,
linked from the guides) reaches it.

Safety contract (why the fictional members look the way they do):

- ``status=FORMER`` + ``user=None`` + blank email — every notification resolver
  skips user-less members, the auto-provision signal only fires for ACTIVE
  members, and ``airtable_pull`` can neither match (blank email) nor orphan-report
  (``airtable_record_id=None``) them. No cron, sync, email, or billing path can
  ever touch these rows.
- ``hide_from_directory=True`` — belt-and-braces: even if a fictional member were
  ever flipped ACTIVE or given a role, the directory override keeps them hidden.
- **No CommunityEvent rows** (meetings/studio hours) — published events surface on
  the public Community Calendar and Discord regardless of guild ``is_active``, so
  the example guild's meeting info lives in the cadence fields instead.
- GuildAnnouncement rows are inert by construction: nothing sends without an
  explicit ``notify_members()`` call, which only the compose/approve flows make.

``seed_example_guild()`` is idempotent (natural-key ``update_or_create``
everywhere) and runs on every deploy via ``manage.py seed_example_guild``
(render.yaml buildCommand), mirroring ``seed_help_center``.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.core.files import File

if TYPE_CHECKING:
    from membership.models import Guild, Member

EXAMPLE_GUILD_SLUG = "cartographers-guild"
EXAMPLE_GUILD_NAME = "Cartographers Guild"

# Committed repo assets reused for the example page's banner and gallery — the
# same files the real guild-content seed (migration 0039) draws from.
BANNER_STATIC_PATH = "img/guild_banners/gardeners_hero.jpg"
GALLERY_STATIC_PATHS: list[tuple[str, str]] = [
    ("img/guild_banners/art_framing_hero.jpg", "A worktable mid-project — frames, straightedges, and patience."),
    ("img/guild_banners/leatherwork_hero.jpg", "Tools of the trade laid out for an evening session."),
    ("img/guild_banners/glass_hero.jpg", "Color studies for the spring atlas plates."),
]

ABOUT = """\
This is a fictional example guild. It exists so guild leads and staff can see a fully \
built-out guild page — every section below is filled in on purpose, and the Help Center's \
"Running a Guild" guides point here.

The Cartographers Guild charts places that don't exist: imaginary coastlines, cities from \
novels, star maps for skies nobody has seen. We draft by hand and by plotter, argue about \
projections, and host a monthly Atlas Night where everyone inks one tile of a shared world.

If you can find this page, you followed a link from a guide — the guild never appears in \
the sidebar or the directory. Look around, click the tabs, and steal any idea you like for \
your own guild's page."""

WISHLIST = """\
Things the (entirely fictional) guild would love:

- A **drafting table** with a parallel bar — ours is imaginary and still wobbles.
- Technical pens, sizes 005 through 08.
- A flat file cabinet for storing maps of places that don't exist.
- One (1) working compass. For drawing circles, not for finding north. North is negotiable here.

*This wishlist demonstrates Markdown formatting — bold, lists, and emphasis all work.*"""

ESSENTIAL_RULES = """\
1. Every map needs a compass rose, even the dishonest ones.
2. Label your sea monsters.
3. Clean the plotter after use — future cartographers thank you.
4. This is an example page: the rules are fictional, but rule 3 is good advice everywhere."""

MEETING_SCHEDULE = "First Tuesdays, 7pm, in the Map Room (which does not exist)."

FAQ_LABEL = "Field Notes"

FAQ_ITEMS: list[dict[str, str]] = [
    {
        "question": "Is this a real guild?",
        "answer": (
            "No — the **Cartographers Guild** is a permanent example built for the Help Center. "
            "Every feature a guild page can use is switched on here: banner, gallery, FAQ with a "
            "video and a document, links, staff roles, announcements, meeting notes, and a wishlist.\n\n"
            "The video on this answer shows that FAQ answers can embed YouTube; the document link "
            "below it shows the attached-document slot."
        ),
        "video_url": "https://www.youtube.com/watch?v=YE7VzlLtp-4",
        "document_url": "https://members.pastlives.space/help/running-a-guild/your-guild-page/",
    },
    {
        "question": "How do I make my guild's page look like this?",
        "answer": (
            "Open your guild's page and click **Guild Settings** — every section here comes from "
            "one of its tabs. The walkthroughs live in the Help Center:\n\n"
            "- [Guild Lead Quickstart](/help/running-a-guild/guild-lead-quickstart/) — the map of everything\n"
            "- [Your Guild Page](/help/running-a-guild/your-guild-page/) — banner, gallery, FAQ, links\n"
            "- [Guild Announcements](/help/running-a-guild/guild-announcements/) — reaching your members"
        ),
        "video_url": "",
        "document_url": "",
    },
    {
        "question": "Why can't I find this guild in the sidebar?",
        "answer": (
            "It's unlisted on purpose. An inactive guild disappears from the sidebar, the guild "
            "directory, voting, and the My Guilds grid — but its page stays reachable by direct "
            "link. That's exactly how this example stays out of everyone's way."
        ),
        "video_url": "",
        "document_url": "",
    },
]

LINKS: list[tuple[str, str]] = [
    (
        "Guild Lead Quickstart (Help Center)",
        "https://members.pastlives.space/help/running-a-guild/guild-lead-quickstart/",
    ),
    ("Your Guild Page guide", "https://members.pastlives.space/help/running-a-guild/your-guild-page/"),
    ("Past Lives wiki", "https://wiki.pastlives.space"),
]

# Fictional people — display name + their staff role (or "lead" / a custom title).
# Exactly one of role/custom_title per staff row, per the model's check constraint.
LEAD_NAME = "Ada Meridian"
STAFF: list[dict[str, str]] = [
    {"name": "Niko Contour", "role": "co_lead", "custom_title": ""},
    {"name": "June Azimuth", "role": "secretary", "custom_title": ""},
    {"name": "Otto Scale", "role": "treasurer", "custom_title": ""},
    {"name": "Rhea Compass", "role": "orienter", "custom_title": ""},
    {"name": "Felix Atlas", "role": "", "custom_title": "Keeper of the Legend"},
]

ANNOUNCEMENTS: list[dict[str, str]] = [
    {
        "title": "Atlas Night: the Salt Marsh Tiles Are In",
        "body": (
            "The shared world grows again — six new tiles came back inked from July's Atlas Night, "
            "including the long-argued-over salt marsh. Come see them pinned up in the (fictional) "
            "Map Room. This announcement is an example: it shows how guild news appears on the page "
            "and on members' Home dashboards."
        ),
    },
    {
        "title": "Projection Debate Settled (Until Next Month)",
        "body": (
            "By a vote of 7 to 5, the guild's official projection for the season is the Winkel "
            "tripel. The two members who voted for the Peirce quincuncial have been asked to "
            "make their case again at the next meeting, with slides. (Also an example post.)"
        ),
    },
    {
        "title": "Welcome to the Example Guild",
        "body": (
            "If you're reading this, you found the Cartographers Guild — the Help Center's "
            "permanent example page. Announcements like this one are what guild leads publish "
            "from the Announcements tab in Guild Settings."
        ),
    },
]

MEETING_NOTES: list[dict[str, Any]] = [
    {
        "meeting_date": date(2026, 8, 4),
        "title": "August General Meeting",
        "body": (
            "**Attendance:** 12 cartographers, 1 skeptical cat.\n\n"
            "- Approved the budget for imaginary vellum.\n"
            "- The coastline subcommittee reports the coastline is still infinite.\n"
            "- Next Atlas Night: first Tuesday of September.\n\n"
            "*(These notes are examples — Meeting Notes support Markdown, and each note can "
            "carry attached files or links.)*"
        ),
        "attachments": [
            (
                "Guild Events, Hours, and Notes guide",
                "https://members.pastlives.space/help/running-a-guild/guild-events-hours-notes/",
            ),
        ],
    },
    {
        "meeting_date": date(2026, 7, 7),
        "title": "July General Meeting",
        "body": (
            "Short one this month:\n\n"
            "- The plotter is fixed. The plotter is always fixed. The plotter breaks again on Thursdays.\n"
            "- Agreed to label the sea monsters going forward (see Essential Rules).\n"
        ),
        "attachments": [],
    },
]

ORIENTATION = {
    "is_enabled": True,
    "is_closed": True,
    "closed_message": "This is an example guild — orientations here aren't real. Book one with a real guild!",
    "info": "Orientations cover the Map Room, the plotter, and which drawers bite.",
    "default_seats": 4,
    "default_location": "The Map Room (2nd floor, fictional wing)",
    "default_duration_minutes": 45,
    "thankyou_email_enabled": False,
    "thankyou_email_subject": "Welcome aboard, cartographer",
    "thankyou_email_body": (
        "Thanks for getting oriented with the Cartographers Guild! (This example text shows where "
        "a guild's post-orientation thank-you email is written.)"
    ),
}


def _example_member(name: str) -> "Member":
    """Create or refresh one inert fictional member (see the module docstring).

    FORMER + user=None + blank email: invisible to every resolver, cron, and sync.
    hide_from_directory is belt-and-braces.
    """
    from membership.models import Member, MembershipPlan

    plan = MembershipPlan.objects.order_by("pk").first()
    if plan is None:
        plan = MembershipPlan.objects.create(name="Standard", monthly_price="50.00")
    member, _ = Member.objects.update_or_create(
        full_legal_name=name,
        user=None,
        defaults={
            "membership_plan": plan,
            "status": Member.Status.FORMER,
            "member_type": Member.MemberType.VOLUNTEER,
            "fog_role": Member.FogRole.MEMBER,
            "hide_from_directory": True,
            "show_in_directory": False,
        },
    )
    return member


def _seed_media(guild: "Guild") -> None:
    """Banner + gallery from committed repo assets, filled only when blank/empty.

    Saved into media storage (R2 in prod); the fill-if-blank contract means a
    hand-tuned banner crop or curated gallery is never clobbered by a redeploy.
    """
    from membership.models import GuildImage

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if not guild.banner_image:
        with (static_dir / BANNER_STATIC_PATH).open("rb") as fh:
            guild.banner_image.save(f"{EXAMPLE_GUILD_SLUG}-banner.jpg", File(fh), save=True)
    if not guild.gallery_images.exists():
        for index, (rel_path, alt_text) in enumerate(GALLERY_STATIC_PATHS):
            with (static_dir / rel_path).open("rb") as fh:
                image = GuildImage(guild=guild, alt_text=alt_text, sort_order=index * 10)
                image.image.save(f"{EXAMPLE_GUILD_SLUG}-gallery-{index + 1}.jpg", File(fh), save=True)


def seed_example_guild() -> "Guild":
    """Create or refresh the Cartographers Guild and its fictional crew. Idempotent.

    Returns the Guild instance. Safe to run on every deploy: rows are keyed on
    natural keys (guild slug, member names scoped to example rows, item titles)
    and refreshed in place, so hand-edits to seeded fields are overwritten but
    nothing duplicates.
    """
    from membership.models import (
        Guild,
        GuildAnnouncement,
        GuildFAQItem,
        GuildLink,
        GuildMeetingNote,
        GuildMeetingNoteAttachment,
        GuildOrientationSettings,
        GuildStaffMembership,
    )

    lead = _example_member(LEAD_NAME)

    guild, _ = Guild.objects.update_or_create(
        slug=EXAMPLE_GUILD_SLUG,
        defaults={
            "name": EXAMPLE_GUILD_NAME,
            "is_active": False,
            "is_featured": False,
            "guild_lead": lead,
            "about": ABOUT,
            "wishlist": WISHLIST,
            "essential_rules": ESSENTIAL_RULES,
            "faq_label": FAQ_LABEL,
            "website_url": "https://members.pastlives.space/help/",
            "youtube_url": "https://www.youtube.com/watch?v=YE7VzlLtp-4",
            "show_members": True,
            "meeting_schedule": MEETING_SCHEDULE,
            "meeting_cadence": Guild.MeetingCadence.MONTHLY,
            "meeting_weekday": 1,  # Tuesday
            "meeting_week_of_month": 1,
            "meeting_time": time(19, 0),
            "meeting_location": "The Map Room (2nd floor)",
        },
    )

    _seed_media(guild)

    # Staff — every preset role plus a custom title, all fictional and inert.
    for entry in STAFF:
        GuildStaffMembership.objects.update_or_create(
            guild=guild,
            member=_example_member(entry["name"]),
            role=entry["role"],
            custom_title=entry["custom_title"],
        )

    for index, item in enumerate(FAQ_ITEMS):
        GuildFAQItem.objects.update_or_create(
            guild=guild,
            question=item["question"],
            defaults={
                "answer": item["answer"],
                "video_url": item["video_url"],
                "document_url": item["document_url"],
                "sort_order": index * 10,
            },
        )

    for index, (label, url) in enumerate(LINKS):
        GuildLink.objects.update_or_create(guild=guild, label=label, defaults={"url": url, "sort_order": index * 10})

    # Announcements are inert rows: nothing emails or posts without an explicit
    # notify_members() call, which only the compose/approve flows ever make.
    for item in ANNOUNCEMENTS:
        GuildAnnouncement.objects.update_or_create(
            guild=guild,
            title=item["title"],
            defaults={
                "body": item["body"],
                "moderation_state": GuildAnnouncement.ModerationState.PUBLISHED,
                "send_email": False,
            },
        )

    for note_data in MEETING_NOTES:
        note, _ = GuildMeetingNote.objects.update_or_create(
            guild=guild,
            title=note_data["title"],
            defaults={"meeting_date": note_data["meeting_date"], "body": note_data["body"]},
        )
        for index, (label, url) in enumerate(note_data["attachments"]):
            GuildMeetingNoteAttachment.objects.update_or_create(
                note=note, label=label, defaults={"url": url, "sort_order": index * 10}
            )

    # Orientation settings: enabled so the machinery shows, but CLOSED with an
    # explanatory message — no real member can ever book the fictional guild.
    GuildOrientationSettings.objects.update_or_create(guild=guild, defaults=dict(ORIENTATION))

    # NOTE deliberately absent: CommunityEvent rows (guild meetings / studio
    # hours). Published events surface on the public Community Calendar and the
    # Discord announcer regardless of guild is_active — the example guild's
    # meeting info lives in the cadence fields above instead.

    return guild
