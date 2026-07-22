# Past Lives Makerspace (plfog)

Membership, guild, and class/workshop management for Past Lives Makerspace (Portland, OR). Two surfaces share one codebase: the **FOG hub** (member hub) and the **book CMS** (public class catalog + booking).

## Language

**Member**:
A person with a Past Lives Makerspace membership and a hub account. The canonical person-record (`membership.Member`).
_Avoid_: user (that's the Django auth record behind a Member), customer, account.

**Instructor**:
A Member who has been granted the **instructor role** by an admin. The role permits creating classes and gives the Member a public instructor page. Not a separate record — it is a facet of a Member (`is_instructor` ⇔ the member holds the instructor role / has an `instructor_slug`).
_Avoid_: instructor account, instructor profile (as a separate table/entity), teacher.

**Member bio**:
The short blurb a Member writes about themselves, shown in the member directory (`about_me`). Edited on member settings.
_Avoid_: about me (ambiguous — say which bio), profile bio.

**Instructor bio**:
A *separate* teaching-focused bio shown on the public instructor page, distinct from the Member bio. Edited on the instructor settings page, labeled "About me as an instructor". A Member who instructs maintains both bios independently.
_Avoid_: about me, instructor about.

**Onboarding**:
The home "Get started" checklist a new Member works through (`Member.onboarding` / `OnboardingChecklist`) — finish your profile, join a guild, etc. It is a checklist of **links**, not a questionnaire. (An older book-CMS 3-step question wizard that collected signup answers was retired; "onboarding" no longer refers to that.)
_Avoid_: onboarding wizard, onboarding questions, onboarding form.

**Class offering**:
One scheduled instance of a class (`classes.ClassOffering`) — a specific run with its own date(s), capacity, and slug. A class taught repeatedly produces many offerings ("runs") over time; each is a distinct offering with a distinct URL.
_Avoid_: using bare "class" for both the abstract class and a single dated run — a run is an offering.

**Contact**:
A labeled contact method on a Member — `{label, value}` (e.g. "Booking email" → an address) with per-surface placement toggles (show in the member directory and/or on the instructor page). One list per Member; absorbs the old fixed website/social/other-contact fields. `phone` and `discord` remain first-class fields, not Contacts.
_Avoid_: contact field, social link, other contact info.

**Community event**:
A dated happening on the Community Calendar (`membership.CommunityEvent`) — site-wide events, guild meetings, guild events, and studio-hours rows. Published events mirror one-way to downstream calendars (Google; Discord Scheduled Events when that ships). Not a class: a bookable class run is a **Class offering** on the book CMS.
_Avoid_: event (unqualified, when it could mean a class session or a notification-spine event key).

**Studio hours**:
A guild's ambient standing weekly hours — a special `CommunityEvent` type (`STUDIO_HOURS`). They render on in-app calendars/cards and the public Google calendar, but are never *announced* and never become Discord Scheduled Events (ambient hours are not happenings; scarce surfaces show happenings only).
_Avoid_: open hours, shop hours (as distinct concepts — they're all studio hours).

**Discord event mirror** vs **channel announcement**:
Two deliberately distinct Discord surfaces for the same community event: the *mirror* is its standing entry in the server's native Events UI (a Scheduled Event, one-way pushed like Google); the *announcement* is the one-shot "new event" embed posted to a channel when it publishes. Both appearing for one event is intended, not a duplicate bug. (A third surface — a weekly classes digest — is designed but paused pending marketing.)
_Avoid_: treating the pair as duplicates; "Discord event" unqualified.

**Guild** (hub):
A member-run interest group within the makerspace (woodshop, blacksmithing, etc.), with leads, staff, and a public page. The real `membership.Guild`.
_Avoid_: using bare "guild" for a class catalog category — that is a **Guild Type** (see below).

**Private guild**:
A Guild whose lead has turned `is_public` off in Guild Settings. It is hidden from the **public guilds site only** — dropped from the public directory, and its public URL answers a friendly "this page is private for now" note (HTTP 403). Inside the member hub nothing changes: members see the page exactly as before. Defaults to public, so no existing guild changed behaviour. (The flag existed in v20, was stripped in v22 as unused, and came back in v23 alongside the public guilds surface at `guilds.pastlives.space`.) To hide a guild *everywhere* — hub, public site, Discord — turn `is_active` off instead.
_Avoid_: "hidden guild" (it is not hidden from members); using `is_public` to gate anything on the member hub.

**Public guild URL**:
`guilds.pastlives.space/<public-slug>/` — the canonical, login-free home of a guild page, where `public-slug` is the guild's slug minus a trailing `-guild` (`woodworking-guild` → `/woodworking/`). Built from `Guild.public_slug` / `public_path` / `public_url`; never hand-assembled. Both the un-stripped `/woodworking-guild/` and the member-hub shape `/guilds/woodworking-guild/` 301 to it on that host. On the member hub the URL stays `/guilds/<slug>/`, but its `<link rel="canonical">` points at the public one.
_Avoid_: calling `/guilds/<slug>/` the public URL; adding a second view or template for the public page (it is the same view and template, differing only by auth state).

**Guild Type**:
The catalog category a class belongs to (the `classes.Category` model). User-facing copy calls it a "Guild Type" — not "category" or bare "Guild". A Guild Type may link to a hub Guild to route a submitted class's approval to that Guild's Lead, but a Guild Type (catalog category) and a Guild (member group) are distinct.
_Avoid_: category (in user-facing copy), class type, bare "guild".
