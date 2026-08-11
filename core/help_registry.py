"""The help-key registry — the in-code spine of the help center.

Every hoverable Info View target, guided-tour step, and help deep link resolves
through this module. Keys are ``<area>.<action-slug>`` (lowercase, dots and
hyphens only — ``KEY_PATTERN`` is THE regex; companion specs import it rather
than restating their own). Entries may land before their articles ship:
``url_for`` degrades to ``/help/`` until the article is seeded and published.

Cross-app by design (hub renders it, core serves it as JSON, tours reference
it), so it lives in ``core`` — but the article lookup imports ``membership``
lazily to keep ``core`` import-clean.
"""

from __future__ import annotations

import re
from typing import TypedDict


class HelpKeyEntry(TypedDict):
    """One registry entry — the contract Specs B/C/D consume."""

    title: str  # short human label, e.g. "Rank your top 3"
    short_text: str  # 1-2 plain sentences for the Info View hover panel (<= 200 chars)
    article_slug: str | None  # WikiArticle.slug the key deep-links into; None = annotation-only key
    anchor: str | None  # heading id inside that article ([a-z0-9-]+; convention: key with "." -> "-");
    #   None when article_slug is None


HELP_KEYS: dict[str, HelpKeyEntry] = {
    "voting.rank-guilds": {
        "title": "Rank your top 3",
        "short_text": (
            "Pick your 1st, 2nd, and 3rd choice guilds. Your ballot sticks and counts every month until you change it."
        ),
        "article_slug": "guild-voting",
        "anchor": "voting-rank-guilds",
    },
    "orientation.book-slot": {
        "title": "Book an orientation",
        "short_text": (
            "Pick an open slot to get oriented on a guild's space and tools. "
            "Your booking counts once a guild lead confirms it."
        ),
        "article_slug": "getting-oriented",
        "anchor": "orientation-book-slot",
    },
    "teach.create-class": {
        "title": "Create a class",
        "short_text": (
            "Draft a class and submit it — the portal opens after the one-time instructor orientation. "
            "A guild lead or admin reviews every class before it goes live."
        ),
        "article_slug": "become-an-instructor",
        "anchor": "teach-create-class",
    },
    "teach.become-instructor": {
        "title": "Become an instructor",
        "short_text": (
            "Complete the short instructor orientation once and the teaching portal unlocks — "
            "no waiting on an admin. Every class is still reviewed before it publishes."
        ),
        "article_slug": "become-an-instructor",
        "anchor": "teach-become-instructor",
    },
    "guild.manage-staff": {
        "title": "Manage guild staff",
        "short_text": (
            "Add co-leads, secretaries, treasurers, and orienters on the Staff tab. "
            "Every staff role carries full lead authority."
        ),
        "article_slug": "guild-staff-roles",
        "anchor": "guild-manage-staff",
    },
    "calendar.subscribe": {
        "title": "Subscribe to the calendar",
        "short_text": (
            "Add the community calendar to your own calendar app with the .ics link. "
            "New events show up there automatically."
        ),
        "article_slug": "community-calendar",
        "anchor": "calendar-subscribe",
    },
    "directory.search-filter": {
        "title": "Search the directory",
        "short_text": (
            "Search the member directory by name or skill to find other members. "
            "Each member controls what their profile shows."
        ),
        "article_slug": "member-directory",
        "anchor": "directory-search-filter",
    },
    "home.dashboard": {
        "title": "Your home dashboard",
        "short_text": (
            "Your home base: what's coming up, the latest from your guilds, and quick links into the rest of FOG."
        ),
        "article_slug": "welcome-to-fog",
        "anchor": "home-dashboard",
    },
    "guild.join-leave": {
        "title": "Join or leave a guild",
        "short_text": (
            "Join free from any guild page — you're on the roster and its announcement emails. "
            "Leave any time from the Guilds tab in Settings."
        ),
        "article_slug": "guilds-and-guild-pages",
        "anchor": "guild-join-leave",
    },
    "orientation.request-custom-time": {
        "title": "Request a custom time",
        "short_text": (
            "If no posted time works, propose your own. A guild lead still has to confirm it. "
            "Only shown when the guild allows custom requests."
        ),
        "article_slug": "getting-oriented",
        "anchor": "orientation-request-custom-time",
    },
    "orientation.cancel-booking": {
        "title": "Cancel your orientation",
        "short_text": (
            "Cancel from the guild page or any orientation email, before or after it's confirmed. "
            "Request a new time whenever you're ready."
        ),
        "article_slug": "getting-oriented",
        "anchor": "orientation-cancel-booking",
    },
    "voting.monthly-cycle": {
        "title": "The monthly voting cycle",
        "short_text": (
            "Each cycle is a calendar month, closing on its last day. Minutes into the new month the standings "
            "are snapshotted and results are emailed to every active member automatically."
        ),
        "article_slug": "guild-voting",
        "anchor": "voting-monthly-cycle",
    },
    "voting.live-standings": {
        "title": "Live standings",
        "short_text": (
            "Watch the tally move all month. Current Standings is live; Last Month's Results shows the latest "
            "locked-in snapshot and funding split."
        ),
        "article_slug": "guild-voting",
        "anchor": "voting-live-standings",
    },
    "class.register": {
        "title": "Register for a class",
        "short_text": (
            "Sign up from the class page — no account needed. Free classes confirm right away; "
            "paid ones go through a secure checkout. Your member email applies member pricing automatically."
        ),
        "article_slug": "taking-a-class",
        "anchor": "class-register",
    },
    "class.join-waitlist": {
        "title": "Join the waitlist",
        "short_text": (
            "Sold-out class? Join the waitlist free. When a spot opens, "
            "the next person in line gets an email with a link to claim it."
        ),
        "article_slug": "taking-a-class",
        "anchor": "class-join-waitlist",
    },
    "class.manage-registration": {
        "title": "Manage your registration",
        "short_text": (
            "Your confirmation email has a personal link to view or cancel your registration — "
            "no login needed. Refunds for paid classes are handled by an admin."
        ),
        "article_slug": "taking-a-class",
        "anchor": "class-manage-registration",
    },
    "calendar.filter": {
        "title": "Show or hide calendars",
        "short_text": (
            "Click a colored filter chip to hide or show that guild or calendar. Your picks are saved on this device."
        ),
        "article_slug": "community-calendar",
        "anchor": "calendar-filter",
    },
    "event.propose": {
        "title": "Propose an event",
        "short_text": (
            "Any member can propose an event for the Community Calendar. "
            "A guild lead or admin reviews it before it publishes."
        ),
        "article_slug": "propose-an-event",
        "anchor": "event-propose",
    },
    "announcement.propose": {
        "title": "Suggest an announcement",
        "short_text": (
            "Anyone can suggest an announcement for a guild. "
            "A guild lead or admin approves it before it appears on the guild page."
        ),
        "article_slug": "announcements",
        "anchor": "announcement-propose",
    },
    "directory.visibility": {
        "title": "Your directory card",
        "short_text": (
            "Set your whole listing to Public or Hidden, then toggle each field on your card. "
            "Anything hidden stays private to staff."
        ),
        "article_slug": "member-directory",
        "anchor": "directory-visibility",
    },
    "teach.submit-for-review": {
        "title": "Submit a class for review",
        "short_text": (
            "Submitting sends your draft to its guild lead (if the guild has one), then an admin. "
            "It publishes only when every reviewer approves."
        ),
        "article_slug": "become-an-instructor",
        "anchor": "teach-submit-for-review",
    },
    "teach.email-students": {
        "title": "Email your students",
        "short_text": (
            "Tick students on the Registrations tab, write your message, and send. "
            "You can only email people registered for your own classes."
        ),
        "article_slug": "run-your-class",
        "anchor": "teach-email-students",
    },
    "teach.duplicate-run": {
        "title": "Offer a class on new dates",
        "short_text": (
            "One click clones your class as a dateless draft that stays grouped with the original. "
            "Add the new dates, then submit it for review."
        ),
        "article_slug": "run-your-class",
        "anchor": "teach-duplicate-run",
    },
    "admin.invite-member": {
        "title": "Invite a member",
        "short_text": (
            "Emails a signup link that expires in 14 days. Resend or revoke any un-accepted invite "
            "from the panel on Manage Members."
        ),
        "article_slug": "members-and-invites",
        "anchor": "admin-invite-member",
    },
    "admin.email-aliases": {
        "title": "Manage sign-in emails",
        "short_text": (
            "Add, verify, or remove the addresses a person can sign in with. "
            "Any verified address works with the emailed sign-in code."
        ),
        "article_slug": "members-and-invites",
        "anchor": "admin-email-aliases",
    },
    "admin.review-queue": {
        "title": "The class review queue",
        "short_text": (
            "Classes waiting on review. The guild lead's gate (when there is one) comes before yours; "
            "a class publishes only when every gate approves."
        ),
        "article_slug": "reviewing-classes-admin",
        "anchor": "admin-review-queue",
    },
    "admin.refund-registration": {
        "title": "Mark a registration refunded",
        "short_text": (
            "Frees the spot, promotes the waitlist, and records the refund. It moves no money: "
            "issue the actual refund in the Stripe dashboard."
        ),
        "article_slug": "reviewing-classes-admin",
        "anchor": "admin-refund-registration",
    },
    "voting.take-snapshot": {
        "title": "Take a funding snapshot",
        "short_text": (
            "Freezes the live vote state into an immutable record. Analyzer filters do not apply; "
            "the commit always captures everything."
        ),
        "article_slug": "voting-admin",
        "anchor": "voting-take-snapshot",
    },
    "voting.send-results": {
        "title": "Send results emails",
        "short_text": (
            "Emails every voter their allocations and recorded vote, once per snapshot. "
            "The month-end auto-snapshot sends these on its own."
        ),
        "article_slug": "voting-admin",
        "anchor": "voting-send-results",
    },
    "voting.settings": {
        "title": "Voting settings",
        "short_text": (
            "Reminder timing, the pool floor, and the on/off switches for reminders and the "
            "automatic month-end snapshot."
        ),
        "article_slug": "voting-admin",
        "anchor": "voting-settings",
    },
    "guild.edit-page": {
        "title": "Edit your guild page",
        "short_text": (
            "Leads, staff, and admins edit a guild page from the Guild Settings button. "
            "Each tab saves with its own button, so save before you switch."
        ),
        "article_slug": "your-guild-page",
        "anchor": "guild-edit-page",
    },
    "guild.photo-gallery": {
        "title": "Guild photo gallery",
        "short_text": (
            "Up to 10 photos on the Images tab. They save the moment you upload; "
            "drag to reorder and add alt text so everyone can follow along."
        ),
        "article_slug": "your-guild-page",
        "anchor": "guild-photo-gallery",
    },
    "guild.staff-authority": {
        "title": "Staff share full authority",
        "short_text": (
            "Every staff role, preset or custom, carries the full authority of the guild lead. "
            "Titles are labels, not permission tiers."
        ),
        "article_slug": "guild-staff-roles",
        "anchor": "guild-staff-authority",
    },
    "orientation.recurring-hours": {
        "title": "Recurring orientation hours",
        "short_text": (
            "Weekly windows that turn into bookable slots automatically. Saving regenerates "
            "slots right away; a nightly job keeps future weeks open."
        ),
        "article_slug": "running-orientations",
        "anchor": "orientation-recurring-hours",
    },
    "orientation.respond-requests": {
        "title": "Respond to a request",
        "short_text": (
            "A request stays pending until a lead or staff member confirms it. Confirm or "
            "decline from the app or the email links; the member is emailed either way."
        ),
        "article_slug": "running-orientations",
        "anchor": "orientation-respond-requests",
    },
    "orientation.dashboard": {
        "title": "The Orientations dashboard",
        "short_text": (
            "Track requests and completions for the guilds you help run. Filter, export CSV, "
            "add a member to a slot, and mark orientations done."
        ),
        "article_slug": "running-orientations",
        "anchor": "orientation-dashboard",
    },
    "announcements.compose": {
        "title": "Compose an announcement",
        "short_text": (
            "Three steps: write the message, design the email, pick the Discord channel. "
            "Everyone in the audience always sees it in the app."
        ),
        "article_slug": "guild-announcements",
        "anchor": "announcements-compose",
    },
    "announcements.review-proposals": {
        "title": "Review member proposals",
        "short_text": (
            "Any member can propose an announcement for your guild. Nothing posts until a "
            "lead, staff member, or admin approves it."
        ),
        "article_slug": "guild-announcements",
        "anchor": "announcements-review-proposals",
    },
    "guild.events": {
        "title": "Guild events",
        "short_text": (
            "Events you add publish straight to the community calendar and your guild "
            "calendar; no approval step. Members get a heads-up in the app."
        ),
        "article_slug": "guild-events-hours-notes",
        "anchor": "guild-events",
    },
    "guild.studio-hours": {
        "title": "Studio hours",
        "short_text": (
            "Weekly windows when someone is around, shown on your guild page. "
            "Ambient only: saving them never announces or pings anyone."
        ),
        "article_slug": "guild-events-hours-notes",
        "anchor": "guild-studio-hours",
    },
    "guild.meeting-notes": {
        "title": "Meeting notes",
        "short_text": (
            "Post agendas and recaps with attachments so members can catch up. "
            "Add, edit, and delete them from the Meeting Notes tab."
        ),
        "article_slug": "guild-events-hours-notes",
        "anchor": "guild-meeting-notes",
    },
    "guild.approve-classes": {
        "title": "Approve guild classes",
        "short_text": (
            "Classes in your guild's categories wait on your review first. Approving sends "
            "the class on to an admin; it publishes only after both say yes."
        ),
        "article_slug": "approving-classes",
        "anchor": "guild-approve-classes",
    },
    # ── Annotation-only keys (Spec B / Info View §6.8) ──────────────────────
    # Stamped as data-help-key targets before their article sections exist;
    # article_slug=None per §5.1(c), so url_for degrades to /help/.
    "voting.results-history": {
        "title": "Past results",
        "short_text": (
            "Every month's locked-in results live here. Open any month to see the final "
            "standings and how the funding split landed."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "guild.staff-roster": {
        "title": "Who runs this guild",
        "short_text": (
            "The guild lead and staff who keep this guild running. Click the lead's name to see their contact card."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "guild.faq": {
        "title": "Guild FAQ",
        "short_text": (
            "Answers from the guild itself — house rules, tools, and how things work in this "
            "shop. Guild leads keep this list up to date."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "guild.manage-faq": {
        "title": "Edit the FAQ",
        "short_text": (
            "The questions and answers members see on your guild page. Each answer can embed "
            "a video or attach one document. Save with the Save FAQ button."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "guild.manage-links": {
        "title": "Edit guild links",
        "short_text": (
            "Links shown on your guild page — your site, docs, sign-up sheets. Add or remove rows, then hit Save Links."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "guild.contact-emails": {
        "title": "Custom mailing-list addresses",
        "short_text": (
            "Extra, non-member addresses that also get this guild's announcement emails — "
            "a booster, a partner org, your own inbox."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "teach.review-pipeline": {
        "title": "Needs your attention",
        "short_text": (
            "Drafts you haven't submitted yet, plus classes waiting on review. Finish a "
            "draft and submit it to send it to its reviewers."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "teach.waitlists": {
        "title": "Class waitlists",
        "short_text": (
            "Sold-out classes collect a waitlist. When a spot opens, the next person in "
            "line is emailed a link to claim it automatically."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "teach.class-schedule": {
        "title": "Dates & sessions",
        "short_text": (
            "Add one date for a one-off class, or every date in a series — students sign up once for all of them."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "teach.class-pricing": {
        "title": "Pricing & discounts",
        "short_text": (
            "Set the member discount and optional sale pricing. Members get member pricing "
            "automatically when they register with their member email."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "settings.profile": {
        "title": "Your member profile",
        "short_text": (
            "What other members see about you in the directory. Every field has its own "
            "show/hide eye — you control what appears."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "settings.contact-methods": {
        "title": "Contact methods",
        "short_text": (
            "Other ways to reach you — a website, Instagram, a booking email. You pick where each one shows."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "settings.skills": {
        "title": "Your skills",
        "short_text": (
            "List what you make and do. Members can search the directory by skill, so this is how people find you."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "settings.your-guilds": {
        "title": "Your guilds",
        "short_text": ("The guilds you belong to. Join or leave here — leaving keeps your orientation history."),
        "article_slug": None,
        "anchor": None,
    },
    "settings.email-addresses": {
        "title": "Your sign-in emails",
        "short_text": (
            "Every address you can sign in with. Add one, verify it, and set your primary — "
            "login codes work with any verified address."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "settings.notifications": {
        "title": "Notification preferences",
        "short_text": (
            "Choose which emails and push notifications you get. Turning something off here "
            "stops just that kind of message, nothing else."
        ),
        "article_slug": None,
        "anchor": None,
    },
    # ── Annotation-only keys (Spec C / guided tours §7) ─────────────────────
    # Stamped as tour step targets per §5.1(c); article_slug=None until their
    # article sections exist, so url_for degrades to /help/.
    "nav.sidebar": {
        "title": "The sidebar",
        "short_text": (
            "The sidebar gets you everywhere: guilds, classes, the calendar, your settings. "
            "On a phone, the menu button opens it."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "nav.guilds": {
        "title": "Guilds in the sidebar",
        "short_text": (
            "Every guild has its own page. Open one to see meetings, orientations, and how to join — "
            "this section collapses if you want it out of the way."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "nav.calendar": {
        "title": "Community calendar",
        "short_text": (
            "Classes, guild meetups, and events all land on one calendar. Filter it, open any event, "
            "or subscribe from your own calendar app."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "nav.help": {
        "title": "The Help page",
        "short_text": ("Short guides to how the makerspace and this app work, plus who to contact and the FAQ."),
        "article_slug": None,
        "anchor": None,
    },
    "home.get-started": {
        "title": "Your Get started list",
        "short_text": (
            "A short checklist to finish setting up — profile, photo, first guild. "
            "It disappears on its own when you're done."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "guild.edit-tabs": {
        "title": "Guild settings tabs",
        "short_text": (
            "Each tab covers one job of running your guild. Basic Information is your public page; "
            "every other tab saves on its own."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "guild.run-orientations": {
        "title": "Orientations tab",
        "short_text": (
            "Set your orientation hours and open slots here. Bookings land on the Orientations "
            "dashboard, where you confirm them and mark them done."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "guild.announcements": {
        "title": "Announcements tab",
        "short_text": (
            "Write to your members with the compose wizard, and review announcements members propose. "
            "Nothing posts until you approve it."
        ),
        "article_slug": None,
        "anchor": None,
    },
    "teach.roster": {
        "title": "Your classes and rosters",
        "short_text": (
            "Click any class to see who signed up, manage the waitlist, email your registrants, "
            "or export the roster as a CSV."
        ),
        "article_slug": None,
        "anchor": None,
    },
}

# THE key regex — Specs B/C import this instead of restating their own.
KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.[a-z0-9]+(?:-[a-z0-9]+)*$")


def entry(key: str) -> HelpKeyEntry:
    """Return the registry entry for ``key``. Unknown key raises ``KeyError`` (fail loudly)."""
    return HELP_KEYS[key]


def url_for(key: str) -> str:
    """Resolve ``key`` to the URL a "Learn more" link should point at.

    Returns the article's canonical URL plus ``#anchor`` when the article exists
    and is published. Falls back to ``/help/`` for annotation-only entries
    (``article_slug is None``) and for entries whose article isn't seeded or
    published yet (§5.1's no-deadlock rule) — resolved lazily at call time.
    """
    key_entry = entry(key)
    article_slug = key_entry["article_slug"]
    if article_slug is None:
        return "/help/"
    # Lazy import: core must not import membership at module load (circular-import guard).
    from membership.models import WikiArticle

    article = WikiArticle.objects.published().filter(slug=article_slug).select_related("category").first()
    if article is None:
        return "/help/"
    return f"{article.get_absolute_url()}#{key_entry['anchor']}"
