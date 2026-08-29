"""Help-center content data — the seeded categories, P1 article bodies, and slug tables.

``seed_help_center`` reads ``CATEGORIES`` / ``ARTICLES`` into the database
(idempotently — correcting copy here and re-running is the workflow), the
screenshot harness (``tests/e2e/help_screenshots_spec.py``) walks every
article's ``screenshots`` list, and the drift guard
(``tests/membership/help_content_spec.py``) keeps bodies, ShotSpecs, and the
PNGs under ``static/help/`` in lockstep.

Authored to the §10.5 working agreement of
``docs/superpowers/plans/2026-08-10-help-center-knowledge-base.md``: every
claim code-verified, ELI14 register, permission caveats kept, GATED surfaces
(Tab/payments, Discord connect, slash commands) never mentioned.

Amendment (owner decision, 2026-08-12): the Discord-connect gate is lifted —
the prod bot has been live and member-announced since v0.22.9, and the owner
requested Discord documentation for the contributing section, so
``discord-and-fog`` documents the connect/link flows. Tab/payments and
slash-command docs stay gated.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class ShotSpec(TypedDict):
    """One screenshot request in an article's ``screenshots`` list (§9).

    The capture harness (``tests/e2e/help_screenshots_spec.py``) reads these to
    regenerate ``static/help/<article-slug>/<file>``; the drift guard
    (``tests/membership/help_content_spec.py``) keeps body images, specs, and
    files on disk in lockstep.
    """

    file: str  # "02-pick-your-guilds.png" → static/help/<article-slug>/02-pick-your-guilds.png
    page: str  # URL name ("hub_guild_voting") or literal path ("/guilds/voting/")
    # CSS selector to crop to — e.g. "[data-help-key='voting.rank-guilds']" once
    # Spec B lands, plain CSS until then. None = framed viewport shot.
    selector: str | None
    caption: str  # becomes the image's alt text in the article body
    as_role: str  # "member" | "guild_lead" | "instructor" | "admin" — whose UI to capture
    full_page: NotRequired[bool]  # default False


# Articles that exist at a URL without appearing in any browsing surface —
# excluded from search, the landing page, and category pages, but their
# canonical URL resolves normally (registry keys / tours may deep-link them).
UNLISTED_SLUGS: frozenset[str] = frozenset({"instructor-orientation"})

# Old /help/#slug landing anchors → new article slugs — all 8 legacy slugs
# (§10.3). Rewritten-in-place slugs are identity-mapped so an old anchor link
# still lands on the article page instead of a vanished landing anchor.
LEGACY_SLUG_MAP: dict[str, str] = {
    "guilds-and-guild-pages": "guilds-and-guild-pages",
    "guild-voting": "guild-voting",
    "taking-a-class": "taking-a-class",
    "orientations": "getting-oriented",
    "teaching-a-class": "become-an-instructor",
    "the-community-calendar": "community-calendar",
    "connecting-discord": "notifications",
    "notifications-and-your-settings": "notifications",
}

# LEGACY_SLUG_MAP targets approved (§10.2) but not yet seeded. Empty now that the
# ``notifications`` guide has shipped (v1.2.0) — its two legacy anchors
# ("connecting-discord", "notifications-and-your-settings") now resolve to the live
# article instead of falling back to /help/. Re-add a slug here only if a future
# legacy target is mapped before its article lands.
PENDING_LEGACY_TARGETS: frozenset[str] = frozenset()

# ── OrgInfoPage launch defaults ──────────────────────────────────────────────
# Fill-if-blank defaults for the Help landing's page blocks. ``seed_help_center``
# writes each one ONLY when the block is still blank, so an admin edit is never
# clobbered (the same contract the old ``seed_wiki_articles._sync_intro`` had).
# Rendered through the help profile — the ``!!!`` blocks are admonitions.

#: The retired seed_wiki_articles default intro, verbatim. Production still holds this
#: text (that seed also filled only-when-blank), so seed_help_center treats an intro
#: exactly equal to it as stale seed output and replaces it with PAGE_INTRO. A
#: hand-edited intro never matches and is never touched.
RETIRED_INTRO = """\
Welcome to the Past Lives member hub. This is where the makerspace runs day to day: your \
guilds, the class catalog, the community calendar, guild voting, and your account settings.

Use the menu on the left to get around:

- Home: your dashboard and recent activity.
- Class Catalog: every class and workshop you can sign up for.
- Community Calendar: everything happening at the space, in one place.
- Spaces: the floor plan and every studio and shared area.
- Guild Voting: rank the guilds you want the monthly funding pool to support.
- Help: this page, plus answers to common questions.
- Member Directory: find other members by skill.
- Guilds: jump straight to any guild's page.

The guides below cover the parts people ask about most. Stuck? The Who's Who section \
further down says who to contact."""

PAGE_INTRO = """\
Welcome to the Member Portal, the app that runs Past Lives day to day. Everything you can do \
here, from taking classes to guild voting, has a short guide below. Pick a category, or \
search if you already know what you need.

!!! tip
    Stuck on something the guides don't cover? Check Who's Who further down this page to \
see who to ask."""

PAGE_PARKING = """\
You can park on the street along SE 9th Ave, SE 10th Ave, and Woodward St.

!!! note
    Check the posted signs before you park."""

PAGE_WHO_TO_CONTACT = """\
Not sure who to ask? Start here:

- **Sushuma** for finances.
- **Lee** for operations.
- **Morlock** for general questions and everything else."""

# The default hero banner, committed to the repo at static/help/_defaults/ so
# every environment seeds the same image into its own media storage (R2 in
# prod, the filesystem locally). Pixabay, license-free.
PAGE_BANNER_STATIC_PATH = "help/_defaults/help-banner.jpg"

# The seeded help-center categories (§10.1) — slug keys the seed command
# update_or_creates on; audience groups them on the landing page.
CATEGORIES: list[dict[str, Any]] = [
    {"slug": "getting-started", "name": "Getting Started", "audience": "member", "sort_order": 10},
    {"slug": "guilds", "name": "Guilds", "audience": "member", "sort_order": 20},
    {"slug": "classes", "name": "Taking Classes", "audience": "member", "sort_order": 30},
    {"slug": "events-community", "name": "Events & Community", "audience": "member", "sort_order": 40},
    {"slug": "contributing", "name": "Contributing & Under the Hood", "audience": "developer", "sort_order": 50},
    {"slug": "teaching", "name": "Teaching", "audience": "instructor", "sort_order": 50},
    {"slug": "running-a-guild", "name": "Running a Guild", "audience": "guild_lead", "sort_order": 60},
    {"slug": "admin", "name": "Admin", "audience": "admin", "sort_order": 70},
]

# The P1 launch articles (§10.2) — one entry per article with
# slug / category / title / sort_order / related / body / screenshots.
# P2 articles (2, 3, 14, 15, 25-29) follow in the fast-follow phase.
ARTICLES: list[dict[str, Any]] = [
    {
        "slug": "welcome-to-fog",
        "category": "getting-started",
        "title": "Welcome to the Member Portal: What's Where",
        "sort_order": 10,
        "related": ["guilds-and-guild-pages", "taking-a-class"],
        "body": """The Member Portal is the Past Lives member hub — the app where the makerspace runs day to day. Your guilds, the class catalog, the community calendar, guild voting, and your account settings all live here.

## Your Home Dashboard {#home-dashboard}

Log in and you land on **Home**. It shows:

- **Get started at Past Lives** — a short checklist for new members. Dismiss it once you're settled.
- Quick links — one tap to the **Community Calendar**, **Class Catalog**, **Guild Voting**, **Member Directory**, and **Settings**.
- **Your upcoming** — classes and events you're signed up for.
- **Latest from your guilds** — recent announcements from guilds you follow.
- **Your guilds** — a chip for each of your guilds, linking straight to its page.

![The home dashboard: your checklist, quick links, upcoming events, and guild news.](/static/help/welcome-to-fog/01-home-dashboard.png)

## Finding Your Way Around

The left sidebar is the map:

- **Home** — your dashboard.
- **Class Catalog** — every class and workshop you can sign up for.
- **Community Calendar** — everything happening at the space, in one place.
- **Spaces** — the floor map and every studio and shared area.
- **Guild Voting** — rank the guilds you want the monthly funding pool to support.
- **Help** — the guides you're reading now, with search.
- **Member Directory** — find other members by name or skill.
- **Guilds** — every guild, listed at the bottom. Jump straight to any guild's page.
- **Feedback** — at the very bottom. Found a bug or have an idea? Send it here.

![The sidebar: every part of the Member Portal, one click away.](/static/help/welcome-to-fog/02-sidebar-navigation.png)

The top bar has a light/dark theme toggle and your avatar. Open the avatar for **Settings** and **Log Out**.

## Good First Steps

- Follow a guild or two — see [Guilds and Guild Pages](/help/guilds/guilds-and-guild-pages/).
- Book a guild orientation — see [Getting Oriented](/help/guilds/getting-oriented/).
- Grab a seat in a class — see [Taking a Class](/help/classes/taking-a-class/).
- Cast your guild vote — see [Guild Voting](/help/guilds/guild-voting/).

## Good to Know

- These guides cover the app. The physical space — tools, machines, shop rules — is documented on the wiki at [wiki.pastlives.space](https://wiki.pastlives.space).
- Some sidebar items only appear for certain roles. Guild leads and staff get an **Orientations** dashboard link; admins see extra management pages.
""",
        "screenshots": [
            {
                "file": "01-home-dashboard.png",
                "page": "hub_home",
                "selector": None,
                "caption": "The home dashboard: your checklist, quick links, upcoming events, and guild news.",
                "as_role": "member",
            },
            {
                "file": "02-sidebar-navigation.png",
                "page": "hub_home",
                "selector": ".hub-sidebar",
                "caption": "The sidebar: every part of the Member Portal, one click away.",
                "as_role": "member",
            },
        ],
    },
    {
        "slug": "notifications",
        "category": "getting-started",
        "title": "Your Notification Settings: Choose What Reaches You",
        "sort_order": 20,
        "related": ["welcome-to-fog", "announcements", "discord-and-fog"],
        "body": """Every important thing that happens at Past Lives — a class you booked getting cancelled, a new announcement from your guild, a reply to a request you sent — can reach you in more than one place. You decide which. This is your notification center, and it lives in **Settings → Notifications**.

## Where to Find It {#where}

There are three ways in:

- **Settings → Notifications.** Open **Settings** from your profile menu (your photo, top right), then pick the **Notifications** tab.
- **The bell, top right.** Click it to open your full **Notifications** page — every notice you've received, newest first.
- **Any email footer.** Every email we send has a "Manage your email preferences or unsubscribe" link. It opens your notification settings for that email address, with no sign-in needed.

## The Ways a Notice Can Reach You {#channels}

Your settings are a grid: every kind of event runs down the side, and each column is a way that event can reach you.

- **In-app (Bell)** — always on. The bell shows everything, so nothing is ever lost.
- **Email** — a message to your inbox.
- **Push** — a notification on your phone. Android only for now; iOS is coming soon.
- **Discord** — a direct message from the Fog Bot. This column only works once you've connected Discord.

Some events also offer a **Scheduled** or **Digest** column — a weekly round-up instead of one message at a time.

## Turning Things On and Off {#toggle}

Flip any switch to turn that notice on or off for that channel. To move faster:

- **All on** / **All off** at the very top flips everything at once.
- Each category ("Classes", "Your guilds", and so on) has its own **All on** / **All off**.

Then hit **Save** at the bottom.

## What You Can't Turn Off {#always-on}

A few notices are locked on, because missing them would cause real problems: receipts, security and sign-in messages, and booking and orientation updates. You'll always get those, and the bell always shows everything.

Announcements can also be marked **urgent** by whoever sends them. An urgent announcement reaches you even if you've turned that kind of email off — it's saved for the things you truly need to know.

## Heads-Up: Some Updates Start Switched On {#defaults}

So that nobody misses something important, several updates come **switched on by default**. That's on purpose, but it's your call. Take a minute to open **Settings → Notifications** and set each one the way you actually want it: turn off what you don't need, and keep the ones that matter to you.""",
    },
    {
        "slug": "guilds-and-guild-pages",
        "category": "guilds",
        "title": "Guilds and Guild Pages",
        "sort_order": 10,
        "related": ["getting-oriented", "guild-voting"],
        "body": """A guild is a group of members built around a craft — ceramics, woodshop, textiles. Guilds run their own classes, hold meetings and orientations, and each month get a share of the funding pool based on how members vote (see [Guild Voting](/help/guilds/guild-voting/)).

## Find a Guild

Every guild is listed in the **Guilds** section at the bottom of the left sidebar. Click one to open its page.

## What's on a Guild Page

![A guild page: the tabs across the top, announcements, and the Get Involved panel.](/static/help/guilds-and-guild-pages/01-a-guild-page.png)

Tabs across the top:

- **Overview** — announcements, what the guild is about, upcoming classes, and meetings.
- **Guild Calendar** — that guild's meetings, classes, and orientation times.
- **Orientations** — book a time to get oriented (appears when the guild offers orientations).
- **FAQ**, **Meeting Notes**, and **Gallery** — appear once the guild adds content to them.

The Overview's side panels show the guild's staff, studio hours, next meeting, members, links, and contact info — plus the **Get Involved** panel with quick actions like booking an orientation.

## Follow a Guild {#guild-join-leave}

1. Click your avatar (top right), open **Settings**, then the **Guilds** tab. The first time you sign in, we also ask which guilds you want updates from.
2. Flip on each guild you want updates from. Flip one off any time to stop. Changes save instantly.

![The Guilds tab in Settings: one toggle per guild, saved instantly.](/static/help/guilds-and-guild-pages/03-leave-from-settings.png)

Following is free, and you can follow as many guilds as you want. It gets you the guild's announcements, puts you on its roster, and gives you its Discord role, and the guild's leads are notified so they can say hi.

## Good to Know

- Anyone can propose an announcement for a guild when the guild has member suggestions on, but a guild lead or admin has to approve it before it appears — so yours may not show up right away.
- Only guild leads, their staff, and admins can edit a guild page. If you help run a guild and need access, ask an admin.
- Many guilds ask you to get oriented before using their space and tools — see [Getting Oriented](/help/guilds/getting-oriented/).
""",
        "screenshots": [
            {
                "file": "01-a-guild-page.png",
                "page": "/guilds/ceramics-guild/",
                "selector": None,
                "caption": "A guild page: the tabs across the top, announcements, and the Get Involved panel.",
                "as_role": "member",
            },
            {
                "file": "03-leave-from-settings.png",
                "page": "/settings/?tab=guilds",
                "selector": ".hub-card:has(.pl-guild-toggle-list)",
                "caption": "The Guilds tab in Settings: one toggle per guild, saved instantly.",
                "as_role": "member",
            },
        ],
    },
    {
        "slug": "getting-oriented",
        "category": "guilds",
        "title": "Getting Oriented",
        "sort_order": 20,
        "related": ["guilds-and-guild-pages"],
        "body": """An orientation is how a guild shows you its space, its tools, and its safety rules. You do one per guild, and many guilds ask for it before you use their equipment.

## Book a Slot {#orientation-book-slot}

1. Open the guild's page and click **Join an Orientation** in the **Get Involved** panel. It jumps you to the booking section on the **Orientations** tab.

   ![Join an Orientation in the Get Involved panel jumps to the booking section.](/static/help/getting-oriented/01-join-an-orientation.png)

2. The booking section lists upcoming times with a **Request** button next to each open one. Pick a time and click **Request**. (A time marked **Full** has no seats left — pick another.)

   ![The Orientations tab holds the guild's booking section.](/static/help/getting-oriented/02-orientations-tab.png)

3. Click **Send request** to confirm.

Your request goes to the guild's leads, and it is not official until one of them approves it. Until then the guild page shows your booking as **Requested — awaiting confirmation from the guild lead**. You'll get an email right away confirming the request was received, with a tentative calendar invite attached — and an "Orientation confirmed" email with a real invite once a lead locks it in.

## Request a Custom Time {#orientation-request-custom-time}

If none of the posted times work, look for **None of these times work? Request a custom time** below the list. Propose a date and time, add a note if it helps, and click **Send request**. The same rule applies: a guild lead has to confirm it before it's real.

Not every guild offers this — the button only appears when the guild allows custom requests.

## Cancel Your Booking {#orientation-cancel-booking}

On the guild page, your booking shows under **Your orientation** with a **Cancel my orientation** button. Click it and confirm with **Cancel orientation**. Every orientation email also carries a cancel link. You can cancel any time, before or after it's confirmed, and request a new time whenever you're ready. To reschedule, cancel and book again.

## Good to Know

- Once you've done it, the guild page simply shows **You're oriented** — the booking section goes away.
- If a guild pauses bookings, the section says **Orientations paused**. Check back later.
""",
        "screenshots": [
            {
                "file": "01-join-an-orientation.png",
                "page": "/guilds/ceramics-guild/",
                "selector": '.hub-card:has(button[data-help-key="orientation.book-slot"])',
                "caption": "Join an Orientation in the Get Involved panel jumps to the booking section.",
                "as_role": "member",
            },
            {
                "file": "02-orientations-tab.png",
                "page": "/guilds/ceramics-guild/",
                "selector": 'nav[role="tablist"]',
                "caption": "The Orientations tab holds the guild's booking section.",
                "as_role": "member",
            },
        ],
    },
    {
        "slug": "guild-voting",
        "category": "guilds",
        "title": "Guild Voting",
        "sort_order": 30,
        "related": ["guilds-and-guild-pages"],
        "body": """Every month, a pool of makerspace funding is split between the guilds — and your vote decides the split. You rank your top three guilds, and each rank earns points: 1st choice 5, 2nd choice 3, 3rd choice 2. More points means a bigger share of the pool.

## Rank Your Top Three {#voting-rank-guilds}

1. Click **Guild Voting** in the left sidebar.
2. Pick three different guilds for **1st Choice (5 pts)**, **2nd Choice (3 pts)**, and **3rd Choice (2 pts)**.

   ![Pick your first, second, and third choice guilds, then submit.](/static/help/guild-voting/01-rank-your-guilds.png)

3. Click **Submit Vote** — or **Update Vote** if you've voted before.

Your ballot is rolling: it sticks and counts every month until you change it. Edit it whenever you like — there's no deadline and no lock while the cycle runs. The **Your Current Votes** card shows your standing ballot and when you last touched it.

## The Monthly Cycle {#voting-monthly-cycle}

A voting cycle is one calendar month. The voting page shows the current cycle, the day it closes (always the last day of the month), and when the next one begins.

![The voting page shows when the current cycle closes and the next begins.](/static/help/guild-voting/02-cycle-dates.png)

Minutes into the new month, the Member Portal automatically freezes the closed cycle's standings into a snapshot and emails the results to every active member. If you voted, your results email includes a recap of your own ballot. There's nothing to do at month's end — your standing ballot was your vote.

## Watch the Standings {#voting-live-standings}

The standings card updates live all month:

- **Current Standings** — the live point tally from everyone's ballots.
- **New Votes This Month** — ballots cast or changed since the last snapshot.
- **Last Month's Results** — the locked-in split: points, share, and dollars per guild, with **Details** and **Full History** links for past cycles.

![Live standings, new votes, and last month's locked-in results.](/static/help/guild-voting/03-live-standings.png)

## Good to Know

- Your three picks must be three different guilds.
- If the page says your account isn't linked to a membership, ask an admin to connect it — then you can vote.
""",
        "screenshots": [
            {
                "file": "01-rank-your-guilds.png",
                "page": "hub_guild_voting",
                "selector": "form.hub-form",
                "caption": "Pick your first, second, and third choice guilds, then submit.",
                "as_role": "member",
            },
            {
                "file": "02-cycle-dates.png",
                "page": "hub_guild_voting",
                "selector": 'div.hub-card[style*="border-left"]',
                "caption": "The voting page shows when the current cycle closes and the next begins.",
                "as_role": "member",
            },
            {
                "file": "03-live-standings.png",
                "page": "hub_guild_voting",
                "selector": ".hub-card:has(.vote-tab-nav)",
                "caption": "Live standings, new votes, and last month's locked-in results.",
                "as_role": "member",
            },
        ],
    },
    # ------------------------------------------------------------------
    # classes / taking-a-class (sort 10)
    # ------------------------------------------------------------------
    {
        "slug": "taking-a-class",
        "category": "classes",
        "title": "Taking a Class",
        "sort_order": 10,
        "related": ["community-calendar", "become-an-instructor"],
        "body": """\
Classes at Past Lives are open to everyone — you don't need to be a member or even have an account to take one.

### Find a Class {#class-find}

1. Open **Class Catalog** in the left menu, or go straight to [/classes/](/classes/).
2. Narrow things down with the **Guild Type** and **When** dropdowns, or open **Filters** for price range, instructor, member discounts, and free classes.
3. Click a class to see its description, dates, price, and how many spots are left.

![The class catalog — every upcoming class, with filters across the top.](/static/help/taking-a-class/01-class-catalog.png)

### Register and Pay {#class-register}

1. On the class page, click **Register now** (a free class says **Register — Free**).
2. Fill in your details, answer any questions, and check the box to agree to the liability waiver.
3. A free class confirms right away. A paid class sends you to a secure Stripe checkout — your spot is locked in once the payment goes through.

![The booking card on a class page — price, spots left, and the Register button.](/static/help/taking-a-class/02-class-page-register.png)

![The registration form — your details, an optional discount code, and the waiver.](/static/help/taking-a-class/03-registration-form.png)

Good to know:

- **Member pricing is automatic.** Register with the email on your Past Lives account and the member price is applied for you — no code needed.
- **Discount codes** go in the **Discount code (optional)** box on the registration form. If a class is on sale, the sale price may not combine with codes — the form tells you when that's the case.
- **You can't join a class after it has started.** That includes joining a series partway through.

### Join the Waitlist {#class-join-waitlist}

Sold out? The class page shows **Join the waitlist** instead of the register button.

1. Click **Join the waitlist** and fill in the same form — no payment, no charge to hold your place.
2. You get an email confirming your spot in line and your position.
3. When a confirmed spot opens up, the next person in line gets an email with a link to claim it. That email says how many hours you have before the spot is offered to the next person.

### Manage or Cancel Your Registration {#class-manage-registration}

Your confirmation email includes a personal link to your registration page. No login needed — the link is yours alone. From there you can see your status, schedule, and what you paid.

To cancel, click **Cancel my registration**. You can cancel any time before the class starts.

Refunds aren't automatic: for a paid class, an admin handles the refund — email info@pastlives.space. Each class page also lists its own cancellation policy in the **Questions** section, so check that before you cancel late.
""",
        "screenshots": [
            {
                "file": "01-class-catalog.png",
                "page": "/classes/",
                "selector": None,
                "caption": "The class catalog — every upcoming class, with filters across the top.",
                "as_role": "member",
            },
            {
                "file": "02-class-page-register.png",
                "page": "/classes/intro-to-lost-wax-casting/",
                "selector": ".cp-detail__rail-card",
                "caption": "The booking card on a class page — price, spots left, and the Register button.",
                "as_role": "member",
            },
            {
                "file": "03-registration-form.png",
                "page": "/classes/intro-to-lost-wax-casting/register/",
                "selector": ".detail-card",
                "caption": "The registration form — your details, an optional discount code, and the waiver.",
                "as_role": "member",
            },
        ],
    },
    # ------------------------------------------------------------------
    # events-community / community-calendar (sort 10)
    # ------------------------------------------------------------------
    {
        "slug": "community-calendar",
        "category": "events-community",
        "title": "The Community Calendar",
        "sort_order": 10,
        "related": ["propose-an-event", "taking-a-class"],
        "body": """\
The Community Calendar puts everything happening at the space in one place: guild meetings, classes, and community events.

### Browse the Calendar {#calendar-browse}

1. Click **Community Calendar** in the left menu.
2. Use the **Week** / **Month** toggle to switch views, and the arrows to move through time.
3. The **Events** tab next to **Calendar** shows the same events as a plain list.
4. Click any event to open its page — the details, plus an **Add to calendar** button that downloads a calendar file for just that event.

![The Community Calendar — Week and Month views, with the Events tab beside them.](/static/help/community-calendar/01-calendar-page.png)

Heads up: some colored events are pulled in from subscribed and guild calendars — not all of them are Past Lives classes, so they won't all appear in the Class Catalog.

### Show or Hide Calendars {#calendar-filter}

A row of colored filter chips sits above the grid — one per guild or calendar.

1. Click a chip to hide that calendar's events; click it again to bring them back.
2. Your choices are saved in your browser, so they stick on this device.

![The filter chips — click one to hide or show that guild or calendar.](/static/help/community-calendar/02-calendar-filters.png)

### Put It in Your Own Calendar App {#calendar-subscribe}

You'll need to be signed in for this part.

1. On the **Calendar** tab, click **Subscribe** (top right).
2. Pick **Subscribe to the Member calendar** for all makerspace events, or **Subscribe to the Public calendar** for the outward facing one. Your calendar app stays in sync as new events are added.
3. Or pick **Download .ics (one time)** for a one time import of this page.

![The Subscribe button, top right of the Calendar tab.](/static/help/community-calendar/03-export-calendar.png)

The Member and Public calendars are the makerspace's shared Google calendars. To register for a class, use the class's own page instead.

Each guild also has its own calendar, on the **Guild Calendar** tab of its guild page.
""",
        "screenshots": [
            {
                "file": "01-calendar-page.png",
                "page": "hub_community_calendar",
                "selector": None,
                "caption": "The Community Calendar — Week and Month views, with the Events tab beside them.",
                "as_role": "member",
            },
            {
                "file": "02-calendar-filters.png",
                "page": "hub_community_calendar",
                "selector": ".pl-calendar-filters",
                "caption": "The filter chips — click one to hide or show that guild or calendar.",
                "as_role": "member",
            },
            {
                "file": "03-export-calendar.png",
                "page": "hub_community_calendar",
                "selector": ".pl-calendar-export",
                "caption": "The Subscribe button, top right of the Calendar tab.",
                "as_role": "member",
            },
        ],
    },
    # ------------------------------------------------------------------
    # events-community / propose-an-event (sort 20)
    # ------------------------------------------------------------------
    {
        "slug": "propose-an-event",
        "category": "events-community",
        "title": "Propose an Event",
        "sort_order": 20,
        "related": ["community-calendar", "announcements"],
        "body": """\
Got a workshop, meetup, or hangout in mind? Any member can propose an event for the Community Calendar.

### Propose It {#event-propose}

1. Open the **Community Calendar** and click **+ Propose an event**.
2. Fill in the form: title, when it starts, whether it repeats, and the details. Pick your guild to propose one of its meetings or events, or leave the guild blank for a site-wide community event.
3. Click **Submit for review**.

![The Events tab — the upcoming list, with the Propose an event button.](/static/help/propose-an-event/01-events-tab.png)

![The proposal form — leave the guild blank for a site-wide event.](/static/help/propose-an-event/02-propose-form.png)

Your event is not on the calendar yet. A guild lead or an admin reviews it first — you'll get a note when they respond. Once it's approved, it publishes to the calendar and gets its own event page anyone can open.

### Track, Edit, or Withdraw {#event-track}

Your in-flight proposals appear under **Your proposed events** on the **Events** tab, and at the top of the propose page. Each one shows a status pill — **Pending review**, **Changes requested**, or **Declined** — plus the reviewer's note when there is one.

- If a reviewer asks for changes: click **Edit**, fix it up, and hit **Resubmit for review**.
- To pull a proposal that hasn't published yet: click **Withdraw**. It's removed and won't be reviewed.
""",
        "screenshots": [
            {
                "file": "01-events-tab.png",
                "page": "/calendar/?tab=events",
                "selector": None,
                "caption": "The Events tab — the upcoming list, with the Propose an event button.",
                "as_role": "member",
            },
            {
                "file": "02-propose-form.png",
                "page": "hub_propose_event",
                "selector": None,
                "caption": "The proposal form — leave the guild blank for a site-wide event.",
                "as_role": "member",
            },
        ],
    },
    # ------------------------------------------------------------------
    # events-community / announcements (sort 30)
    # ------------------------------------------------------------------
    {
        "slug": "announcements",
        "category": "events-community",
        "title": "Announcements",
        "sort_order": 30,
        "related": ["propose-an-event", "guilds-and-guild-pages"],
        "body": """\
Announcements are how guilds share news — a restock, a schedule change, a call for help.

### Where They Show Up {#announcement-where}

- Every guild page has an **Announcements** section with that guild's posts.
- Your **Home** dashboard shows **Latest from your guilds** — recent announcements from the guilds you follow.
- A guild's leads can also send an announcement to guild members by email, so keep an eye on your inbox.

![The Announcements section on a guild page, with the Suggest an announcement button.](/static/help/announcements/01-guild-announcements.png)

### Suggest an Announcement {#announcement-propose}

Anyone can suggest an announcement for any guild that has member suggestions on — you don't need to run it.

1. On the guild's page, click **+ Suggest an announcement**.
2. Write your title and message, and submit it.
3. It goes to the guild's leads (or an admin) for review before it posts. You'll get a note when someone responds.

Some guilds turn member suggestions off. If you don't see the button, that guild isn't taking suggestions right now.

![The Suggest an announcement form.](/static/help/announcements/02-propose-announcement.png)

### Edit or Withdraw Your Proposal {#announcement-manage}

Your in-flight proposals appear under **Your proposed announcements** at the top of the suggest page. Each shows a status pill — **Pending review**, **Changes requested**, or **Declined** — plus any reviewer note.

- If a reviewer asks for changes: click **Edit** and resubmit. It goes back to the review queue.
- Click **Withdraw** to remove a proposal before it posts. It won't be reviewed.
""",
        "screenshots": [
            {
                "file": "01-guild-announcements.png",
                "page": "/guilds/ceramics-guild/",
                "selector": "section.pl-guild-section:has(a[href*='/announcements/propose/'])",
                "caption": "The Announcements section on a guild page, with the Suggest an announcement button.",
                "as_role": "member",
            },
            {
                "file": "02-propose-announcement.png",
                "page": "hub_guild_announcement_propose",
                "selector": None,
                "caption": "The Suggest an announcement form.",
                "as_role": "member",
            },
        ],
    },
    # ------------------------------------------------------------------
    # events-community / member-directory (sort 40)
    # ------------------------------------------------------------------
    {
        "slug": "member-directory",
        "category": "events-community",
        "title": "The Member Directory",
        "sort_order": 40,
        "related": ["guilds-and-guild-pages"],
        "body": """\
The Member Directory is where you find other makers — by name or skill — and where you decide what they see about you.

### Find People {#directory-search-filter}

1. Click **Member Directory** in the left menu. You'll need to be signed in (unless an admin has made the directory public).
2. Filter with the **Skill** dropdown.
3. Type a name or a skill into the **Search** box.
4. Tick **Open for commissions** to see only members taking commission work.
5. Click **Apply**.

![The Member Directory — a card for every listed member.](/static/help/member-directory/01-directory.png)

![Filter by skill, search by name, or show only members open for commissions.](/static/help/member-directory/02-directory-filters.png)

Each card shows what that member chose to share: name, photo, pronouns, contact details, and skills. Want to be findable by skill? Add yours under **My skills** in your settings.

### Control What Others See {#directory-visibility}

You decide what your own card shows. Open **Settings** from your profile menu (top right), then the **Profile** tab:

1. The **Member Directory** switch sets your whole listing to **Public** or **Hidden**.
2. Each field — email, profile photo, pronouns, phone, Discord, about me — has its own **Public** / **Hidden** toggle.
3. Any extra contact methods you add have their own **Show in directory** switch.
4. Click **Save Profile**.

![The directory switch in your Settings — set your listing to Public or Hidden.](/static/help/member-directory/03-visibility-controls.png)

The fine print:

- Anything you switch off stays private to staff.
- Admins, guild officers, guild leads, and instructors are always listed — their role needs a public profile — but they still choose which fields appear on their card.
- Some things are never shown to other members, no matter what: your full legal name, billing details, emergency contacts, your account status or notes, and which guilds you follow (that's a notification choice, not a public label).
- The directory normally requires signing in. Admins can turn on a site setting that opens it to visitors without an account — if that's on, treat whatever your card shows as public.
""",
        "screenshots": [
            {
                "file": "01-directory.png",
                "page": "hub_member_directory",
                "selector": None,
                "caption": "The Member Directory — a card for every listed member.",
                "as_role": "member",
            },
            {
                "file": "02-directory-filters.png",
                "page": "hub_member_directory",
                "selector": ".pl-directory-filters",
                "caption": "Filter by skill, search by name, or show only members open for commissions.",
                "as_role": "member",
            },
            {
                "file": "03-visibility-controls.png",
                "page": "hub_user_settings",
                "selector": ".pl-profile-master",
                "caption": "The directory switch in your Settings — set your listing to Public or Hidden.",
                "as_role": "member",
            },
        ],
    },
    {
        "slug": "instructor-quickstart",
        "category": "teaching",
        "title": "Instructor Quickstart: Everything You Can Do",
        "sort_order": 10,
        "related": ["become-an-instructor", "run-your-class"],
        "body": """\
Everything you can do as an instructor, on one page. Each item links to a short guide that walks through it with screenshots. New here? Read top to bottom — it takes two minutes.

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

### Start With the Tour {#instructor-tour}

The guided tour points at the real buttons, right on the teaching portal, in about 20 seconds. [Take the tour](/classes/teach/?tour=instructor) — you can retake it anytime from the [Help page](/help/).

### Study a Finished Class {#instructor-example-class}

[Shaker Side Table: Hand-Cut Joinery](/classes/shaker-side-table-hand-cut-joinery/) is a permanent example class. It never appears in the catalog and you can't register for it — it exists so you can study what a complete, published class page looks like: photos, description, sessions, pricing, and FAQ. Aim for this.

### Everything You Can Do {#instructor-toolkit}

**Create and publish:**

- **Create a class** — a private draft with your title, description, dates, price, and photos. See [Become an Instructor](/help/teaching/become-an-instructor/).
- **Submit it for review** — a guild lead (when your category has one) and an admin check it before it goes live. Same guide.
- **Preview as a student** — see the public page exactly as a student will, at any point while you work.
- **Offer it again on new dates** — one click copies a class into a new draft so you can run it again; the two link to each other on the public page. See [Run Your Class](/help/teaching/run-your-class/).

**Run the class:**

- **See your roster** — who signed up and their answers to your questions.
- **Email your students** — pick recipients and send, straight from the portal.
- **Watch the waitlist** — it runs itself; the portal shows you the line.
- **Write a welcome email** — sent automatically to each student the moment their spot is confirmed.

All four are covered in [Run Your Class](/help/teaching/run-your-class/).

**Reach further:**

- **Announce to your class** — open **Admin Tools** in the sidebar, then **Announcements**, and pick one of your published classes as the recipient group: everyone registered for it gets the announcement in the app, by push, and by email — see [The Announcement Composer](/help/running-a-guild/announcement-composer/). (Admin Tools shows up once an admin has given you the Instructor role — ask if you don't see it.)
- **Your public instructor page** — with the Instructor role, the catalog links your bio and your classes from every class you teach.

### The Ground Rules {#instructor-ground-rules}

- Nobody self-publishes. Every class — including every new run of an old one — goes through review first.
- Draft and pending classes are all yours to edit. Once a class publishes, only an admin can change it.
- The one-time [portal orientation for instructors](/help/more/instructor-orientation/) is what unlocks the portal, and the quality bar lives there.

![The teaching portal: overview, your classes, and registrations.](/static/help/instructor-quickstart/01-the-teaching-portal.png)
""",
        "screenshots": [
            {
                "file": "01-the-teaching-portal.png",
                "page": "/classes/teach/",
                "selector": None,
                "caption": "The teaching portal: overview, your classes, and registrations.",
                "as_role": "instructor",
            },
        ],
    },
    {
        "slug": "become-an-instructor",
        "category": "teaching",
        "title": "Become an Instructor",
        "sort_order": 20,
        "related": ["run-your-class", "taking-a-class"],
        "body": """\
Are you an experienced instructor? Past Lives instructors come from our membership base. If you have a vision for a class you'd like to teach at Past Lives, the first step is to talk to your guild lead or email [lee@pastlives.space](mailto:lee@pastlives.space).

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

### Complete the Orientation {#teach-become-instructor}

The first time you head for the teaching portal, you land on the [portal orientation for instructors](/classes/teach/orientation/). It's one page: what we expect from instructors, how class review works, and the quality bar. Read it, tick the box, and the portal unlocks right away — you only ever do it once. (This is about using the portal; it's separate from any guild equipment or tool orientations.)

One note on the word "instructor": an admin can also set your role to Instructor. The role adds extras — a public instructor page in the class catalog and the class Announcements tool. You don't need the role to create or teach a class; completing the portal orientation is what opens the teaching portal.

### Open the Teaching Portal

Three ways in:

1. Click **Class Catalog** in the left menu, then **Manage My Classes**.
2. Go straight to `/classes/teach/`.
3. On any guild page, click **Teach a Class** in the Get Involved panel. That one jumps straight to the new class form.

![The teaching portal Overview: your drafts, classes in review, and recent sign-ups.](/static/help/become-an-instructor/01-teaching-portal.png)

### Create Your Draft {#teach-create-class}

1. In the portal, open the **Classes** tab and click **+ New Class** (your first time, the button says **+ Create your first class**).
2. Fill in the basics: title, guild category, description, price, and how many spots.
3. Add your dates. A class can be one session or a series; add one row per session. You can also pick flexible scheduling if the dates are arranged later.
4. Add at least two photos: a hero image and at least one gallery photo. A class needs both before it can be submitted.
5. Click **Save Draft** to keep working, or **Save & Submit for Review** when it is ready.

![The new class form: describe it, price it, and add your session dates.](/static/help/become-an-instructor/02-new-class-form.png)

A draft is private. Only you and admins can see it, and you can edit it as much as you like. **Preview** shows you the public page exactly as a student will see it.

Not sure what "done" looks like? Study the permanent example class, [Shaker Side Table: Hand-Cut Joinery](/classes/shaker-side-table-hand-cut-joinery/) — a complete page with photos, a description that answers the basics, sessions, pricing, and FAQ. (It's example-only: not in the catalog, registration closed.)

### Submit It for Review {#teach-submit-for-review}

Click **Save & Submit for Review** on the form, or **Submit for review** on the class page. Review happens in order:

- If your class's category belongs to a guild that has a lead, that guild lead reviews it first.
- Then an admin gives the final yes. No guild lead involved? The admin reviews it directly.

Reviewers see your class exactly as a student would, in a full preview of the public page. Each reviewer picks **Approve**, **Request changes**, or **Decline**, and has to leave a note when requesting changes or declining, so you always know what to fix. You get an email as each decision lands.

**Request changes** and **Decline** send the class back to Draft with the reviewer's notes. Fix it up and submit again; a fresh submission restarts the review from the first gate.

### What the Statuses Mean

- **Draft**: private. Edit freely, submit when ready.
- **Pending**: submitted, waiting on review. You can still edit it.
- **Published**: live in the catalog and open for sign-ups.

Once a class is published (or archived), only an admin can edit it. Need a change to a live class? Ask an admin.""",
        "screenshots": [
            {
                "file": "01-teaching-portal.png",
                "page": "/classes/teach/",
                "selector": None,
                "caption": "The teaching portal Overview: your drafts, classes in review, and recent sign-ups.",
                "as_role": "instructor",
            },
            {
                "file": "02-new-class-form.png",
                "page": "/classes/teach/classes/new/",
                "selector": None,
                "caption": "The new class form: describe it, price it, and add your session dates.",
                "as_role": "instructor",
            },
        ],
    },
    {
        # UNLISTED (§10.6 / Spec D): renders on the orientation page at
        # /classes/teach/orientation/ and resolves at its own /help/ URL, but
        # never appears on the landing, category pages, or search.
        "slug": "instructor-orientation",
        "category": None,
        "title": "Portal Orientation for Instructors",
        "sort_order": 0,
        "related": [],
        "body": """\
Past Lives instructors come from our membership base. This page is the one-time portal orientation for instructors: read it, tick the box at the bottom, and the teaching portal unlocks right away. (Have a class in mind but haven't talked to anyone yet? Start with your guild lead, or email lee@pastlives.space.)

## What We Expect From Instructors {#what-we-expect}

- **Show up prepared.** Know your material, have your tools and supplies sorted, and start on time.
- **Keep it safe.** You're responsible for how tools are used in your class. If a session uses guild equipment, make sure everyone in the room is cleared to use it — or build that training into the class.
- **Be straight in your listing.** The title, description, and price should match what students actually get. No surprises on the day.
- **Look after your students.** Answer questions, use the portal's email tool to keep registrants posted, and tell an admin early if you have to cancel.

## How Class Review Works {#how-review-works}

You never self-publish — every class is reviewed before it appears in the catalog:

1. You write a **draft**. Drafts are private; only you and admins can see them.
2. You **submit it for review** when it's ready.
3. If your class's category belongs to a guild with a lead, that **guild lead** reviews it first.
4. An **admin** gives the final yes. Only then does it publish.

Reviewers can approve, request changes, or decline — and they have to leave a note when sending something back, so you always know what to fix. You get an email as each decision lands.

## The Quality Bar {#the-quality-bar}

Before a class can be submitted it needs:

- **At least two photos** — its own hero image and at least one gallery photo. Classes with real photos of the work get real sign-ups.
- **A description that answers the basics** — what students will make or learn, what's provided, and what (if anything) to bring.
- **Fair pricing** — cover your materials and time. If you set a member discount, members get it automatically when they register with their member email.""",
        "screenshots": [],
    },
    {
        "slug": "run-your-class",
        "category": "teaching",
        "title": "Run Your Class",
        "sort_order": 30,
        "related": ["become-an-instructor", "taking-a-class"],
        "body": """\
Once your class is submitted or live, the teaching portal at `/classes/teach/` is where you run it: see who signed up, email them, watch the waitlist, set up your welcome email, and offer the class again.

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

The portal has three tabs: **Overview**, **Classes**, and **Registrations**. Opening a class from the **Classes** tab gives that class its own workspace with sub-tabs: **Overview**, **Registrations**, **Waitlist**, and **Emails**.

![The Classes tab lists every class you teach, with its status and sign-up count.](/static/help/run-your-class/01-your-classes.png)

### See Who Signed Up

Open the portal-wide **Registrations** tab to see students for all your classes at once, grouped by class. Or open one class and use its **Registrations** sub-tab. Each row shows the student's name, email, status, when they registered, and their answers to any registration questions.

![The Registrations tab: your students grouped by class, with the email tool.](/static/help/run-your-class/02-registrations.png)

### Email Your Students {#teach-email-students}

1. On a Registrations tab, tick the students you want to reach.
2. Click **Email selected students**.
3. Write a subject and message. Leave **Send me a copy** checked to get your own copy.
4. Click **Send**.

You can only email people registered for your own classes, and the per-class tab only reaches that class's students.

### Your Welcome Email {#teach-welcome-email}

Each class can have a welcome email, sent automatically to every student the moment their spot is confirmed — that is, when their payment clears. Waitlist joins never get it; they're not in the class yet.

Open the class's **Emails** sub-tab, switch the welcome email on, and write the subject and body. **Send a test to me** puts it in your own inbox so you can check it before students see it. It only goes out when it's switched on and has both a subject and a body.

### Discount Codes {#teach-discount-codes}

Discount codes are created by admins. Want one for your class — a percent off, a flat amount off, or an auto-apply sale? Ask an admin.

### Announce to Your Class {#teach-announce-class}

For bigger news — a schedule change, a supply list — send a real announcement instead of a plain email: open **Admin Tools** in the sidebar, then **Announcements**. Your published classes appear as audiences, and the announcement reaches every registrant in the app, by push notification on their phones, and by email. [The Announcement Composer](/help/running-a-guild/announcement-composer/) guide walks the whole tool. (Admin Tools appears once an admin has given you the Instructor role.)

### The Waitlist

When a class fills up, new sign-ups join a waitlist. You do not manage it by hand: the moment a confirmed spot opens (someone cancels or is refunded), the app emails the next person in line a link to claim the spot and marks them as notified. The **Waitlist** sub-tab shows everyone in order, with when they joined and whether they have been notified yet. Your **Overview** tab also flags any class with people waiting.

### Offer It Again {#teach-duplicate-run}

You do not need to rebuild a class to run it on new dates.

1. While the class is a draft or pending, open its **Edit** page.
2. At the bottom, click **+ Offer on another set of dates**.
3. You get a draft copy with no dates. Add the new dates, then submit it for review.

On the public class page, the original and the new class link to each other under **Other Dates for This Class**, so students always see every date the class is offered. And like any class, the new one goes through review before it publishes.

The caveat: once a class is published, only an admin can edit it, and that edit page is where the button lives. To add a new date-set to a live class, ask an admin; they have the same one-click tool.""",
        "screenshots": [
            {
                "file": "01-your-classes.png",
                "page": "/classes/teach/classes/",
                "selector": None,
                "caption": "The Classes tab lists every class you teach, with its status and sign-up count.",
                "as_role": "instructor",
            },
            {
                "file": "02-registrations.png",
                "page": "/classes/teach/registrations/",
                "selector": None,
                "caption": "The Registrations tab: your students grouped by class, with the email tool.",
                "as_role": "instructor",
            },
        ],
    },
    {
        "slug": "members-and-invites",
        "category": "admin",
        "title": "Members & Invites",
        "sort_order": 10,
        "related": ["reviewing-classes-admin", "voting-admin"],
        "body": """\
**Manage Members** in the left menu (admins only) is the roster: every member, every not-yet-member user, and the invite pipeline, on one page.

The list shows members first, then any user accounts with no membership attached ("Non-member user"). Filter by status, role, or member type, flag members with no email on file, or search by name, email, or Discord handle.

![Manage Members: the full roster with filters, search, and the invites panel.](/static/help/members-and-invites/01-members-page.png)

### Invite a Member {#admin-invite-member}

1. In the **Members & invites** card at the top, click **+ Invite a member**.
2. Enter their email and click **Send invite**.

They get an email with a signup link that pre-fills their address. Invites expire after 14 days. Un-accepted invites sit in the panel with two buttons each:

- **Resend** fires the invite email again.
- **Revoke** kills the signup link. You can always re-invite them later.

Expired invites collapse behind a count; **Clear expired** revokes them all in one click.

Prefer to skip the email? **+ Add member** creates the member directly on the roster, with no invite and no email sent. Use **Send login invite** later (see below) when they are ready to sign in.

### Edit a Member {#admin-edit-member}

Click **Edit** on any row. The edit page has two tabs.

**Details** holds their name, pronouns, Discord handle, status, member type, directory visibility, and the **Role** dropdown:

- **Admin**, **Guild Officer**, and **Member** set the hierarchy role.
- **Instructor** keeps them a regular member and creates their public instructor page in the class catalog. It is not what grants teaching access; any active member can already use the teaching portal.
- **Guest** deactivates the member. No hub access.

There is also a **Can approve their own discount codes** checkbox: with it on, that member can activate their own class discount codes without waiting for an admin. Click **Save member** to apply.

For a member who has never signed in, the Details tab shows **Send login invite**: it emails them a first-time sign-in link.

![The member edit page: Details, the Role dropdown, and the Emails tab.](/static/help/members-and-invites/02-members-table.png)

### Email Aliases {#admin-email-aliases}

The **Emails** tab manages every address a person can sign in with. Sign-in works by emailed code, and any verified address on this list works.

- **+ Add email** adds an alias. It arrives verified and non-primary.
- **Set primary** makes an address the one the app writes to.
- The verified toggle flips whether an address can be used to sign in.
- **Remove** takes an address off the list. The app stops you from removing the last usable sign-in address, so you cannot lock someone out by accident.""",
        "screenshots": [
            {
                "file": "01-members-page.png",
                "page": "hub_admin_members",
                "selector": None,
                "caption": "Manage Members: the full roster with filters, search, and the invites panel.",
                "as_role": "admin",
            },
            {
                "file": "02-members-table.png",
                "page": "hub_admin_members",
                "selector": ".pl-members-table",
                "caption": "The member edit page: Details, the Role dropdown, and the Emails tab.",
                "as_role": "admin",
            },
        ],
    },
    {
        "slug": "reviewing-classes-admin",
        "category": "admin",
        "title": "Reviewing & Publishing Classes",
        "sort_order": 20,
        "related": ["members-and-invites", "become-an-instructor"],
        "body": """\
The classes admin lives at `/classes/admin/`. Reach it from **Class Catalog** in the left menu, then **Manage classes** (the button admins see where members see Manage My Classes). It requires an actual admin account; instructors and guild leads run their own classes from the teaching portal instead.

### The Review Queue {#admin-review-queue}

The Overview's "Needs your attention" panel lists every class waiting on review. Each row has two buttons:

- **Approve** records your admin approval on the spot.
- **Review** opens the full review page: the class details, upcoming sessions, a student-eye preview of the public page, and a decision form with **Approve**, **Request changes**, and **Decline**. Notes are optional on approve and required on the other two, so the instructor always knows what to fix. **Submit decision** records it and emails the instructor.

Review order matters. When the class's category belongs to a guild that has a lead, the guild lead reviews first, through a tokenized link emailed to them (no admin access needed). Your admin gate only opens after the lead approves, so quick-approving early gets you a "waiting on the remaining reviewer(s)" message rather than a publish. Once every required approval is in, the class publishes and opens for sign-ups. **Request changes** and **Decline** send it back to the instructor as a draft, with your notes.

Admins can also create classes directly from the **Classes** tab; those publish immediately, with no review chain.

![Needs your attention: every class waiting on a review decision.](/static/help/reviewing-classes-admin/01-review-queue.png)

### Archive or Delete

**Archive** (on a class's detail page) takes a class off the public portal and out of the instructor's dashboard. Registrations are preserved, everyone who booked gets a cancellation email, and you can re-open it later via the Archived filter on the classes list.

Delete is only for classes with zero registrations, ever. Anything with registration history refuses to delete so the audit trail survives; archive it instead.

![The classes list: filter by status, including Pending and Archived.](/static/help/reviewing-classes-admin/02-classes-list.png)

### Fix a Registration {#admin-refund-registration}

Open the **Registrations** tab and click into a registration. Three admin-only actions:

- **Cancel Registration** frees the spot. If someone is on the waitlist, the next person is automatically emailed a claim link.
- **Move** reassigns the registration to a different class. Payment stays exactly as it is; there is no price reconciliation, so sort any price difference out separately.
- **Mark Refunded** frees the spot, promotes the waitlist, notifies the registrant, and records the refund in the activity feed. It does NOT move any money. Issue the actual refund by hand in the Stripe dashboard; this button is the bookkeeping half.

![A registration's detail page, with Cancel, Move, and Mark Refunded.](/static/help/reviewing-classes-admin/03-registrations.png)

### Discount Codes

Discount codes are created by admins — instructors ask you when they want one for a class. The **Discount Codes** tab is where you create and manage them: percent off or a flat amount off, optional valid dates, an optional cap on total uses, and an auto-apply option for running a sale. A new code starts inactive; **Approve** activates it and **Unapprove** switches it back off.""",
        "screenshots": [
            {
                "file": "01-review-queue.png",
                "page": "/classes/admin/",
                "selector": None,
                "caption": "Needs your attention: every class waiting on a review decision.",
                "as_role": "admin",
            },
            {
                "file": "02-classes-list.png",
                "page": "/classes/admin/classes/",
                "selector": None,
                "caption": "The classes list: filter by status, including Pending and Archived.",
                "as_role": "admin",
            },
            {
                "file": "03-registrations.png",
                "page": "/classes/admin/registrations/",
                "selector": None,
                "caption": "A registration's detail page, with Cancel, Move, and Mark Refunded.",
                "as_role": "admin",
            },
        ],
    },
    {
        "slug": "voting-admin",
        "category": "admin",
        "title": "Voting Admin",
        "sort_order": 30,
        "related": ["guild-voting", "members-and-invites"],
        "body": """\
Guild voting mostly runs itself. Members keep one ranked ballot each, editable any time, and it counts every month until they change it. The cycle is the calendar month, closing on the last day, and the standings are always live; there is no hard lock. Your job as admin is to watch turnout, freeze the month's results into a snapshot, and send members their results.

As an admin, the Guild Voting page grows a tab bar: **Overview** (the same ballot page members see), **At a Glance**, **Funding History**, **Snapshots**, and **Settings**.

### At a Glance {#admin-voting-overview}

The read-only dashboard for the current cycle: members with votes, active members, participation rate, paying voters, and the funding pool. The pool is the larger of paying voters times $10 and the pool floor from Settings.

When a snapshot exists whose results have not been emailed yet, a "Results are in... review & send" banner sits at the top with **Review numbers** and **Send results** buttons.

![At a Glance: this cycle's turnout, pool, and live leaders.](/static/help/voting-admin/01-at-a-glance.png)

### The Automatic Month-End Snapshot

You usually do not have to do anything at month end. On the first cron tick of a new month, the app snapshots the cycle that just closed, exactly once, and then automatically emails the results to everyone who voted. Two guards keep it sane:

- It is gated on the **Auto snapshot enabled** switch in Settings.
- It skips itself if any snapshot was already taken during that cycle's window. So if you took a manual snapshot, the automatic one stands down; you will not get doubles.

### Take a Snapshot by Hand {#voting-take-snapshot}

1. Open the **Snapshots** tab. It shows the live vote analyzer for the current state.
2. Optionally set a title and a minimum pool in the "Take a snapshot" form.
3. Click **Take Snapshot**.

The commit always captures the full, unfiltered live state; the analyzer's filters are for your reading only. A snapshot is immutable once taken. If nobody has voted yet, there is nothing to snapshot and the app says so.

![The Take a snapshot form on the Snapshots tab.](/static/help/voting-admin/02-take-snapshot.png)

### Send Results Emails {#voting-send-results}

**Send results** (on the At a Glance banner or a snapshot's history page) emails every member who voted in that snapshot their guild allocations plus their own recorded vote, and drops an in-app notification too. Each snapshot's results send once; asking again gets you "already sent" unless you explicitly **Resend**, which confirms first and then re-emails everyone.

### Funding History

Every snapshot, newest first. **View** opens the per-member audit of exactly who voted for what and how the pool split. **Delete** permanently removes a snapshot and its Airtable mirror; there is no undo, so treat it as a fix for mistakes, not housekeeping.

### Settings {#voting-settings}

The **Settings** tab holds the knobs, saved with **Save voting settings**:

- How many days before close the "Polls closing soon!" reminder goes out.
- The dollar floor for the funding pool.
- On/off switches for the voter reminder, the "Vote soon!" nudge to signed-in non-voters, the officer heads-up to guild leadership, and the automatic month-end snapshot.

![Voting settings: reminders, the pool floor, and the automatic snapshot switch.](/static/help/voting-admin/03-voting-settings.png)

Below the switches, the Email wording links let you edit the text of each voting email, with a live preview and version history.""",
        "screenshots": [
            {
                "file": "01-at-a-glance.png",
                "page": "hub_admin_voting_overview",
                "selector": None,
                "caption": "At a Glance: this cycle's turnout, pool, and live leaders.",
                "as_role": "admin",
            },
            {
                "file": "02-take-snapshot.png",
                "page": "hub_admin_voting_snapshots",
                "selector": ".pl-vote-take",
                "caption": "The Take a snapshot form on the Snapshots tab.",
                "as_role": "admin",
            },
            {
                "file": "03-voting-settings.png",
                "page": "hub_admin_voting_settings",
                "selector": ".pl-vote-settings-form",
                "caption": "Voting settings: reminders, the pool floor, and the automatic snapshot switch.",
                "as_role": "admin",
            },
        ],
    },
    {
        "slug": "guild-lead-quickstart",
        "category": "running-a-guild",
        "title": "Guild Lead Quickstart: Everything You Can Do",
        "sort_order": 10,
        "related": ["your-guild-page", "guild-staff-roles", "guild-announcements"],
        "body": """\
Lead a guild, or hold any staff role on one? You have a full control room for your guild: its portal landing page, orientations, announcements, events, and staff. This page is the map — one line per tool, each linking to a short guide with screenshots.

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

### Start With the Tour {#guild-lead-tour}

The guided tour runs right on your guild's settings page and takes about 30 seconds. Open [Help](/help/) and click **Start** next to **Guild Lead Tools** in the Guided Tours card — retake it anytime.

### Study a Fully Built Guild {#guild-lead-example}

The [Cartographers Guild](/guilds/cartographers-guild/) is a permanent, fictional example guild with everything filled in: banner, gallery, FAQ with a video and a document, links, every staff role, announcements, meeting notes, and a wishlist. It's unlisted — it never appears in the sidebar or the guild directory — but the page itself is real, and only this link gets you there. When you're deciding what your own page could look like, start there.

![The example guild's page: a banner, announcements, staff, hours, and links — all filled in.](/static/help/guild-lead-quickstart/01-example-guild-page.png)

### Everything You Can Do {#guild-lead-toolkit}

Open your guild's page and click **Guild Settings**. Every tool below lives on one of its tabs.

**Your guild's portal landing page:**

- **Basic Information** — name, About text, essential rules, contact email, website, Discord invite, a YouTube video, a featured class, and whether your member roster shows. See [Your Guild Page](/help/running-a-guild/your-guild-page/).
- **Banner and gallery** — a hero banner with a crop tool, plus up to 10 photos with alt text. Same guide.
- **FAQ and links** — questions with optional video and document attachments, plus sidebar links. Same guide.
- **Share and print** — a short link to your page, a printable flyer, and QR codes for the shop wall. Same guide.

**People:**

- **Staff** — add co-leads, secretaries, treasurers, orienters, or invent a title. Every role carries full lead authority, so read [Guild Staff Roles](/help/running-a-guild/guild-staff-roles/) before adding anyone.
- **Orientations** — set recurring hours, respond to requests straight from the email on your phone, and track who's oriented. See [Running Orientations](/help/running-a-guild/running-orientations/).

**Reaching members:**

- **Announcements** — write to your whole guild (in-app, push, email, and Discord from one wizard), manage your mailing list, and review member-proposed announcements. See [Guild Announcements](/help/running-a-guild/guild-announcements/) and [The Announcement Composer](/help/running-a-guild/announcement-composer/).
- **One automatic email** — a thank-you after their orientation. See [Your Guild Page](/help/running-a-guild/your-guild-page/).

**Calendar and records:**

- **Events** — your guild's meetings and events publish straight to the calendars, no approval step. See [Guild Events, Studio Hours, and Meeting Notes](/help/running-a-guild/guild-events-hours-notes/).
- **Studio hours** — the quiet weekly drop-in windows shown on your guild page. Same guide.
- **Meeting notes** — agendas and recaps, with attachments, for members who missed the meeting. Same guide.

**Classes:**

- **Approve classes** — when a class is submitted in your guild's category, you're the first gate before the admin. See [Approving Classes](/help/running-a-guild/approving-classes/).

The two tools you'll reach for most — Announcements and Orientations — are also collected under **Admin Tools**, in the sidebar and on your Home page.

### The One Rule Worth Repeating {#guild-lead-staff-rule}

Anyone you add on the Staff tab gets your full authority — every tab, every tool, including removing you. Titles are labels, not permission levels. Add people you trust with the whole guild.
""",
        "screenshots": [
            {
                "file": "01-example-guild-page.png",
                "page": "/guilds/cartographers-guild/",
                "selector": None,
                "caption": "The example guild's page: a banner, announcements, staff, hours, and links — all filled in.",
                "as_role": "member",
            },
        ],
    },
    {
        "slug": "your-guild-page",
        "category": "running-a-guild",
        "title": "Your Guild Page",
        "sort_order": 20,
        "related": ["guild-staff-roles", "guild-events-hours-notes"],
        "body": """\
If you lead a guild, or hold any staff role on it, you can edit everything on its page. Open your guild's page and click **Guild Settings**. Admins can edit any guild's page too.

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

Wondering how far you can take the page? The [Cartographers Guild](/guilds/cartographers-guild/) is a permanent, fictional example guild with every feature filled in — reachable only by that link.

![The guild settings page. The tabs across the top cover every part of your page.](/static/help/your-guild-page/01-guild-settings.png)

### The Tabs {#guild-edit-page}

Guild Settings is one page with tabs across the top: **Basic Information**, **Meetings**, **Studio Hours**, **Meeting Notes**, **Events**, **Orientations**, **Welcome Packet**, **Images**, **FAQ**, **Links**, **Announcements**, and **Staff**. Basic Information, Meetings, and Images share one **Save Changes** button. Every other tab has its own Save button, so save each tab before you leave it.

This guide covers the page itself. Orientations, announcements, events, studio hours, and staff each have their own guide.

### Basic Information

Your guild's name, the About text, your essential rules, and your links: a YouTube video, a contact email (shown on your page so members know who to reach), your Discord invite, and a website. You can also feature one of your guild's classes and choose whether the member list shows. Click **Save Changes** when you're done.

The **Share & Print** section on the same tab gives you a short link to your public page, a printable flyer (**Open printable flyer**, then print or save as PDF), and QR code downloads for the shop wall.

### Meeting Cadence

On the **Meetings** tab, set a cadence like monthly, 3rd, Thursday, and the page shows the next meeting date automatically. Use the override field for a one-off date, or tick TBA if nothing is scheduled yet. Calendar integration and your guild's Discord webhook settings live here too.

### Banner and Placement

On the **Images** tab, upload a hero banner under **Branding**. To frame it, go back to your guild page: an **Adjust** button appears once a banner is set. Drag the Vertical and Horizontal sliders until it looks right, then click **Save**.

![The Images tab: your banner up top, the photo gallery below.](/static/help/your-guild-page/02-images-tab.png)

### Photo Gallery {#guild-photo-gallery}

The **Gallery** section on the Images tab holds up to 10 photos. They save the moment you upload them; there's no Save button to press. Drag photos to reorder them, add alt text so screen-reader users can follow along, and delete any you don't want.

### FAQ

On the **FAQ** tab, click **+ Add a question**. Each answer can also embed a YouTube video and attach one document: upload a file or paste a link, not both. **Delete this question** saves the whole page right away, so nothing else you typed is lost. Click **Save FAQ** when you're done. Want the section called something other than FAQ? Rename it on the **Basic Information** tab.

![The FAQ tab. Each answer can carry a video and a document.](/static/help/your-guild-page/03-faq-and-links.png)

### Links

On the **Links** tab, add links with a label and a URL, then click **Save Links**. They show on your guild page.

### Your Automatic Email

On the **Orientations** tab you can write a **Thank-you email**, sent to a member once their orientation is marked complete. It's on by default and falls back to standard wording, so you can leave the subject and body blank or write your own. Click its **Save** button.

Only leads, staff, and admins see the Guild Settings button. Everything a staff member can do here, they can do with your full authority; see the guild staff roles guide before adding anyone.""",
        "screenshots": [
            {
                "file": "01-guild-settings.png",
                "page": "/guilds/1/edit/",
                "selector": None,
                "caption": "The guild settings page. The tabs across the top cover every part of your page.",
                "as_role": "guild_lead",
            },
            {
                "file": "02-images-tab.png",
                "page": "/guilds/1/edit/?tab=images",
                "selector": "[x-show=\"section === 'images'\"]",
                "caption": "The Images tab: your banner up top, the photo gallery below.",
                "as_role": "guild_lead",
            },
            {
                "file": "03-faq-and-links.png",
                "page": "/guilds/1/edit/?tab=content",
                "selector": "[x-show=\"section === 'content'\"]",
                "caption": "The FAQ tab. Each answer can carry a video and a document.",
                "as_role": "guild_lead",
            },
        ],
    },
    {
        "slug": "guild-staff-roles",
        "category": "running-a-guild",
        "title": "Guild Staff Roles",
        "sort_order": 30,
        "related": ["your-guild-page", "running-orientations"],
        "body": """\
### One Rule Before Anything Else {#guild-staff-authority}

**Every staff role grants the full authority of the guild lead.** There are no junior roles. Whether you add someone as a Guild Lead, Secretary, Treasurer, Orientator, or under a custom title you invent, the title is only a label. The moment they're on your staff, they can do everything you can do for this guild.

Concretely, every staff member can:

- Edit the guild page: the About text, banner, gallery, FAQ, links, meetings, everything.
- Manage and approve the guild's classes, including reviewing new class submissions.
- Run orientations: confirm and decline requests, and mark members oriented.
- Send announcements to the whole guild and review member-proposed ones.
- Add and remove other staff members, including you.
- Receive every email the lead receives: class review requests and orientation requests go to the lead plus all staff.

So add people you trust with the whole guild, not just with one job.

### Add or Remove Staff {#guild-manage-staff}

1. Open your guild's page and click **Guild Settings**, then the **Staff** tab.
2. Pick the member, then either a preset role (Guild Lead, Secretary, Treasurer, Orientator) or type your own title. One or the other, not both.
3. Click **Add staff member**.

![The Staff tab: current staff with their title badges, and the add form below.](/static/help/guild-staff-roles/01-staff-tab.png)

The same person can hold more than one title; each shows as its own badge. To take a role away, click **Remove** next to that badge. They lose guild-lead access to this guild (for that role) and you can add them back anytime.

![The add form: pick a member, then a preset role or a custom title.](/static/help/guild-staff-roles/02-add-staff-form.png)

### The Guild Lead Itself

The primary guild lead is set by an admin, not on this tab. If your guild's lead needs to change, ask an admin.""",
        "screenshots": [
            {
                "file": "01-staff-tab.png",
                "page": "/guilds/1/edit/?tab=staff",
                "selector": "[x-show=\"section === 'staff'\"]",
                "caption": "The Staff tab: current staff with their title badges, and the add form below.",
                "as_role": "guild_lead",
            },
            {
                "file": "02-add-staff-form.png",
                "page": "/guilds/1/edit/?tab=staff",
                "selector": "form[action='/guilds/1/staff/add/']",
                "caption": "The add form: pick a member, then a preset role or a custom title.",
                "as_role": "guild_lead",
            },
        ],
    },
    {
        "slug": "running-orientations",
        "category": "running-a-guild",
        "title": "Running Orientations",
        "sort_order": 40,
        "related": ["getting-oriented", "guild-staff-roles"],
        "body": """\
Members request orientations from your guild's own page. Every request stays pending until you (or any of your staff) confirm it; nothing is official until then. This guide is the lead's side: setting up booking, responding, and tracking who's oriented.

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

### Turn On Booking

1. Open **Guild Settings**, then the **Orientations** tab.
2. In the **Booking** card, switch booking on and fill in your defaults: how many seats a slot holds, where orientations happen, and how long they run. The info text you write here is shown to members before they book.
3. Choose whether members may propose their own time (custom requests).
4. Click **Save orientation settings**.

![The Orientations tab: booking settings, the thank-you email, and the Orientation Schedule.](/static/help/running-orientations/01-orientations-tab.png)

Going away for a while? The **Closed for orientations** card pauses bookings without losing any of your settings, and shows members your message (like: on vacation till Sept 8).

### Recurring Hours {#orientation-recurring-hours}

The **Recurring hours** card is where bookable times come from. Add one row per weekly window (Tuesdays 6-7 pm and Saturdays 10-11 am are two rows), then click **Save Hours**. Saving turns your hours into concrete bookable slots immediately, and a nightly job keeps the next eight weeks open.

![Recurring hours become bookable slots automatically.](/static/help/running-orientations/02-recurring-hours.png)

### Custom Times and One-Off Slots

If you allow custom requests, a member who can't make your posted times can propose their own. That creates a one-off, single-seat slot at their proposed time and sends you the same request as any other booking; confirm it and it's on. You can also put a member into any upcoming slot yourself from the dashboard (below).

### Respond to a Request {#orientation-respond-requests}

When a member requests a slot, the guild lead and every staff member get an email, and orienters get an in-app notice. You can respond two ways:

- **From the email.** It carries direct confirm and decline links. Each opens a one-click confirmation page and works without logging in, so you can handle a request from your phone.
- **From the app.** Open the request (from the email's respond link, the in-app notice, or the dashboard) and click **Confirm orientation**, or **Decline request** with an optional note, like suggesting another time.

Confirming emails the member a calendar invite. Declining emails them your note. If plans change after you've confirmed, **Cancel this orientation** notifies the member; members can also cancel their own bookings, and you'll see the status change.

### The Orientations Dashboard {#orientation-dashboard}

Click **Orientations** in the left menu (leads, staff, and admins see it). Pending and upcoming bookings sit at the top with **Respond** buttons. Below is the full history: search by member or guild, filter by guild, status, completion, or date range, sort any column, and click **Export CSV** to download exactly what you've filtered.

![The Orientations dashboard: filters, the table, and Export CSV.](/static/help/running-orientations/03-orientations-dashboard.png)

Need to orient someone who never booked? Use **Add a member to a slot** at the bottom: pick the member and an upcoming slot, and they're emailed just like a self-booking (still pending until confirmed).

![Add a member to a slot puts someone into an upcoming orientation for you.](/static/help/running-orientations/04-add-member.png)

### Past Orientations Complete Themselves

Every 15 minutes, a background job marks confirmed orientations complete once their time has passed. Completion sends your thank-you email (if you've set one up on the Orientations tab) and posts a welcome notice to the guild. If a no-show got auto-completed, the guild's lead or an admin can flip it back with the **Mark done** toggle in the dashboard table.""",
        "screenshots": [
            {
                "file": "01-orientations-tab.png",
                "page": "/guilds/1/edit/?tab=orientations",
                "selector": "[x-show=\"section === 'orientations'\"]",
                "caption": "The Orientations tab: booking settings, the thank-you email, and the Orientation Schedule.",
                "as_role": "guild_lead",
            },
            {
                "file": "02-recurring-hours.png",
                "page": "/guilds/1/edit/?tab=orientations",
                "selector": "form[action='/guilds/1/orientation/hours/save/']",
                "caption": "Recurring hours become bookable slots automatically.",
                "as_role": "guild_lead",
            },
            {
                "file": "03-orientations-dashboard.png",
                "page": "hub_orientations_dashboard",
                "selector": None,
                "caption": "The Orientations dashboard: filters, the table, and Export CSV.",
                "as_role": "guild_lead",
            },
            {
                "file": "04-add-member.png",
                "page": "hub_orientations_dashboard",
                "selector": "form[action='/orientations/add-member/']",
                "caption": "Add a member to a slot puts someone into an upcoming orientation for you.",
                "as_role": "guild_lead",
            },
        ],
    },
    {
        "slug": "guild-announcements",
        "category": "running-a-guild",
        "title": "Guild Announcements",
        "sort_order": 50,
        "related": ["announcements", "your-guild-page"],
        "body": """\
Guild leads and staff can announce things to their whole guild: in the app, by email, and on Discord, all from one composer. Admins can announce site-wide too. Regular members can't send announcements directly, but they can propose one for your review (more on that below).

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

### The Compose Wizard {#announcements-compose}

From **Guild Settings**, open the **Announcements** tab and click **Compose announcement**; it opens already pointed at your guild. Two tabs: **Compose** (message, recipients, and the push/email/Discord delivery switches) and **Preview & send** (the push phone line, the email preview, the Discord channel and ping, and the confirm button).

![The composer's Compose tab: message, recipients, and the delivery switches.](/static/help/guild-announcements/01-compose-wizard.png)

The full walkthrough — including push notifications, the urgent switch, and test-sends to yourself — lives in [The Announcement Composer](/help/running-a-guild/announcement-composer/). The short version: everyone in your recipient list always gets it in their notification bell; push and email are on by default and members can tune those in their own settings; Discord is one post to the channel you pick. Sending can't be undone.

### Your Mailing List

The **Announcements** tab shows exactly who your emails reach. Guild members are on the list automatically. You can add custom addresses too (a booster, a partner org) one at a time or by importing a CSV or text file, one email per line.

![The Announcements tab: your mailing list, the compose button, and recent announcements.](/static/help/guild-announcements/02-announcements-tab.png)

### Edit or Delete a Posted Announcement

Under **Recent Announcements** on the same tab, each announcement has **Edit** and **Delete**. Editing updates the title, body, and expiry on your guild page; it never re-sends the email or re-posts to Discord. Deleting asks you to confirm and can't be undone. An expired announcement stays in your list with an Expired badge but is hidden from members.

### Review Member Proposals {#announcements-review-proposals}

Any logged-in member can propose an announcement for any guild. Nothing posts until a lead, staff member, or admin approves it. When proposals are waiting, the Announcements tab shows a banner with a **Review proposals** button; the queue shows proposals for the guilds you help run (admins see every guild's).

![The review queue for member-proposed announcements.](/static/help/guild-announcements/03-review-queue.png)

For each proposal you can:

- **Approve** it. It posts to the guild page, and the approve dialog lets you choose whether it also emails the guild and posts to your Discord channel.
- **Request changes**, with a note. The proposal goes back to the member, who can edit and resubmit it.
- **Decline** it, with a note explaining why.

The proposer is notified of your decision either way.

### Turning Member Suggestions Off {#announcements-member-suggestions}

If you'd rather not take member-proposed announcements, open the **Member Suggestions** card on the **Announcements** tab and switch it off. The **+ Suggest an announcement** button disappears from your guild page, and the suggest page stops offering your guild in its picker. Proposals already in your queue stay decidable, and anyone mid-revision on a changes-requested proposal can still resubmit it. Turn it back on any time.""",
        "screenshots": [
            {
                "file": "01-compose-wizard.png",
                "page": "hub_compose",
                "selector": ".pl-wizard",
                "caption": "The composer's Compose tab: message, recipients, and the delivery switches.",
                "as_role": "guild_lead",
            },
            {
                "file": "02-announcements-tab.png",
                "page": "/guilds/1/edit/?tab=announcements",
                "selector": "[x-show=\"section === 'announcements'\"]",
                "caption": "The Announcements tab: your mailing list, the compose button, and recent announcements.",
                "as_role": "guild_lead",
            },
            {
                "file": "03-review-queue.png",
                "page": "hub_guild_announcement_review_queue",
                "selector": None,
                "caption": "The review queue for member-proposed announcements.",
                "as_role": "guild_lead",
            },
        ],
    },
    {
        "slug": "announcement-composer",
        "category": "running-a-guild",
        "title": "The Announcement Composer",
        "sort_order": 55,
        "related": ["guild-announcements", "notifications"],
        "body": """\
One composer sends an announcement everywhere it needs to go: the notification bell, phones (push), email, and Discord. Admins, guild leads and staff, and instructors all use the same tool. This guide walks it end to end — especially push notifications, which put your announcement straight on members' phones.

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

### Who Can Send, and to Whom {#composer-who}

- **Admins** can send to **Everyone (site-wide)** or to any guild.
- **Guild leads and staff** can send to the guilds they help run.
- **Instructors** can send to the roster of any published class they teach. (Admins reach a class's roster from that class's page — its **Send Announcement** button.)

Ways in: **Admin Tools → Announcements**, the **Send Announcement** button on your guild page or class pages (those arrive already aimed at that guild or class), and **Compose announcement** on your guild's Announcements tab.

### Tab 1: Compose {#composer-compose}

![The Compose tab: audience, message, recipients, and the delivery switches.](/static/help/announcement-composer/01-compose-tab.png)

- **Who is this for?** — pick the audience. If you came from a guild or class page, it's locked to that audience.
- **Message** — a rich-text editor. The formatted version goes out by email; the bell, push, and Discord get a plain-text version.
- **Recipients** (guild and class audiences) — everyone is checked by default. Uncheck anyone, or add a member who isn't on the list. Class audiences also get an **Also include the waitlist** toggle.
- **Delivery** — three switches, all on to start: **Push notification**, **Email**, and **Post to Discord**. (Class announcements never post to Discord — they go straight to your students.)
- **Mark as urgent** — see below. Use sparingly.
- **Hide after** (guild announcements only) — an optional date after which the post hides from your guild page.

![The Delivery switches: push, email, Discord, and the urgent toggle.](/static/help/announcement-composer/02-delivery-switches.png)

Everyone in your recipient list **always** gets the announcement in their notification bell, no matter how you set the switches.

### Push Notifications {#composer-push}

This is the channel that lands on phones, and announcements send it **on by default**.

- **The phone line.** On the Preview & send tab, the **Push Notification Message** box holds the short text shown on the phone (max 180 characters). It starts as an auto-shortened copy of your message — edit it into one clean sentence; that's what people read on their lock screen.
- **The title is automatic**: "&lt;Your Guild&gt; Announcement", "Class Announcement", or "Makerspace Announcement" — with "Urgent:" in front when you mark it urgent.
- **Test it on yourself.** Click **Send a Push Notification test to me** and check your own phone. If it says no push devices are registered, your account has no phone set up yet — install the Android app, or add the Member Portal to your home screen and allow notifications.
- **Who receives it:** members who have the app installed and notifications allowed, and who haven't switched announcement pushes off in **Settings → Notifications**. Class guests without accounts and custom mailing-list addresses are email-only — push and the bell can't reach them.

### Tab 2: Preview & Send {#composer-send}

- **Push** — the phone line and a live preview card of exactly what the phone shows.
- **Email** — click **Refresh preview** to see the real branded email, **Send a test to me** to check your own inbox, and flip **Show who it's from** to add your name to the email (email only; push and the bell never show a sender).
- **Discord** — pick the channel (or **Don't post**) and the ping: **@everyone**, **@here**, your guild's own role ping when it's configured (the gentlest option that still notifies your people), or no ping. It's one post to the channel, not direct messages.
- The reach line totals it up — "Reaching N recipients in the app and by push, plus email" — and **Send announcement** asks you to confirm. Sending can't be undone.

### Mark as Urgent {#composer-urgent}

Urgent is the break-glass switch: it bypasses each recipient's notification preferences, so the announcement reaches everyone **even if they turned announcement emails or pushes off**. Two honest caveats:

- It only overrides the *recipients'* settings. If **you** switched Email or Push off in Delivery, urgent doesn't turn them back on.
- Every copy gets "Urgent:" in front of the title. Save it for closures, safety issues, and genuine emergencies — it spends trust.

### Where It Lands Afterward {#composer-afterward}

- A **guild** announcement also posts to your guild page (and members' Home dashboards) until its hide-after date, if you set one.
- A **class** announcement goes straight to the roster — no public post anywhere.
- A **site-wide** announcement is a notice to everyone, with no page post.
""",
        "screenshots": [
            {
                "file": "01-compose-tab.png",
                "page": "hub_compose",
                "selector": None,
                "caption": "The Compose tab: audience, message, recipients, and the delivery switches.",
                "as_role": "guild_lead",
            },
            {
                "file": "02-delivery-switches.png",
                "page": "hub_compose",
                "selector": ".pl-compose-section:has(#id_push_enabled)",
                "caption": "The Delivery switches: push, email, Discord, and the urgent toggle.",
                "as_role": "guild_lead",
            },
        ],
    },
    {
        "slug": "guild-events-hours-notes",
        "category": "running-a-guild",
        "title": "Guild Events, Studio Hours, and Meeting Notes",
        "sort_order": 60,
        "related": ["community-calendar", "your-guild-page"],
        "body": """\
Three ways to keep members in the loop, each on its own Guild Settings tab. Events go on the community calendars and send members a heads-up. Studio hours simply show on your guild page — no announcements. Meeting notes are the written record members can read later.

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

### Guild Events {#guild-events}

On the **Events** tab, click **+ Add event** to schedule a meeting or event. Your events publish straight away; there's no approval step for guild leads and staff. They show on the Community Calendar and on your guild's own calendar, and members get a heads-up in the app when a new one goes live.

![The Events tab. Add event puts your meetings and events on the calendars.](/static/help/guild-events-hours-notes/01-events-tab.png)

Good to know:

- Set a future publish time and the event is parked with a Scheduled badge until then; the heads-up goes out when it actually publishes.
- Editing a live event updates the calendars but never re-announces it. The heads-up fires once, on publish.
- **Delete** removes the event from the calendars. It can't be undone.
- An event can repeat monthly; the calendar shows each occurrence.

### Studio Hours {#guild-studio-hours}

Studio hours are the weekly windows when someone from your guild — usually the lead or a staff member — is in the studio, so members can drop by to work, ask questions, and get guidance. They only appear on your guild page's Studio Hours card; posting them never announces anything or sends a notification to anyone.

On the **Studio Hours** tab, add one row per window (Tuesdays and Saturdays are two rows) with a day, start and end time, location, and an optional note. Click **Save Studio Hours**. The row's **Delete** button saves the page at the same time, so nothing else is lost.

![The Studio Hours tab: one row per weekly window.](/static/help/guild-events-hours-notes/02-studio-hours-tab.png)

### Meeting Notes {#guild-meeting-notes}

Post your agendas and recaps so members can catch up on what they missed. On the **Meeting Notes** tab:

![The Meeting Notes tab: your posted notes, newest first.](/static/help/guild-events-hours-notes/03-meeting-notes-tab.png)

- **+ Add meeting notes** opens a page for the title, meeting date, write-up, and any attached files or links.
- **Edit** reopens a posted note.
- **Delete** removes the note and all its attachments, after a confirm. It can't be undone.

Posted notes appear on your guild page for members to read.""",
        "screenshots": [
            {
                "file": "01-events-tab.png",
                "page": "/guilds/1/edit/?tab=events",
                "selector": "[x-show=\"section === 'events'\"]",
                "caption": "The Events tab. Add event puts your meetings and events on the calendars.",
                "as_role": "guild_lead",
            },
            {
                "file": "02-studio-hours-tab.png",
                "page": "/guilds/1/edit/?tab=studio_hours",
                "selector": "[x-show=\"section === 'studio_hours'\"]",
                "caption": "The Studio Hours tab: one row per weekly window.",
                "as_role": "guild_lead",
            },
            {
                "file": "03-meeting-notes-tab.png",
                "page": "/guilds/1/edit/?tab=meeting_notes",
                "selector": "[x-show=\"section === 'meeting_notes'\"]",
                "caption": "The Meeting Notes tab: your posted notes, newest first.",
                "as_role": "guild_lead",
            },
        ],
    },
    {
        "slug": "approving-classes",
        "category": "running-a-guild",
        "title": "Approving Classes",
        "sort_order": 70,
        "related": ["become-an-instructor", "reviewing-classes-admin"],
        "body": """\
When someone submits a class in a category tied to your guild, you're the first gate. Review is sequential: the class waits on you before an admin ever sees it, and it can't publish until both of you say yes.

<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->

### How a Review Reaches You

The moment a class is submitted, the guild lead and every staff member get a review-request email, plus an in-app notice. The instructor is told their class is in review. Review is a shared duty: any one of your staff can make the call, and the first decision is the guild's decision.

### Where to Review {#guild-approve-classes}

Two doors lead to the same review page.

**From the email (easiest):** the review request carries a direct review link. It's a signed, personal link that works without logging in, so you can review from anywhere.

**From the teaching area:**

1. Go to `/classes/teach/` (or click **Class Catalog** in the left menu, then **Manage My Classes**).
2. On the Overview, find the **Waiting on your review** panel at the top. You only see this panel if you lead or staff a guild.
3. Click **Review** next to the waiting class.

One caveat: the teaching area itself sits behind the one-time portal orientation for instructors. If you've never unlocked teaching, use the email link instead — it works without it.

![The teaching overview. Classes waiting on your review sit at the top.](/static/help/approving-classes/01-review-panel.png)

![The Waiting on your review panel, with a Review button per class.](/static/help/approving-classes/02-review-queue-panel.png)

The review page shows everything the class will publish with: description, sessions, price, capacity, and photos, plus the history of past review rounds.

### Make the Call

Pick one of three decisions and click **Submit decision**:

- **Approve.** You're vouching for the class; it moves on to the admin gate.
- **Request changes.** Notes are required so the instructor knows what to fix. The class goes back to draft; they edit and resubmit, and a fresh review round starts with you.
- **Decline.** Notes are required here too. The class also returns to draft; declining isn't permanent, and the instructor can rework and resubmit.

Each review link accepts exactly one decision. If you open it again afterwards, or a co-reviewer beat you to it, the page shows what was already decided.

### What Happens After You Approve

Your approval doesn't publish the class. It opens the admin gate: an admin gets a validation request naming you as the lead who vouched, and only their approval publishes the class and opens sign-ups. The instructor is notified at every step, so you don't need to relay anything.""",
        "screenshots": [
            {
                "file": "01-review-panel.png",
                "page": "classes:teach_overview",
                "selector": None,
                "caption": "The teaching overview. Classes waiting on your review sit at the top.",
                "as_role": "guild_lead",
            },
            {
                "file": "02-review-queue-panel.png",
                "page": "classes:teach_overview",
                "selector": '[data-help-key="guild.approve-classes"]',
                "caption": "The Waiting on your review panel, with a Review button per class.",
                "as_role": "guild_lead",
            },
        ],
    },
    # ── Contributing & under the hood (added after P1 — the technical/contributor section) ──
    {
        "slug": "fog-is-open-source",
        "category": "contributing",
        "title": "The Member Portal Is Open Source: How to Contribute",
        "sort_order": 10,
        "related": ["the-fog-api", "how-fog-runs"],
        "body": """The Member Portal — this app — is open source. All of its code is public on GitHub under the MIT license, and any member can read it, report problems, suggest features, or change it directly.

The repository lives at [github.com/Past-Lives-Makerspace/plfog](https://github.com/Past-Lives-Makerspace/plfog).

## Ways to Contribute (No Coding Required)

- **Report a bug or share an idea in the app.** The **Feedback** link at the bottom of the sidebar goes straight to the people who build the Member Portal.
- **Open a GitHub issue.** If you have a GitHub account, [open an issue](https://github.com/Past-Lives-Makerspace/plfog/issues) describing the bug or idea. Screenshots help a lot.
- **Improve these guides.** The Help Center articles live in the code too — typo fixes and clearer wording are real contributions.

## Contributing Code

The Member Portal is a Django (Python) web app. If you can write Python, HTML, or CSS, you can work on it.

1. **Get it running locally.** The README in the repository walks through setup. Local development runs entirely in Docker Compose — you don't need Python or a database installed, just Docker.
2. **Make your change on a branch**, with tests. The project keeps 100% test coverage, so changes come with tests that prove they work.
3. **Open a pull request** (see below).

## What's a Pull Request?

A pull request (a "PR") is how changes get into a shared codebase safely. Instead of editing the live code directly, you publish your proposed change as a bundle that others can read, comment on, and approve — like handing in a draft for review rather than gluing pages straight into the library's only copy.

When you open a PR against the Member Portal, two things happen automatically:

- **The robots check it.** GitHub runs the full test suite, database checks, and code-style linters against your change. If anything fails, the PR is marked red and can't ship until it's fixed.
- **A person checks it.** Every PR needs at least **one approving review** before it can merge. Nothing lands in the main branch unreviewed.

## What Happens When a PR Merges

Merging to the main branch **is** deploying. Render (our hosting platform) watches the main branch: on every merge it builds the app, runs database migrations, and swaps the new version live — usually within a few minutes. That's why the review gate matters: main is production.

!!! note
    You don't need permission to start. Fork the repository, experiment, and open a PR — the worst that happens is a friendly code review. If you'd like a tour of the codebase first, ask in Discord.""",
        "screenshots": [],
    },
    {
        "slug": "the-fog-api",
        "category": "contributing",
        "title": "The Member Portal API",
        "sort_order": 20,
        "related": ["fog-is-open-source", "how-fog-runs"],
        "body": """The Member Portal has a REST API — the same data you see in the app, as JSON, for scripts, bots, and integrations built by members. Our own Discord bot and admin tooling use it.

The base URL is `https://members.pastlives.space/api/v1/`.

## Try It Right Now

If you're logged into the Member Portal in your browser, open [members.pastlives.space/api/v1/](https://members.pastlives.space/api/v1/) — you'll get a browsable version of the API you can click through. Every endpoint shows its data and format right on the page.

## Authentication

Every request must be authenticated — there is no anonymous access. Two ways in:

- **Your browser session.** Being logged into the Member Portal is enough for the browsable API above.
- **A token**, for scripts and bots. Send it as a header: `Authorization: Token YOUR_TOKEN`. Tokens are issued by a Member Portal admin — ask in Discord if you're building something and need one.

Example:

```
curl -H "Authorization: Token YOUR_TOKEN" https://members.pastlives.space/api/v1/guilds/
```

## Endpoints

| Endpoint | Read | Write |
|---|---|---|
| `/api/v1/guilds/` | Any member | Read-only |
| `/api/v1/events/` | Any member | Admins |
| `/api/v1/announcements/` | Any member | Admins |
| `/api/v1/members/` | Admins only | Admins only |
| `/api/v1/plans/` | Admins only | Admins only |

Member data and membership plans are admin-only for privacy. Everything else is readable by any authenticated member.

Writes do real things: creating an event through `POST /api/v1/events/` also pushes it to the Google Calendar and announces it on Discord, exactly as if an admin had created it in the app. Posting an announcement notifies the guild's members. The API is a first-class door into the app, not a side channel.

## Format Details

- Responses are JSON, paginated 50 items per page. Follow the `next` link (or pass `?page=2`) for more.
- List endpoints support the standard REST shapes: `/guilds/` for the list, `/guilds/<id>/` for one record.

!!! tip
    Building something on the API — a dashboard, a bot, a shop-status display? Post about it in Discord. If the API is missing a field or an endpoint you need, [open a GitHub issue](https://github.com/Past-Lives-Makerspace/plfog/issues) or add it yourself (see [The Member Portal Is Open Source](/help/contributing/fog-is-open-source/)).""",
        "screenshots": [],
    },
    {
        "slug": "how-fog-runs",
        "category": "contributing",
        "title": "How the Member Portal Runs: Under the Hood",
        "sort_order": 30,
        "related": ["the-fog-api", "logins-and-usernames"],
        "body": """A tour of the machinery for the curious: what the Member Portal is built with, where it runs, and what it talks to.

## The Stack

- **Django** (Python 3.13) — the web framework. One app serves the member hub, the class catalog, and the API.
- **PostgreSQL** — the database.
- **Render** — the hosting platform. It builds and runs the app, the database, and the scheduled jobs.
- **Cloudflare R2** — stores uploaded images (guild photos, class images, avatars).
- **Resend** — delivers the app's email (login codes, class confirmations, announcements).

The Member Portal is also an installable web app: "Add to Home Screen" on your phone gives you an app icon and push notifications.

## Merging Code Is Deploying

There are no release days. When a change merges to the main branch on GitHub (after tests pass and a human approves — see [The Member Portal Is Open Source](/help/contributing/fog-is-open-source/)), Render picks it up automatically: it installs dependencies, runs database migrations, refreshes seeded content like these help guides, and swaps the new version live. A typical change is in production minutes after merge.

## What the Member Portal Talks To

- **Airtable** — the membership roster lives there; the Member Portal pulls members, spaces, and leases in on a schedule, and pushes guild-voting results back out.
- **Discord** — the Fog Bot mirrors community events into Discord's event list, posts guild announcements and new classes, and links member accounts (see [Discord and the Member Portal](/help/contributing/discord-and-fog/)).
- **Google Calendar** — community events are pushed to the shared calendar automatically.

## Scheduled Jobs

Some things run on a clock rather than a click: the nightly Airtable and Discord syncs, class reminder emails, and orientation slot generation all run as scheduled jobs on Render.

## Where to See All This

Every piece above is defined in the public repository — the Render blueprint, the sync code, the API, the lot. If this page made you curious, [the code](https://github.com/Past-Lives-Makerspace/plfog) is the full story.""",
        "screenshots": [],
    },
    {
        "slug": "logins-and-usernames",
        "category": "contributing",
        "title": "How Logins and Usernames Work",
        "sort_order": 40,
        "related": ["discord-and-fog", "welcome-to-fog"],
        "body": """The Member Portal has no passwords. Here's what happens instead, and why login "just works" with your email.

## Logging In

Enter your email on the login page and the Member Portal emails you a one-time code. Type it in and you're logged in. The code expires after 5 minutes, and your session sticks around afterwards — you won't be asked again every visit.

There's nothing to forget and nothing to reuse from another site, which is the point: a leaked password list somewhere else can never open your Member Portal account.

## Where Your Account Came From

You never "signed up" for the Member Portal, and there's no open registration page. Accounts are created from the makerspace's membership roster — when you became a member, an account was provisioned for the email you gave us. New members get theirs by invite.

That's also the fix when a login code never arrives: the email you typed probably isn't the one on file. Ask a staff member to check.

## More Than One Email

You can attach extra email addresses to your account under **Settings → Emails**. Once verified, **any** of your emails works for login, and you choose which one the Member Portal uses to reach you.

## So What's My Username?

You don't have one — at least not one you ever need to know. Internally the system files your account under your email address, and everything other members see (the directory, announcements, class rosters) uses the display name from your profile. Change it anytime under **Settings → Profile**.""",
        "screenshots": [],
    },
    {
        "slug": "discord-and-fog",
        "category": "contributing",
        "title": "Discord and the Member Portal",
        "sort_order": 50,
        "related": ["logins-and-usernames", "community-calendar"],
        "body": """Past Lives runs on two systems that talk to each other: the Member Portal (this app) for the official record — membership, classes, voting, events — and Discord for the day-to-day conversation. The Fog Bot is the bridge.

## What the Bot Does on Its Own

Without you doing anything, the Fog Bot:

- Mirrors the community calendar into **Discord's event list**, so events show up where you already hang out.
- Posts **guild announcements** to Discord channels (for guilds that turn that on).
- Posts **new classes** when they're published.

## Linking Your Accounts

Linking tells the Member Portal which Discord user is you. Two ways:

- **From the Member Portal:** go to **Settings → Notifications** and hit **Connect Discord**. You'll bounce to Discord to approve, then land back in the Member Portal.
- **From Discord:** click the link the bot posts in the server. If your Discord account's verified email matches your Past Lives email, you're linked in one click — no Member Portal login needed.

## What Linking Gets You

- **Guild sync, both directions.** Reacted to the guild role message in Discord? Those guilds are set up for you in the Member Portal. Follow or unfollow a guild in the Member Portal and your Discord roles update to match.
- One identity across both systems, so your guild channels, roles, and Member Portal account always agree.

Linking is safe by design: a Discord account can only ever be linked to one member, and the Member Portal will never silently swap or reassign a link. You can disconnect anytime from the same settings tab.""",
        "screenshots": [],
    },
    {
        "slug": "calendar-sync",
        "category": "contributing",
        "title": "How the Calendar Syncs: The Member Portal, Google, and Discord",
        "sort_order": 60,
        "related": ["discord-and-fog", "how-fog-runs", "community-calendar"],
        "body": """One calendar, three systems. Events at Past Lives live in the Member Portal (this app), on two shared **Google calendars** (Member and Public), and in **Discord's event list**. This is how they stay in sync — and, just as important, where you should create or edit an event so it behaves.

## The One Rule That Matters

**Where an event is born decides where you manage it.** The Member Portal can push events *out*, but it only *reads* the Google calendars — so an event created in Google can't be edited from the Member Portal, and an event created in the Member Portal can't be edited from Google. Pick one home per event and stick with it.

## Create It in the Member Portal (Recommended)

When you add an event in the Member Portal app, the Member Portal owns it and fans it out:

- It's pushed to the right **Google calendar** (Member or Public) automatically.
- It's pushed to **Discord** as a scheduled event.
- Edit it in the Member Portal and both copies update. Delete it in the Member Portal and both copies disappear.

Because the Member Portal owns it end to end, this is the path that gives you "create once, shows up everywhere; delete once, gone everywhere." If you want a single place to manage everything, make it the Member Portal. (The Member Portal won't double-list its own events: the one it pushes to Google comes back through the import below, and the Member Portal recognizes it by ID and drops the echo.)

## Create It on a Google Calendar

Anyone with edit access to the Member or Public Google calendar can add an event there. The Member Portal then:

- **Imports** it (read-only) on the nightly sync, so it appears on the Community Calendar.
- **Mirrors** it into Discord's event list.

The catch: the Member Portal treats these as read-only. You **cannot edit or delete a Google-born event from the Member Portal** — editing the Member Portal copy just gets overwritten on the next sync. To change its date, time, or details, or to remove it, do that **on the Google calendar**; the Member Portal picks up the change on the next sync.

## Discord Is Downstream

Discord's event list is a mirror, never a source. The Member Portal pushes to it; nothing you do in Discord flows back to the Member Portal or Google. Don't create or delete events directly in Discord — treat it as a read-only display.

## What Deletes What

- Delete a **Member Portal** event in the Member Portal → removed from Google and Discord.
- Delete a **Google** event in Google → removed from the Member Portal and Discord on the next sync.
- Delete an event in **Discord** → nothing else changes, and the Member Portal may re-push it.
- Try to delete a **Google** event from the Member Portal → it doesn't stick; the next sync re-imports it.

## Timing

The Member Portal → Google and Discord happens the moment you save. Google → the Member Portal runs on the **nightly** sync (a scheduled job on Render — see [How the Member Portal Runs](/help/contributing/how-fog-runs/)), so an event added straight to a Google calendar can take until the next sync to appear in the Member Portal and Discord.

## The All-Day Gotcha (For the Curious)

An all-day event is a date with no time, but the Member Portal stores every event as a precise moment — so it has to choose one for "all day." It anchors an all-day event to **local midnight** (Portland time). That sounds trivial, but it's a classic trap: anchor to midnight **UTC** instead and the event renders on the *previous* evening for anyone west of UTC — an all-day event on the 22nd shows up on the 21st. The Member Portal anchors to local midnight so the day is always right. If you ever see an all-day event landing a day early, that's the shape of the bug.

A practical tip that follows from this: if an event happens at a specific time, give it that time instead of marking it all-day. "All day" is for genuinely all-day things (a work party, a social); a 7–9pm event should be entered as 7–9pm, or it will show as an all-day block.

## Where the Code Lives

The feed import and the outbound pushes live in `hub/calendar_service.py` (the Google-calendar feeds) and `core/integrations/google_calendar.py` and `core/integrations/discord_events.py` (the Google and Discord pushes). As always, [the repository](https://github.com/Past-Lives-Makerspace/plfog) is the full story.""",
        "screenshots": [],
    },
]
