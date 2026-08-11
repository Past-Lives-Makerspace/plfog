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

# LEGACY_SLUG_MAP targets that are approved (§10.2) but not seeded until the
# P2 fast-follow. The landing's legacy-anchor filter already drops map entries
# whose target isn't live, so these anchors fall back to /help/ — never a dead
# end — until the article ships.
PENDING_LEGACY_TARGETS: frozenset[str] = frozenset({"notifications"})

# The seeded help-center categories (§10.1) — slug keys the seed command
# update_or_creates on; audience groups them on the landing page.
CATEGORIES: list[dict[str, Any]] = [
    {"slug": "getting-started", "name": "Getting started", "audience": "member", "sort_order": 10},
    {"slug": "guilds", "name": "Guilds", "audience": "member", "sort_order": 20},
    {"slug": "classes", "name": "Taking classes", "audience": "member", "sort_order": 30},
    {"slug": "events-community", "name": "Events & community", "audience": "member", "sort_order": 40},
    {"slug": "teaching", "name": "Teaching", "audience": "instructor", "sort_order": 50},
    {"slug": "running-a-guild", "name": "Running a guild", "audience": "guild_lead", "sort_order": 60},
    {"slug": "admin", "name": "Admin", "audience": "admin", "sort_order": 70},
]

# The P1 launch articles (§10.2) — one entry per article with
# slug / category / title / sort_order / related / body / screenshots.
# P2 articles (2, 3, 14, 15, 25-29) follow in the fast-follow phase.
ARTICLES: list[dict[str, Any]] = [
    {
        "slug": "welcome-to-fog",
        "category": "getting-started",
        "title": "Welcome to FOG: what's where",
        "sort_order": 10,
        "related": ["guilds-and-guild-pages", "taking-a-class"],
        "body": """FOG is the Past Lives member hub — the app where the makerspace runs day to day. Your guilds, the class catalog, the community calendar, guild voting, and your account settings all live here.

## Your home dashboard {#home-dashboard}

Log in and you land on **Home**. It shows:

- **Get started at Past Lives** — a short checklist for new members. Dismiss it once you're settled.
- Quick links — one tap to the **Community Calendar**, **Class Catalog**, **Guild Voting**, **Member Directory**, and **Settings**.
- **Your upcoming** — classes and events you're signed up for.
- **Latest from your guilds** — recent announcements from guilds you've joined.
- **Your guilds** — a chip for each of your guilds, linking straight to its page.

![The home dashboard: your checklist, quick links, upcoming events, and guild news.](/static/help/welcome-to-fog/01-home-dashboard.png)

## Finding your way around

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

![The sidebar: every part of FOG, one click away.](/static/help/welcome-to-fog/02-sidebar-navigation.png)

The top bar has a light/dark theme toggle and your avatar. Open the avatar for **Settings** and **Log Out**.

## Good first steps

- Join a guild or two — see [Guilds and guild pages](/help/guilds/guilds-and-guild-pages/).
- Book a guild orientation — see [Getting oriented](/help/guilds/getting-oriented/).
- Grab a seat in a class — see [Taking a class](/help/classes/taking-a-class/).
- Cast your guild vote — see [Guild voting](/help/guilds/guild-voting/).

## Good to know

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
                "caption": "The sidebar: every part of FOG, one click away.",
                "as_role": "member",
            },
        ],
    },
    {
        "slug": "guilds-and-guild-pages",
        "category": "guilds",
        "title": "Guilds and guild pages",
        "sort_order": 10,
        "related": ["getting-oriented", "guild-voting"],
        "body": """A guild is a group of members built around a craft — ceramics, woodshop, textiles. Guilds run their own classes, hold meetings and orientations, and each month get a share of the funding pool based on how members vote (see [Guild voting](/help/guilds/guild-voting/)).

## Find a guild

Every guild is listed in the **Guilds** section at the bottom of the left sidebar. Click one to open its page.

## What's on a guild page

![A guild page: the tabs across the top, announcements, and the Get Involved panel.](/static/help/guilds-and-guild-pages/01-a-guild-page.png)

Tabs across the top:

- **Overview** — announcements, what the guild is about, upcoming classes, and meetings.
- **Guild Calendar** — that guild's meetings, classes, and orientation times.
- **FAQ**, **Meeting Notes**, and **Gallery** — appear once the guild adds content to them.

The Overview's side panels show the guild's staff, studio hours, next meeting, members, links, and contact info — plus the **Get Involved** panel, where joining happens.

## Join a guild {#guild-join-leave}

1. Open the guild's page.
2. In the **Get Involved** panel, click **Join This Guild**.

![Join This Guild lives in the Get Involved panel on every guild page.](/static/help/guilds-and-guild-pages/02-join-this-guild.png)

Joining is free, and you can be in as many guilds as you want. It puts you on the guild's roster and its announcement emails. Some guilds send a welcome email with next steps, and the guild's leads are notified so they can say hi.

### Leave a guild

1. Click your avatar (top right), open **Settings**, then the **Guilds** tab. Guild pages you've joined also show a **Manage in Settings** shortcut.
2. Flip that guild's toggle off. Changes save instantly — flip it back on any time to rejoin.

![The Guilds tab in Settings: one toggle per guild, saved instantly.](/static/help/guilds-and-guild-pages/03-leave-from-settings.png)

## Good to know

- Anyone can propose an announcement for a guild, but a guild lead or admin has to approve it before it appears — so yours may not show up right away.
- Only guild leads, their staff, and admins can edit a guild page. If you help run a guild and need access, ask an admin.
- Many guilds ask you to get oriented before using their space and tools — see [Getting oriented](/help/guilds/getting-oriented/).
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
                "file": "02-join-this-guild.png",
                "page": "/guilds/ceramics-guild/",
                "selector": '.hub-card:has(form[action$="/join/"])',
                "caption": "Join This Guild lives in the Get Involved panel on every guild page.",
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
        "title": "Getting oriented",
        "sort_order": 20,
        "related": ["guilds-and-guild-pages"],
        "body": """An orientation is how a guild shows you its space, its tools, and its safety rules. You do one per guild, and many guilds ask for it before you use their equipment.

## Book a slot {#orientation-book-slot}

1. Open the guild's page and click **Join an Orientation** in the **Get Involved** panel. It jumps you to the booking section on the **Guild Calendar** tab.

   ![Join an Orientation in the Get Involved panel jumps to the booking section.](/static/help/getting-oriented/01-join-an-orientation.png)

2. The booking section lists upcoming times with a **Request** button next to each open one. Pick a time and click **Request**. (A time marked **Full** has no seats left — pick another.)

   ![The Guild Calendar tab holds the guild's schedule and the orientation booking section.](/static/help/getting-oriented/02-guild-calendar-tab.png)

3. Click **Send request** to confirm.

Your request goes to the guild's leads, and it is not official until one of them approves it. Until then the guild page shows your booking as **Requested — awaiting confirmation from the guild lead**. You'll get an email right away confirming the request was received, with a tentative calendar invite attached — and an "Orientation confirmed" email with a real invite once a lead locks it in.

## Request a custom time {#orientation-request-custom-time}

If none of the posted times work, look for **None of these times work? Request a custom time** below the list. Propose a date and time, add a note if it helps, and click **Send request**. The same rule applies: a guild lead has to confirm it before it's real.

Not every guild offers this — the button only appears when the guild allows custom requests.

## Cancel your booking {#orientation-cancel-booking}

On the guild page, your booking shows under **Your orientation** with a **Cancel my orientation** button. Click it and confirm with **Cancel orientation**. Every orientation email also carries a cancel link. You can cancel any time, before or after it's confirmed, and request a new time whenever you're ready. To reschedule, cancel and book again.

## Good to know

- Once you've done it, the guild page simply shows **You're oriented** — the booking section goes away.
- If a guild pauses bookings, the section says **Orientations paused**. Check back later.
""",
        "screenshots": [
            {
                "file": "01-join-an-orientation.png",
                "page": "/guilds/ceramics-guild/",
                "selector": '.hub-card:has(form[action$="/join/"])',
                "caption": "Join an Orientation in the Get Involved panel jumps to the booking section.",
                "as_role": "member",
            },
            {
                "file": "02-guild-calendar-tab.png",
                "page": "/guilds/ceramics-guild/",
                "selector": 'nav[role="tablist"]',
                "caption": "The Guild Calendar tab holds the guild's schedule and the orientation booking section.",
                "as_role": "member",
            },
        ],
    },
    {
        "slug": "guild-voting",
        "category": "guilds",
        "title": "Guild voting",
        "sort_order": 30,
        "related": ["guilds-and-guild-pages"],
        "body": """Every month, a pool of makerspace funding is split between the guilds — and your vote decides the split. You rank your top three guilds, and each rank earns points: 1st choice 5, 2nd choice 3, 3rd choice 2. More points means a bigger share of the pool.

## Rank your top three {#voting-rank-guilds}

1. Click **Guild Voting** in the left sidebar.
2. Pick three different guilds for **1st Choice (5 pts)**, **2nd Choice (3 pts)**, and **3rd Choice (2 pts)**.

   ![Pick your first, second, and third choice guilds, then submit.](/static/help/guild-voting/01-rank-your-guilds.png)

3. Click **Submit Vote** — or **Update Vote** if you've voted before.

Your ballot is rolling: it sticks and counts every month until you change it. Edit it whenever you like — there's no deadline and no lock while the cycle runs. The **Your Current Votes** card shows your standing ballot and when you last touched it.

## The monthly cycle {#voting-monthly-cycle}

A voting cycle is one calendar month. The voting page shows the current cycle, the day it closes (always the last day of the month), and when the next one begins.

![The voting page shows when the current cycle closes and the next begins.](/static/help/guild-voting/02-cycle-dates.png)

Minutes into the new month, FOG automatically freezes the closed cycle's standings into a snapshot and emails the results to every active member. If you voted, your results email includes a recap of your own ballot. There's nothing to do at month's end — your standing ballot was your vote.

## Watch the standings {#voting-live-standings}

The standings card updates live all month:

- **Current Standings** — the live point tally from everyone's ballots.
- **New Votes This Month** — ballots cast or changed since the last snapshot.
- **Last Month's Results** — the locked-in split: points, share, and dollars per guild, with **Details** and **Full History** links for past cycles.

![Live standings, new votes, and last month's locked-in results.](/static/help/guild-voting/03-live-standings.png)

## Good to know

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
        "title": "Taking a class",
        "sort_order": 10,
        "related": ["community-calendar", "become-an-instructor"],
        "body": """\
Classes at Past Lives are open to everyone — you don't need to be a member or even have an account to take one.

### Find a class {#class-find}

1. Open **Class Catalog** in the left menu, or go straight to [/classes/](/classes/).
2. Narrow things down with the **Guild Type** and **When** dropdowns, or open **Filters** for price range, instructor, member discounts, and free classes.
3. Click a class to see its description, dates, price, and how many spots are left.

![The class catalog — every upcoming class, with filters across the top.](/static/help/taking-a-class/01-class-catalog.png)

### Register and pay {#class-register}

1. On the class page, click **Register now** (a free class says **Register — Free**).
2. Fill in your details, answer any questions, and check the box to agree to the liability waiver.
3. A free class confirms right away. A paid class sends you to a secure Stripe checkout — your spot is locked in once the payment goes through.

![The booking card on a class page — price, spots left, and the Register button.](/static/help/taking-a-class/02-class-page-register.png)

![The registration form — your details, an optional discount code, and the waiver.](/static/help/taking-a-class/03-registration-form.png)

Good to know:

- **Member pricing is automatic.** Register with the email on your Past Lives account and the member price is applied for you — no code needed.
- **Discount codes** go in the **Discount code (optional)** box on the registration form. If a class is on sale, the sale price may not combine with codes — the form tells you when that's the case.
- **You can't join a class after it has started.** That includes joining a series partway through.

### Join the waitlist {#class-join-waitlist}

Sold out? The class page shows **Join the waitlist** instead of the register button.

1. Click **Join the waitlist** and fill in the same form — no payment, no charge to hold your place.
2. You get an email confirming your spot in line and your position.
3. When a confirmed spot opens up, the next person in line gets an email with a link to claim it. That email says how many hours you have before the spot is offered to the next person.

### Manage or cancel your registration {#class-manage-registration}

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
        "title": "The community calendar",
        "sort_order": 10,
        "related": ["propose-an-event", "taking-a-class"],
        "body": """\
The Community Calendar puts everything happening at the space in one place: guild meetings, classes, and community events.

### Browse the calendar {#calendar-browse}

1. Click **Community Calendar** in the left menu.
2. Use the **Week** / **Month** toggle to switch views, and the arrows to move through time.
3. The **Events** tab next to **Calendar** shows the same events as a plain list.
4. Click any event to open its page — the details, plus an **Add to calendar** button that downloads a calendar file for just that event.

![The Community Calendar — Week and Month views, with the Events tab beside them.](/static/help/community-calendar/01-calendar-page.png)

Heads up: some colored events are pulled in from subscribed and guild calendars — not all of them are Past Lives classes, so they won't all appear in the Class Catalog.

### Show or hide calendars {#calendar-filter}

A row of colored filter chips sits above the grid — one per guild or calendar.

1. Click a chip to hide that calendar's events; click it again to bring them back.
2. Your choices are saved in your browser, so they stick on this device.

![The filter chips — click one to hide or show that guild or calendar.](/static/help/community-calendar/02-calendar-filters.png)

### Put it in your own calendar app {#calendar-subscribe}

You'll need to be signed in for this part.

1. On the **Calendar** tab, click **Export Calendar** (top right).
2. Pick **Subscribe via webcal** to keep your calendar app in sync — new events show up there automatically.
3. Or pick **Download .ics (Apple / Outlook)** for a one-time import.

![The Export Calendar button, top right of the Calendar tab.](/static/help/community-calendar/03-export-calendar.png)

The export covers the full community calendar — all guild, general, and class events. To register for a class, use the class's own page instead.

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
                "caption": "The Export Calendar button, top right of the Calendar tab.",
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
        "title": "Propose an event",
        "sort_order": 20,
        "related": ["community-calendar", "announcements"],
        "body": """\
Got a workshop, meetup, or hangout in mind? Any member can propose an event for the Community Calendar.

### Propose it {#event-propose}

1. Open the **Community Calendar** and click **+ Propose an event**.
2. Fill in the form: title, when it starts, whether it repeats, and the details. Pick your guild to propose one of its meetings or events, or leave the guild blank for a site-wide community event.
3. Click **Submit for review**.

![The Events tab — the upcoming list, with the Propose an event button.](/static/help/propose-an-event/01-events-tab.png)

![The proposal form — leave the guild blank for a site-wide event.](/static/help/propose-an-event/02-propose-form.png)

Your event is not on the calendar yet. A guild lead or an admin reviews it first — you'll get a note when they respond. Once it's approved, it publishes to the calendar and gets its own event page anyone can open.

### Track, edit, or withdraw {#event-track}

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

### Where they show up {#announcement-where}

- Every guild page has an **Announcements** section with that guild's posts.
- Your **Home** dashboard shows **Latest from your guilds** — recent announcements from the guilds you've joined.
- A guild's leads can also send an announcement to guild members by email, so keep an eye on your inbox.

![The Announcements section on a guild page, with the Suggest an announcement button.](/static/help/announcements/01-guild-announcements.png)

### Suggest an announcement {#announcement-propose}

Anyone can suggest an announcement for any guild — you don't need to run it.

1. On the guild's page, click **+ Suggest an announcement**.
2. Write your title and message, and submit it.
3. It goes to the guild's leads (or an admin) for review before it posts. You'll get a note when someone responds.

![The Suggest an announcement form.](/static/help/announcements/02-propose-announcement.png)

### Edit or withdraw your proposal {#announcement-manage}

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
        "title": "The member directory",
        "sort_order": 40,
        "related": ["guilds-and-guild-pages"],
        "body": """\
The Member Directory is where you find other makers — by name, guild, or skill — and where you decide what they see about you.

### Find people {#directory-search-filter}

1. Click **Member Directory** in the left menu. You'll need to be signed in (unless an admin has made the directory public).
2. Filter with the **Guild** and **Skill** dropdowns.
3. Type a name or a skill into the **Search** box.
4. Tick **Open for commissions** to see only members taking commission work.
5. Click **Apply**.

![The Member Directory — a card for every listed member.](/static/help/member-directory/01-directory.png)

![Filter by guild or skill, search by name, or show only members open for commissions.](/static/help/member-directory/02-directory-filters.png)

Each card shows what that member chose to share: name, photo, pronouns, contact details, guilds, and skills. Want to be findable by skill? Add yours under **My skills** in your settings.

### Control what others see {#directory-visibility}

You decide what your own card shows. Open **Settings** from your profile menu (top right), then the **Profile** tab:

1. The **Member Directory** switch sets your whole listing to **Public** or **Hidden**.
2. Each field — email, profile photo, pronouns, phone, Discord, about me — has its own **Public** / **Hidden** toggle.
3. Any extra contact methods you add have their own **Show in directory** switch.
4. Click **Save Profile**.

![The directory switch in your Settings — set your listing to Public or Hidden.](/static/help/member-directory/03-visibility-controls.png)

The fine print:

- Anything you switch off stays private to staff.
- Admins, guild officers, guild leads, and instructors are always listed — their role needs a public profile — but they still choose which fields appear on their card.
- Some things are never shown to other members, no matter what: your full legal name, billing details, emergency contacts, and your account status or notes.
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
                "caption": "Filter by guild or skill, search by name, or show only members open for commissions.",
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
        "slug": "become-an-instructor",
        "category": "teaching",
        "title": "Become an instructor",
        "sort_order": 10,
        "related": ["run-your-class", "taking-a-class"],
        "body": """\
Any active member can become an instructor at Past Lives. There is no application to fill out and no waiting on approval — just a short, one-time orientation.

What nobody can do is self-publish. Every class goes through review before it appears in the catalog, no matter who wrote it.

### Complete the orientation {#teach-become-instructor}

The first time you head for the teaching portal, you land on the [instructor orientation](/classes/teach/orientation/). It's one page: what we expect from instructors, how class review works, and the quality bar. Read it, tick the box, and the portal unlocks right away — no admin sign-off, and you only ever do it once.

Already taught a class before the orientation existed? You're grandfathered in — the portal is already open for you.

One note on the word "instructor": an admin can set your role to Instructor, which creates your public instructor page in the class catalog (and opens the teaching portal for you if it wasn't open already). The role and the unlock are separate things — you don't need the role to teach.

### Open the teaching portal

Three ways in:

1. Click **Class Catalog** in the left menu, then **Manage My Classes**.
2. Go straight to `/classes/teach/`.
3. On any guild page, click **Teach a Class** in the Get Involved panel. That one jumps straight to the new class form.

Haven't done the orientation yet? Any of these takes you there first — finish it and you're through.

![The teaching portal Overview: your drafts, classes in review, and recent sign-ups.](/static/help/become-an-instructor/01-teaching-portal.png)

### Create your draft {#teach-create-class}

1. In the portal, open the **Classes** tab and click **+ New Class** (your first time, the button says **+ Create your first class**).
2. Fill in the basics: title, guild category, description, price, and how many spots.
3. Add your dates. A class can be one session or a series; add one row per session. You can also pick flexible scheduling if the dates are arranged later.
4. Add at least one photo. A class needs its own hero image or one gallery photo before it can be submitted.
5. Click **Save Draft** to keep working, or **Save & Submit for Review** when it is ready.

![The new class form: describe it, price it, and add your session dates.](/static/help/become-an-instructor/02-new-class-form.png)

A draft is private. Only you and admins can see it, and you can edit it as much as you like. **Preview** shows you the public page exactly as a student will see it.

### Submit it for review {#teach-submit-for-review}

Click **Save & Submit for Review** on the form, or **Submit for review** on the class page. Review happens in order:

- If your class's category belongs to a guild that has a lead, that guild lead reviews it first.
- Then an admin gives the final yes. No guild lead involved? The admin reviews it directly.

Reviewers see your class exactly as a student would, in a full preview of the public page. Each reviewer picks **Approve**, **Request changes**, or **Decline**, and has to leave a note when requesting changes or declining, so you always know what to fix. You get an email as each decision lands.

**Request changes** and **Decline** send the class back to Draft with the reviewer's notes. Fix it up and submit again; a fresh submission restarts the review from the first gate.

### What the statuses mean

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
        "title": "Instructor orientation",
        "sort_order": 0,
        "related": [],
        "body": """\
Teaching at Past Lives is open to every active member. This page is the one-time orientation: read it, tick the box at the bottom, and the teaching portal unlocks right away.

## What we expect from instructors {#what-we-expect}

- **Show up prepared.** Know your material, have your tools and supplies sorted, and start on time.
- **Keep it safe.** You're responsible for how tools are used in your class. If a session uses guild equipment, make sure everyone in the room is cleared to use it — or build that training into the class.
- **Be straight in your listing.** The title, description, and price should match what students actually get. No surprises on the day.
- **Look after your students.** Answer questions, use the portal's email tool to keep registrants posted, and tell an admin early if you have to cancel.

## How class review works {#how-review-works}

You never self-publish — every class is reviewed before it appears in the catalog:

1. You write a **draft**. Drafts are private; only you and admins can see them.
2. You **submit it for review** when it's ready.
3. If your class's category belongs to a guild with a lead, that **guild lead** reviews it first.
4. An **admin** gives the final yes. Only then does it publish.

Reviewers can approve, request changes, or decline — and they have to leave a note when sending something back, so you always know what to fix. You get an email as each decision lands.

## The quality bar {#the-quality-bar}

Before a class can be submitted it needs:

- **At least one photo** — its own hero image or a gallery photo. Classes with real photos of the work get real sign-ups.
- **A description that answers the basics** — what students will make or learn, what's provided, and what (if anything) to bring.
- **Fair pricing** — cover your materials and time. If you set a member discount, members get it automatically when they register with their member email.""",
        "screenshots": [],
    },
    {
        "slug": "run-your-class",
        "category": "teaching",
        "title": "Run your class",
        "sort_order": 20,
        "related": ["become-an-instructor", "taking-a-class"],
        "body": """\
Once your class is submitted or live, the teaching portal at `/classes/teach/` is where you run it: see who signed up, email them, watch the waitlist, and offer the class again.

The portal has four tabs: **Overview**, **Classes**, **Registrations**, and **Discount Codes**. Opening a class from the **Classes** tab gives that class its own workspace with sub-tabs: **Overview**, **Registrations**, **Waitlist**, **Discount Codes**, and **Emails**.

![The Classes tab lists every class you teach, with its status and sign-up count.](/static/help/run-your-class/01-your-classes.png)

### See who signed up

Open the portal-wide **Registrations** tab to see students for all your classes at once, grouped by class. Or open one class and use its **Registrations** sub-tab. Each row shows the student's name, email, status, when they registered, and their answers to any registration questions.

Need the list outside the app? Click **Export Data** on a class's Registrations sub-tab to download the full roster as a CSV. It opens in any spreadsheet.

![The Registrations tab: your students grouped by class, with the email tool.](/static/help/run-your-class/02-registrations.png)

### Email your students {#teach-email-students}

1. On a Registrations tab, tick the students you want to reach.
2. Click **Email selected students**.
3. Write a subject and message. Leave **Send me a copy** checked to get your own copy.
4. Click **Send**.

You can only email people registered for your own classes, and the per-class tab only reaches that class's students.

### The waitlist

When a class fills up, new sign-ups join a waitlist. You do not manage it by hand: the moment a confirmed spot opens (someone cancels or is refunded), the app emails the next person in line a link to claim the spot and marks them as notified. The **Waitlist** sub-tab shows everyone in order, with when they joined and whether they have been notified yet. Your **Overview** tab also flags any class with people waiting.

### Offer it again {#teach-duplicate-run}

You do not need to rebuild a class to run it on new dates.

1. While the class is a draft or pending, open its **Edit** page.
2. At the bottom, click **+ Offer on another set of dates**.
3. You get a draft copy with no dates. Add the new dates, then submit it for review.

The new run stays grouped with the original on the public page, and every new run goes through review before it publishes.

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
        "title": "Members & invites",
        "sort_order": 10,
        "related": ["reviewing-classes-admin", "voting-admin"],
        "body": """\
**Manage Members** in the left menu (admins only) is the roster: every member, every not-yet-member user, and the invite pipeline, on one page.

The list shows members first, then any user accounts with no membership attached ("Non-member user"). Filter by status, role, or member type, flag members with no email on file, or search by name, email, or Discord handle.

![Manage Members: the full roster with filters, search, and the invites panel.](/static/help/members-and-invites/01-members-page.png)

### Invite a member {#admin-invite-member}

1. In the **Members & invites** card at the top, click **+ Invite a member**.
2. Enter their email and click **Send invite**.

They get an email with a signup link that pre-fills their address. Invites expire after 14 days. Un-accepted invites sit in the panel with two buttons each:

- **Resend** fires the invite email again.
- **Revoke** kills the signup link. You can always re-invite them later.

Expired invites collapse behind a count; **Clear expired** revokes them all in one click.

Prefer to skip the email? **+ Add member** creates the member directly on the roster, with no invite and no email sent. Use **Send login invite** later (see below) when they are ready to sign in.

### Edit a member {#admin-edit-member}

Click **Edit** on any row. The edit page has two tabs.

**Details** holds their name, pronouns, Discord handle, status, member type, directory visibility, and the **Role** dropdown:

- **Admin**, **Guild Officer**, and **Member** set the hierarchy role.
- **Instructor** keeps them a regular member and creates their public instructor page in the class catalog. It is not what grants teaching access; any active member can already use the teaching portal.
- **Guest** deactivates the member. No hub access.

There is also a **Can approve their own discount codes** checkbox: with it on, that member can activate their own class discount codes without waiting for an admin. Click **Save member** to apply.

For a member who has never signed in, the Details tab shows **Send login invite**: it emails them a first-time sign-in link.

![The member edit page: Details, the Role dropdown, and the Emails tab.](/static/help/members-and-invites/02-members-table.png)

### Email aliases {#admin-email-aliases}

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
        "title": "Reviewing & publishing classes",
        "sort_order": 20,
        "related": ["members-and-invites", "become-an-instructor"],
        "body": """\
The classes admin lives at `/classes/admin/`. Reach it from **Class Catalog** in the left menu, then **Manage classes** (the button admins see where members see Manage My Classes). It requires an actual admin account; instructors and guild leads run their own classes from the teaching portal instead.

### The review queue {#admin-review-queue}

The Overview's "Needs your attention" panel lists every class waiting on review. Each row has two buttons:

- **Approve** records your admin approval on the spot.
- **Review** opens the full review page: the class details, upcoming sessions, a student-eye preview of the public page, and a decision form with **Approve**, **Request changes**, and **Decline**. Notes are optional on approve and required on the other two, so the instructor always knows what to fix. **Submit decision** records it and emails the instructor.

Review order matters. When the class's category belongs to a guild that has a lead, the guild lead reviews first, through a tokenized link emailed to them (no admin access needed). Your admin gate only opens after the lead approves, so quick-approving early gets you a "waiting on the remaining reviewer(s)" message rather than a publish. Once every required approval is in, the class publishes and opens for sign-ups. **Request changes** and **Decline** send it back to the instructor as a draft, with your notes.

Admins can also create classes directly from the **Classes** tab; those publish immediately, with no review chain.

![Needs your attention: every class waiting on a review decision.](/static/help/reviewing-classes-admin/01-review-queue.png)

### Archive or delete

**Archive** (on a class's detail page) takes a class off the public portal and out of the instructor's dashboard. Registrations are preserved, everyone who booked gets a cancellation email, and you can re-open it later via the Archived filter on the classes list.

Delete is only for classes with zero registrations, ever. Anything with registration history refuses to delete so the audit trail survives; archive it instead.

![The classes list: filter by status, including Pending and Archived.](/static/help/reviewing-classes-admin/02-classes-list.png)

### Fix a registration {#admin-refund-registration}

Open the **Registrations** tab and click into a registration. Three admin-only actions:

- **Cancel Registration** frees the spot. If someone is on the waitlist, the next person is automatically emailed a claim link.
- **Move** reassigns the registration to a different class. Payment stays exactly as it is; there is no price reconciliation, so sort any price difference out separately.
- **Mark Refunded** frees the spot, promotes the waitlist, notifies the registrant, and records the refund in the activity feed. It does NOT move any money. Issue the actual refund by hand in the Stripe dashboard; this button is the bookkeeping half.

![A registration's detail page, with Cancel, Move, and Mark Refunded.](/static/help/reviewing-classes-admin/03-registrations.png)

### Discount code approvals

Every discount code an instructor creates starts inactive, waiting on approval. The **Discount Codes** tab shows them all; **Approve** activates a code and **Unapprove** switches it back off. Members you have given the "can approve their own discount codes" checkbox (on their Manage Members edit page) can activate their own without you.""",
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
        "title": "Voting admin",
        "sort_order": 30,
        "related": ["guild-voting", "members-and-invites"],
        "body": """\
Guild voting mostly runs itself. Members keep one ranked ballot each, editable any time, and it counts every month until they change it. The cycle is the calendar month, closing on the last day, and the standings are always live; there is no hard lock. Your job as admin is to watch turnout, freeze the month's results into a snapshot, and send members their results.

As an admin, the Guild Voting page grows a tab bar: **Overview** (the same ballot page members see), **At A Glance**, **Funding History**, **Snapshots**, and **Settings**.

### At A Glance {#admin-voting-overview}

The read-only dashboard for the current cycle: members with votes, active members, participation rate, paying voters, and the funding pool. The pool is the larger of paying voters times $10 and the pool floor from Settings.

When a snapshot exists whose results have not been emailed yet, a "Results are in... review & send" banner sits at the top with **Review numbers** and **Send results** buttons.

![At A Glance: this cycle's turnout, pool, and live leaders.](/static/help/voting-admin/01-at-a-glance.png)

### The automatic month-end snapshot

You usually do not have to do anything at month end. On the first cron tick of a new month, the app snapshots the cycle that just closed, exactly once, and then automatically emails the results to everyone who voted. Two guards keep it sane:

- It is gated on the **Auto snapshot enabled** switch in Settings.
- It skips itself if any snapshot was already taken during that cycle's window. So if you took a manual snapshot, the automatic one stands down; you will not get doubles.

### Take a snapshot by hand {#voting-take-snapshot}

1. Open the **Snapshots** tab. It shows the live vote analyzer for the current state.
2. Optionally set a title and a minimum pool in the "Take a snapshot" form.
3. Click **Take Snapshot**.

The commit always captures the full, unfiltered live state; the analyzer's filters are for your reading only. A snapshot is immutable once taken. If nobody has voted yet, there is nothing to snapshot and the app says so.

![The Take a snapshot form on the Snapshots tab.](/static/help/voting-admin/02-take-snapshot.png)

### Send results emails {#voting-send-results}

**Send results** (on the At A Glance banner or a snapshot's history page) emails every member who voted in that snapshot their guild allocations plus their own recorded vote, and drops an in-app notification too. Each snapshot's results send once; asking again gets you "already sent" unless you explicitly **Resend**, which confirms first and then re-emails everyone.

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
                "caption": "At A Glance: this cycle's turnout, pool, and live leaders.",
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
        "slug": "your-guild-page",
        "category": "running-a-guild",
        "title": "Your guild page",
        "sort_order": 10,
        "related": ["guild-staff-roles", "guild-events-hours-notes"],
        "body": """\
If you lead a guild, or hold any staff role on it, you can edit everything on its page. Open your guild's page and click **Guild Settings**. Admins can edit any guild's page too.

![The guild settings page. The tabs across the top cover every part of your page.](/static/help/your-guild-page/01-guild-settings.png)

### The tabs {#guild-edit-page}

Guild Settings is one page with tabs across the top: **Basic Information**, **Meetings**, **Studio Hours**, **Meeting Notes**, **Events**, **Orientations**, **Images**, **FAQ & Links**, **Announcements/Emails**, and **Staff**. Basic Information, Meetings, and Images share one **Save Changes** button. Every other tab has its own Save button, so save each tab before you leave it.

This guide covers the page itself. Orientations, announcements, events, studio hours, and staff each have their own guide.

### Basic information

Your guild's name, the About text, your essential rules, and your links: a YouTube video, a contact email (shown on your page so members know who to reach), your Discord invite, and a website. You can also feature one of your guild's classes and choose whether the member list shows. Click **Save Changes** when you're done.

The **Share & Print** section on the same tab gives you a short link to your public page, a printable flyer (**Open printable flyer**, then print or save as PDF), and QR code downloads for the shop wall.

### Meeting cadence

On the **Meetings** tab, set a cadence like monthly, 3rd, Thursday, and the page shows the next meeting date automatically. Use the override field for a one-off date, or tick TBA if nothing is scheduled yet. Calendar integration and your guild's Discord webhook settings live here too.

### Banner and placement

On the **Images** tab, upload a hero banner under **Branding**. To frame it, go back to your guild page: an **Adjust** button appears once a banner is set. Drag the Vertical and Horizontal sliders until it looks right, then click **Save**.

![The Images tab: your banner up top, the photo gallery below.](/static/help/your-guild-page/02-images-tab.png)

### Photo gallery {#guild-photo-gallery}

The **Gallery** section on the Images tab holds up to 10 photos. They save the moment you upload them; there's no Save button to press. Drag photos to reorder them, add alt text so screen-reader users can follow along, and delete any you don't want.

### FAQ

On the **FAQ & Links** tab, click **+ Add a question**. Each answer can also embed a YouTube video and attach one document: upload a file or paste a link, not both. **Delete this question** saves the whole page right away, so nothing else you typed is lost. Click **Save FAQ** when you're done. Want the section called something other than FAQ? Rename it on the **Basic Information** tab.

![The FAQ & Links tab. Each answer can carry a video and a document.](/static/help/your-guild-page/03-faq-and-links.png)

### Links

Below the FAQ editor, add links with a label and a URL, then click **Save Links**. They show on your guild page.

### Two automatic emails

On the **Announcements/Emails** tab you can write a **Thank-you email**, sent to a member once their orientation is marked complete, and a **Welcome email**, sent when a member joins your guild. Each one only goes out when it's switched on and has both a subject and a body. Click **Save emails**.

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
                "caption": "The FAQ & Links tab. Each answer can carry a video and a document.",
                "as_role": "guild_lead",
            },
        ],
    },
    {
        "slug": "guild-staff-roles",
        "category": "running-a-guild",
        "title": "Guild staff roles",
        "sort_order": 20,
        "related": ["your-guild-page", "running-orientations"],
        "body": """\
### One rule before anything else {#guild-staff-authority}

**Every staff role grants the full authority of the guild lead.** There are no junior roles. Whether you add someone as a Guild Lead, Secretary, Treasurer, Orientator, or under a custom title you invent, the title is only a label. The moment they're on your staff, they can do everything you can do for this guild.

Concretely, every staff member can:

- Edit the guild page: the About text, banner, gallery, FAQ, links, meetings, everything.
- Manage and approve the guild's classes, including reviewing new class submissions.
- Run orientations: confirm and decline requests, and mark members oriented.
- Send announcements to the whole guild and review member-proposed ones.
- Add and remove other staff members, including you.
- Receive every email the lead receives: class review requests and orientation requests go to the lead plus all staff.

So add people you trust with the whole guild, not just with one job.

### Add or remove staff {#guild-manage-staff}

1. Open your guild's page and click **Guild Settings**, then the **Staff** tab.
2. Pick the member, then either a preset role (Guild Lead, Secretary, Treasurer, Orientator) or type your own title. One or the other, not both.
3. Click **Add staff member**.

![The Staff tab: current staff with their title badges, and the add form below.](/static/help/guild-staff-roles/01-staff-tab.png)

The same person can hold more than one title; each shows as its own badge. To take a role away, click **Remove** next to that badge. They lose guild-lead access to this guild (for that role) and you can add them back anytime.

![The add form: pick a member, then a preset role or a custom title.](/static/help/guild-staff-roles/02-add-staff-form.png)

### The guild lead itself

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
        "title": "Running orientations",
        "sort_order": 30,
        "related": ["getting-oriented", "guild-staff-roles"],
        "body": """\
Members request orientations from your guild's own page. Every request stays pending until you (or any of your staff) confirm it; nothing is official until then. This guide is the lead's side: setting up booking, responding, and tracking who's oriented.

### Turn on booking

1. Open **Guild Settings**, then the **Orientations** tab.
2. In the **Booking** card, switch booking on and fill in your defaults: how many seats a slot holds, where orientations happen, and how long they run. The info text you write here is shown to members before they book.
3. Choose whether members may propose their own time (custom requests).
4. Click **Save orientation settings**.

![The Orientations tab: booking settings, the pause switch, and recurring hours.](/static/help/running-orientations/01-orientations-tab.png)

Going away for a while? The **Closed for orientations** card pauses bookings without losing any of your settings, and shows members your message (like: on vacation till Sept 8).

### Recurring hours {#orientation-recurring-hours}

The **Recurring hours** card is where bookable times come from. Add one row per weekly window (Tuesdays 6-7 pm and Saturdays 10-11 am are two rows), then click **Save Hours**. Saving turns your hours into concrete bookable slots immediately, and a nightly job keeps the next eight weeks open.

![Recurring hours become bookable slots automatically.](/static/help/running-orientations/02-recurring-hours.png)

### Custom times and one-off slots

If you allow custom requests, a member who can't make your posted times can propose their own. That creates a one-off, single-seat slot at their proposed time and sends you the same request as any other booking; confirm it and it's on. You can also put a member into any upcoming slot yourself from the dashboard (below).

### Respond to a request {#orientation-respond-requests}

When a member requests a slot, the guild lead and every staff member get an email, and orienters get an in-app notice. You can respond two ways:

- **From the email.** It carries direct confirm and decline links. Each opens a one-click confirmation page and works without logging in, so you can handle a request from your phone.
- **From the app.** Open the request (from the email's respond link, the in-app notice, or the dashboard) and click **Confirm orientation**, or **Decline request** with an optional note, like suggesting another time.

Confirming emails the member a calendar invite. Declining emails them your note. If plans change after you've confirmed, **Cancel this orientation** notifies the member; members can also cancel their own bookings, and you'll see the status change.

### The Orientations dashboard {#orientation-dashboard}

Click **Orientations** in the left menu (leads, staff, and admins see it). Pending and upcoming bookings sit at the top with **Respond** buttons. Below is the full history: search by member or guild, filter by guild, status, completion, or date range, sort any column, and click **Export CSV** to download exactly what you've filtered.

![The Orientations dashboard: filters, the table, and Export CSV.](/static/help/running-orientations/03-orientations-dashboard.png)

Need to orient someone who never booked? Use **Add a member to a slot** at the bottom: pick the member and an upcoming slot, and they're emailed just like a self-booking (still pending until confirmed).

![Add a member to a slot puts someone into an upcoming orientation for you.](/static/help/running-orientations/04-add-member.png)

### Past orientations complete themselves

Every 15 minutes, a background job marks confirmed orientations complete once their time has passed. Completion sends your thank-you email (if you've set one up on the Announcements/Emails tab) and posts a welcome notice to the guild. If a no-show got auto-completed, the guild's lead or an admin can flip it back with the **Mark done** toggle in the dashboard table.""",
        "screenshots": [
            {
                "file": "01-orientations-tab.png",
                "page": "/guilds/1/edit/?tab=orientations",
                "selector": "[x-show=\"section === 'orientations'\"]",
                "caption": "The Orientations tab: booking settings, the pause switch, and recurring hours.",
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
        "title": "Guild announcements",
        "sort_order": 40,
        "related": ["announcements", "your-guild-page"],
        "body": """\
Guild leads and staff can announce things to their whole guild: in the app, by email, and on Discord, all from one composer. Admins can announce site-wide too. Regular members can't send announcements directly, but they can propose one for your review (more on that below).

### The compose wizard {#announcements-compose}

From **Guild Settings**, open the **Announcements/Emails** tab and click **Compose announcement**; it opens already pointed at your guild. The wizard walks three steps:

![The compose wizard: three steps across the top, starting with audience and message.](/static/help/guild-announcements/01-compose-wizard.png)

1. **Audience & message.** Pick who it's for, write the title and body, and (for a guild announcement) set an optional expiry date, after which it hides from your guild page.
2. **Email.** Everyone in the audience always sees the announcement in the app. Flip **Also send as email** to also send the branded email; you can uncheck individual recipients, click **Refresh preview** to see exactly what lands in inboxes, and **Send a test to me** to check your own inbox first.
3. **Discord.** Pick the Discord channel it should post to, or no channel at all. If you pick one, you can ping everyone (the default), only online members (@here), or nobody.

The last step shows how many people you're reaching. Click **Send announcement** and confirm; sending can't be undone. Members without an email on file still see it in the app; they just can't be emailed.

### Your mailing list

The **Announcements/Emails** tab shows exactly who your emails reach. Guild members are on the list automatically. You can add custom addresses too (a booster, a partner org) one at a time or by importing a CSV or text file, one email per line.

![The Announcements/Emails tab: your mailing list, the compose button, and recent announcements.](/static/help/guild-announcements/02-announcements-tab.png)

### Edit or delete a posted announcement

Under **Recent Announcements** on the same tab, each announcement has **Edit** and **Delete**. Editing updates the title, body, and expiry on your guild page; it never re-sends the email or re-posts to Discord. Deleting asks you to confirm and can't be undone. An expired announcement stays in your list with an Expired badge but is hidden from members.

### Review member proposals {#announcements-review-proposals}

Any logged-in member can propose an announcement for any guild. Nothing posts until a lead, staff member, or admin approves it. When proposals are waiting, the Announcements/Emails tab shows a banner with a **Review proposals** button; the queue shows proposals for the guilds you help run (admins see every guild's).

![The review queue for member-proposed announcements.](/static/help/guild-announcements/03-review-queue.png)

For each proposal you can:

- **Approve** it. It posts to the guild page, and the approve dialog lets you choose whether it also emails the guild and posts to your Discord channel.
- **Request changes**, with a note. The proposal goes back to the member, who can edit and resubmit it.
- **Decline** it, with a note explaining why.

The proposer is notified of your decision either way.""",
        "screenshots": [
            {
                "file": "01-compose-wizard.png",
                "page": "hub_compose",
                "selector": ".pl-wizard",
                "caption": "The compose wizard: three steps across the top, starting with audience and message.",
                "as_role": "guild_lead",
            },
            {
                "file": "02-announcements-tab.png",
                "page": "/guilds/1/edit/?tab=announcements",
                "selector": "[x-show=\"section === 'announcements'\"]",
                "caption": "The Announcements/Emails tab: your mailing list, the compose button, and recent announcements.",
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
        "slug": "guild-events-hours-notes",
        "category": "running-a-guild",
        "title": "Guild events, studio hours, and meeting notes",
        "sort_order": 50,
        "related": ["community-calendar", "your-guild-page"],
        "body": """\
Three ways to keep members in the loop, each on its own Guild Settings tab. Events make noise, studio hours stay quiet, and meeting notes are the paper trail.

### Guild events {#guild-events}

On the **Events** tab, click **+ Add event** to schedule a meeting or event. Your events publish straight away; there's no approval step for guild leads and staff. They show on the Community Calendar and on your guild's own calendar, and members get a heads-up in the app when a new one goes live.

![The Events tab. Add event puts your meetings and events on the calendars.](/static/help/guild-events-hours-notes/01-events-tab.png)

Good to know:

- Set a future publish time and the event is parked with a Scheduled badge until then; the heads-up goes out when it actually publishes.
- Editing a live event updates the calendars but never re-announces it. The heads-up fires once, on publish.
- **Delete** removes the event from the calendars. It can't be undone.
- An event can repeat monthly; the calendar shows each occurrence.

### Studio hours {#guild-studio-hours}

Studio hours are the weekly windows when someone from your guild is around for members to drop by. They're ambient: shown on your guild page's Studio Hours card, never announced, never a notification to anyone.

On the **Studio Hours** tab, add one row per window (Tuesdays and Saturdays are two rows) with a day, start and end time, location, and an optional note. Click **Save Studio Hours**. The row's **Delete** button saves the page at the same time, so nothing else is lost.

![The Studio Hours tab: one row per weekly window.](/static/help/guild-events-hours-notes/02-studio-hours-tab.png)

### Meeting notes {#guild-meeting-notes}

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
        "title": "Approving classes",
        "sort_order": 60,
        "related": ["become-an-instructor", "reviewing-classes-admin"],
        "body": """\
When someone submits a class in a category tied to your guild, you're the first gate. Review is sequential: the class waits on you before an admin ever sees it, and it can't publish until both of you say yes.

### How a review reaches you

The moment a class is submitted, the guild lead and every staff member get a review-request email, plus an in-app notice. The instructor is told their class is in review. Review is a shared duty: any one of your staff can make the call, and the first decision is the guild's decision.

### Where to review {#guild-approve-classes}

Two doors, same review page:

- **The email link.** The review request carries a direct review link. It's a signed, personal link that works without logging in, so you can review from anywhere.
- **The teaching area.** Open the teaching overview and the **Waiting on your review** panel sits at the top, one **Review** button per waiting class. (You'll only see this panel if you lead or staff a guild.)

![The teaching overview. Classes waiting on your review sit at the top.](/static/help/approving-classes/01-review-panel.png)

![The Waiting on your review panel, with a Review button per class.](/static/help/approving-classes/02-review-queue-panel.png)

The review page shows everything the class will publish with: description, sessions, price, capacity, and photos, plus the history of past review rounds.

### Make the call

Pick one of three decisions and click **Submit decision**:

- **Approve.** You're vouching for the class; it moves on to the admin gate.
- **Request changes.** Notes are required so the instructor knows what to fix. The class goes back to draft; they edit and resubmit, and a fresh review round starts with you.
- **Decline.** Notes are required here too. The class also returns to draft; declining isn't permanent, and the instructor can rework and resubmit.

Each review link accepts exactly one decision. If you open it again afterwards, or a co-reviewer beat you to it, the page shows what was already decided.

### What happens after you approve

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
                "selector": "div.hub-card > section.hub-card",
                "caption": "The Waiting on your review panel, with a Review button per class.",
                "as_role": "guild_lead",
            },
        ],
    },
]
