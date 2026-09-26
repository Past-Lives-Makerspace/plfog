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

**Member event** / **Public event**:
The only two words for who a dated happening on the Community Calendar is *for*, stored in `CommunityEvent.google_calendar_target` (`member` / `public`). Every member-facing surface badges that answer; the public is the default. **It says nothing about who can see the event.** Both Google calendars are open subscription feeds, so a member event is visible to anyone, posts to Discord and keeps an open event page exactly like a public one; a stranger finding one is how people discover the makerspace. The choice only picks which of the two calendars it lands on, and the members one is for guild meetings, council meetings, studio hours and the occasional members only meeting. Choosing it is a guild-staff and admin permission: the composer asks "Who is the audience?" only of them, and a plain member's event goes on the public calendar.
_Avoid_: community event, makerspace event, "which calendar"; and never call a member event private, hidden or restricted.

**Community event** (the model, not the words):
The record behind all of the above (`membership.CommunityEvent`) — guild meetings, guild events, makerspace-wide events, the Guild Lead Meeting, and studio-hours rows. The class keeps its name; nobody says it out loud. Its `event_type` is plumbing, not a label: it routes the launch announcement and scopes a guild's own meetings, and is never shown to a member (`community`, the general value, reads simply "Event" in the Django admin). Published events mirror one-way to downstream calendars (Google; Discord Scheduled Events). Not a class: a bookable class run is a **Class offering** on the book CMS.
_Avoid_: saying "community event" to a member — say member event or public event; event (unqualified, when it could mean a class session or a notification-spine event key).

**Studio hours**:
A guild's ambient standing weekly hours — a special `CommunityEvent` type (`STUDIO_HOURS`). They render on in-app calendars/cards and the public Google calendar, but are never *announced* and never become Discord Scheduled Events (ambient hours are not happenings; scarce surfaces show happenings only).
_Avoid_: open hours, shop hours (as distinct concepts — they're all studio hours).

**Discord event mirror** vs **channel announcement**:
Two deliberately distinct Discord surfaces for the same community event: the *mirror* is its standing entry in the server's native Events UI (a Scheduled Event, one-way pushed like Google); the *announcement* is the one-shot "new event" embed posted to a channel when it publishes. Both appearing for one event is intended, not a duplicate bug. (A third surface — a weekly classes digest — is designed but paused pending marketing.)
_Avoid_: treating the pair as duplicates; "Discord event" unqualified.

**Guild** (hub):
A member-run interest group within the makerspace (woodshop, blacksmithing, etc.), with leads, staff, and a public page. The real `membership.Guild`.
_Avoid_: using bare "guild" for a class catalog category — that is a **Guild Type** (see below).

**Private guild** (removed concept):
There is no such thing — every active Guild is visible on every surface (hub, public guilds site, Discord). The `is_public` flag was stripped in v22 as unused (0 of 15 guilds ever set it); "hide a guild" is `is_active` off, which removes it everywhere.
_Avoid_: private guild, hidden guild, gating anything on guild visibility.

**Guild Type**:
The catalog category a class belongs to (the `classes.Category` model). User-facing copy calls it a "Guild Type" — not "category" or bare "Guild". A Guild Type may link to a hub Guild to route a submitted class's approval to that Guild's Lead, but a Guild Type (catalog category) and a Guild (member group) are distinct.
_Avoid_: category (in user-facing copy), class type, bare "guild".
