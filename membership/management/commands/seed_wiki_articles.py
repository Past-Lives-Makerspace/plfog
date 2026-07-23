"""Seed the plain-language "how it works" guides onto the Wiki page.

Writes one :class:`~membership.models.WikiArticle` per guide (idempotent
``update_or_create`` keyed on ``(page, slug)``) and fills the page intro when it is still
blank. Re-running refreshes each guide's title/body/order in place and never duplicates a
row, so correcting the copy here and re-running is the intended workflow. Mirrors
``seed_floor_geometry`` in shape: data lives in a module-level list, ``--dry-run`` previews
without writing, and the command reports what it wrote and skipped.

The three fixed blocks (parking, who-to-contact, code of conduct) stay Josh's to fill and
are deliberately not touched here.

Usage::

    manage.py seed_wiki_articles [--dry-run]
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

#: Welcome blurb for the top of the page (OrgInfoPage.intro), not an article. Only written
#: when the intro is still blank, so a later admin edit is never clobbered.
INTRO = """\
Welcome to the Past Lives member hub. This is where the makerspace runs day to day: your \
guilds, the class catalog, the community calendar, guild voting, and your account settings.

Use the menu on the left to get around:

- Home: your dashboard and recent activity.
- Class Catalog: every class and workshop you can sign up for.
- Community Calendar: everything happening at the space, in one place.
- Spaces: the floor plan and every studio and shared area.
- Guild Voting: rank the guilds you want the monthly funding pool to support.
- Wiki: this page, plus answers to common questions.
- Member Directory: find other members by skill or guild.
- Guilds: jump straight to any guild's page.

The guides below cover the parts people ask about most. Stuck? The Who's Who section \
further down says who to contact."""


#: The eight code-verified guides, tightened to an "explain it like I'm 14" register.
#: Order here sets ``sort_order`` (and so the table-of-contents order on the page).
#:
#: NOTE (blocked on Josh): the "Connecting Discord" guide is seeded WITHOUT the slash-command
#: list (/link, /join-guild, /vote, /whats-on, /info, /schedule-orientation). The commands
#: exist in code but the interactions endpoint is gated on DISCORD_BOT_TOKEN and
#: DISCORD_INTERACTIONS_PUBLIC_KEY being set on Render, which is unconfirmed. Append the list
#: to this guide only once Josh confirms the production bot is live.
ARTICLES: list[dict[str, str]] = [
    {
        "slug": "guilds-and-guild-pages",
        "title": "Guilds and guild pages",
        "body": """\
A guild is a group built around a craft or interest, like a woodshop, a ceramics studio, or a print guild. Guilds run their own classes, hold meetings, and get a share of the monthly funding pool based on how members vote.

Find a guild:
1. Open Guilds at the bottom of the left menu and pick one, or
2. Browse the full guild directory to see them all.

Each guild page has tabs across the top:
- Overview: what the guild does, its announcements, and meeting times.
- Guild Calendar: that guild's meetings, classes, and orientations.
- FAQ: common questions, once the guild adds some.
- Meeting Notes and Gallery: shown when the guild posts them.

Join a guild: open its page and click "Join This Guild" in the Get Involved panel. It is free, and you can be in as many guilds as you want. To leave one, open User Settings, go to the Guilds tab, and switch that guild off.

Anyone can propose an announcement for a guild, but a guild lead or an admin has to approve it first, so yours may not show up right away.

Only guild leads, their staff, and admins can edit a guild page. If you help run a guild and need edit access, ask an admin.""",
    },
    {
        "slug": "orientations",
        "title": "Orientations",
        "body": """\
Many guilds ask you to finish a short orientation before you use their tools and equipment. It covers safety and how the space works. You only do it once per guild.

Book one:
1. Open the guild's page.
2. In the orientation panel, click "Join an Orientation."
3. Pick a posted time, or ask for a different one if the guild allows custom requests.

Your request is not confirmed until a guild lead approves it. You will get an email with the details, and another once the time is locked in, so check your email (spam folder included) after you request.

Need to cancel or reschedule? Use the link in your confirmation email, or contact the guild lead.""",
    },
    {
        "slug": "guild-voting",
        "title": "Guild voting",
        "body": """\
Every month, members vote on how the guild money gets split. You pick your top three guilds and each one earns points: 1st pick 5, 2nd pick 3, 3rd pick 2. At the end of the month we add up everyone's points, and that sets how much each guild gets.

Vote or change your vote:
1. Click Guild Voting in the left menu.
2. Pick your first, second, and third choice guilds.
3. Click Submit Vote (or Update Vote if you have voted before).

Vote once and it sticks. Your picks keep counting every month until you change them.

You can also vote from Discord: run /vote once your Discord account is linked (see Connecting Discord).

If the page says your account is not linked to a membership, ask an admin to connect it, and then you can vote.""",
    },
    {
        "slug": "taking-a-class",
        "title": "Taking a class",
        "body": """\
Browse and sign up for classes from the Class Catalog.

Find a class:
1. Click Class Catalog in the left menu.
2. Open any class to see its description, dates, price, and open spots.

Sign up:
1. On the class page, click to register.
2. Fill in your details and sign any waiver.
3. Free classes confirm right away. Paid ones send you to a secure checkout, and your spot is locked in once the payment goes through.

Good to know:
- You do not need to log in to register. Just use your email.
- Register with the email on your membership and any member discount is added for you.
- If a class is full, join the waitlist and we email you if a spot opens.
- You cannot join a class after it has started.

To view or cancel your spot, use the link in your confirmation email.""",
    },
    {
        "slug": "teaching-a-class",
        "title": "Teaching a class",
        "body": """\
Any active member can propose and teach a class. You do not need a special role.

Start your class:
1. Open any guild page and click "Teach a Class" in the Get Involved panel.
2. Fill in the details: title, description, dates, price, and how many people can join.
3. Add at least one photo. A class needs one before you can submit it.
4. Submit it for review.

How approval works:
- If your class is tied to a guild that has a lead, that guild lead reviews it first, then an admin gives the final yes.
- If it is not tied to a guild with a lead, an admin reviews it directly.
- Once it is approved, your class goes live and opens for sign-ups.

You get a notice as your class moves through review. From the teaching area you can also see who signed up, message them, and manage your waitlist.""",
    },
    {
        "slug": "the-community-calendar",
        "title": "The community calendar",
        "body": """\
The Community Calendar puts everything happening at the space in one place: guild meetings, classes, workshops, and community events.

Get around:
1. Click Community Calendar in the left menu.
2. Use the Calendar tab for a week or month grid, or the Events tab for a plain list.
3. Use the colored filters to show or hide a guild or calendar. Your choices are saved on this device.

Add it to your own calendar app:
1. Click Export Calendar (top right of the Calendar tab).
2. Pick "Subscribe via webcal" to keep your app in sync, or "Download .ics" for a one-time import into Apple Calendar or Outlook.

When member proposals are open, click "Propose an event" and an admin reviews it before it shows up. Admins can add events directly.

Each guild has its own calendar too, on the Guild Calendar tab of its page.""",
    },
    {
        "slug": "connecting-discord",
        "title": "Connecting Discord",
        # Slash-command list intentionally omitted; see the module note above.
        "body": """\
Linking your Discord account lets the app send you notices as a direct message, and keeps your guild memberships in sync between Discord and the hub.

Connect from the hub:
1. Open User Settings, then the Notifications tab.
2. Click to connect Discord and approve the sign-in on Discord.
3. Once you are linked, your guilds sync automatically and you can choose to get notices as a DM.

Connect from Discord: if a link is posted in the Past Lives Discord, you can tap it to connect in one step. If your Discord email matches your Past Lives account, you are linked right away, with no separate login.

To stop DMs and unlink, open User Settings, go to the Notifications tab, and disconnect Discord.""",
    },
    {
        "slug": "notifications-and-your-settings",
        "title": "Notifications and your settings",
        "body": """\
You choose how the app reaches you.

It can reach you on four channels:
- In the app, on the bell icon.
- By email.
- By web push in your browser.
- By Discord direct message, once you link Discord.

Set it up:
1. Open User Settings, then the Notifications tab.
2. Turn each kind of notice on or off, per channel.

A few important notices always go out and cannot be turned off. Everything else is yours to tune. Discord DMs only work after you connect your Discord account.""",
    },
]


class Command(BaseCommand):
    help = (
        "Seed the plain-language how-it-works guides onto the Wiki page. Idempotent "
        "(keyed on slug); use --dry-run to preview without writing."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing any rows.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.models import OrgInfoPage, WikiArticle

        dry_run = bool(options["dry_run"])
        page = OrgInfoPage.load()
        totals = {"created": 0, "updated": 0}

        for offset, guide in enumerate(ARTICLES):
            created = self._sync_article(page, offset, guide, WikiArticle, dry_run=dry_run)
            totals["created" if created else "updated"] += 1

        intro_action = self._sync_intro(page, dry_run=dry_run)
        self._report(totals, intro_action, dry_run=dry_run)

    @staticmethod
    def _sync_article(page: Any, offset: int, guide: dict[str, str], article_model: Any, *, dry_run: bool) -> bool:
        """Create or refresh one guide, keyed on its slug. Returns True when it is new."""
        existing = article_model.objects.filter(page=page, slug=guide["slug"]).first()
        values = {"title": guide["title"], "body": guide["body"], "sort_order": offset, "is_published": True}
        if existing is None:
            if not dry_run:
                article_model.objects.create(page=page, slug=guide["slug"], **values)
            return True
        for name, value in values.items():
            setattr(existing, name, value)
        if not dry_run:
            existing.save(update_fields=[*values])
        return False

    @staticmethod
    def _sync_intro(page: Any, *, dry_run: bool) -> str:
        """Fill the page intro only when it is still blank — never clobber an admin edit."""
        if page.intro.strip():
            return "left as is"
        if not dry_run:
            page.intro = INTRO
            page.save(update_fields=["intro", "updated_at"])
        return "set"

    def _report(self, totals: dict[str, int], intro_action: str, *, dry_run: bool) -> None:
        prefix = "Would seed" if dry_run else "Seeded"
        self.stdout.write(
            f"{prefix} {len(ARTICLES)} guide(s): "
            f"{totals['created']} added, {totals['updated']} refreshed. Intro {intro_action}."
        )
        self.stdout.write(
            self.style.WARNING(
                "Discord slash-command list held out of the Connecting Discord guide pending "
                "confirmation the production bot is live."
            )
        )
