# Video link on community events

**Status:** ready to build
**Size:** small (one new field on one existing model + display/sync plumbing)
**Slug:** event-video-link

## Why

Some Past Lives events run as online (Google Meet) or hybrid meetings. Today an event only
has a free-text `location`, so a host who wants members to join a video call has to bury a
URL in the location or description with no clear "click here to join" affordance, and the
link never travels cleanly to Google Calendar or Discord.

Google Meet's premium features (recording, transcripts, Gemini "take notes for me", longer
call length) attach to **whoever hosts the call**, not to the link or to whatever account
created the calendar entry. So the correct, robust design is a **paste-your-own-link**
field: the host creates the Meet link under their own Google account (which carries their
premium features), pastes it into the event, and FOG surfaces it everywhere members look.
No service-account delegation is involved (and it is impossible for a personal Google
account anyway).

## What we're building

A single new optional field, `CommunityEvent.video_url`, surfaced end-to-end:

1. **Model** — new `video_url` URLField, optional, with a migration.
2. **Form** — a "Video link" field in the create/edit/propose event form.
3. **Event detail page** — a clear "Join online" call-to-action when a link is set.
4. **Community calendar** — an "online" affordance on the event so members can reach the
   link from the calendar too.
5. **Google Calendar push** — the link travels into the pushed Google event (reliably, in
   the description) so the members'/public Google calendars carry it.
6. **Discord Scheduled Event** — the link appears in the Discord event so it "shows up on
   Discord".
7. **`.ics` export** — the link is in the exported VEVENT so "Add to calendar" carries it.

## The field

In `membership/models.py`, on `CommunityEvent` (add it right after `location`, ~line 4262):

```python
video_url = models.URLField(
    max_length=500,
    blank=True,
    default="",
    help_text=(
        "Optional link members click to join this event online (e.g. a Google Meet, Zoom, "
        "or Jitsi URL). Paste a link you created under your own account so the meeting "
        "carries your own video-call features."
    ),
)
```

- `URLField` gives us URL validation for free (a bad value fails form validation loudly —
  matches the fail-loudly standard; no silent acceptance of junk).
- `blank=True, default=""` — optional, non-null empty string (never `None`), consistent
  with the existing `location`/`description` string fields on this model.
- One migration for the added column. No data backfill (defaults to `""`).

## Form (`hub/forms.py` — `CommunityEventForm`, ~line 1569)

- Add `"video_url"` to `Meta.fields`, positioned **immediately after `"location"`** so the
  physical-location and online-link inputs sit together.
- Give it a friendly label and hint. In `__init__`, set:
  ```python
  self.fields["video_url"].label = "Video link"
  ```
  The model `help_text` already carries the guidance; if the form's rendering shows
  help_text, that's enough. Keep the "for online / hybrid meetings" framing.
- `URLField` on the model yields a `forms.URLField` — it is **not required** (blank=True),
  so nothing else in `clean()` needs to change. Do not add it to `StudioHoursForm`
  (standing studio hours are not video meetings).
- All three surfaces of this one form (`as_admin`, lead default, `as_member` proposal)
  pick the field up automatically since they share `Meta.fields`.

## Event detail page (`templates/hub/event_detail.html`)

Location currently renders as an icon + `{{ event.location }}` block (~line 32), and the
action row (~line 50) has an "Add to calendar" primary button plus ghost buttons.

- Add a **"Join online"** call-to-action, shown only when `event.video_url` is set. Make it
  the primary join affordance. Use the existing button classes already in this template
  (`hub-btn hub-btn--primary`) — match the surrounding markup; do not invent new component
  classes. Link opens the URL in a new tab:
  ```html
  {% if event.video_url %}
    <a class="hub-btn hub-btn--primary" href="{{ event.video_url }}" target="_blank" rel="noopener noreferrer">Join online</a>
  {% endif %}
  ```
- Since "Join online" becomes the primary action, demote "Add to calendar" to a ghost
  button when a video link is present (one primary button per row reads best). Use your
  judgement to keep the row visually balanced per FRONTEND.md; if unsure, keep both but
  ensure only one `--primary`.
- Follow **FRONTEND.md** for spacing/dark-mode. Read it before touching the template.

## Community calendar (`templates/hub/community_calendar.html` /
`templates/hub/partials/calendar_event_item.html`)

Events reach the calendar as `CalendarEntry` dataclass instances
(`hub/calendar_entries.py`, `@dataclass CalendarEntry`, ~line 37 — fields include
`url, location, description, guild`). The builders that turn a `CommunityEvent` into a
`CalendarEntry` are in `hub/calendar_entries.py` (~lines 151, 200) and `hub/home.py`
(~lines 119, 138).

- Add a `video_url: str = ""` field to `CalendarEntry`.
- Populate it from `event.video_url` in every builder that constructs a `CalendarEntry`
  (and the home-feed entry) from a `CommunityEvent`. Feed / class / orientation entries
  have no video link — leave their `video_url` at the `""` default.
- In the calendar item partial, when `entry.video_url` is set, show a small **"Join
  online"** link/indicator (a text link is fine — this is a dense list, not a detail page).
  Match the partial's existing markup and FRONTEND.md; do not over-build.

## Google Calendar push (`core/integrations/google_calendar.py` — `_build_event_body`)

Reliable path only. In `_build_event_body` (~line 161), when `event.video_url` is set,
prepend a clear join line to the description so the pushed Google event carries a clickable
link on both the member and public calendars:

```python
if event.video_url:
    description = f"Join online: {event.video_url}\n\n{description}"
```

(Place it so the final description reads: `Join online: <url>` / blank line / event
description / blank line / `Added by <who> via FOG`.)

**Explicitly out of scope: `conferenceData` / a native "Join with Google Meet" button.**
That requires either a `createRequest` (only a Meet-licensed *organizer* can mint one — the
service account cannot) or attaching an externally-created link via a conference add-on,
which is fragile from a service-account context and risks 400-ing the whole push. The
description link is clickable in Google Calendar and never breaks. This matches the "essential,
not minimal; fail loudly; don't gold-plate" posture. Do **not** change the `insert_event` /
`update_event` signatures or add `conferenceDataVersion`.

## Discord Scheduled Event (`core/integrations/discord_events.py`)

`_build_description` (~line 193) builds `event.description` + a blank line + `public_url`.
`_build_scheduled_event_body` (~line 256) sets `entity_metadata.location` from
`event.location or DEFAULT_LOCATION`.

- In `_build_description`, when `event.video_url` is set, include a `Join online:
  <video_url>` line (place it first so it's visible above the fold; keep the existing
  description + public_url). Respect the existing `[:_DESCRIPTION_MAX]` clamp applied by the
  caller.
- In `_build_scheduled_event_body`, when the event has a `video_url` **and no physical
  `location`**, use the video URL as the `entity_metadata.location` (clamped to
  `_LOCATION_MAX`) instead of `DEFAULT_LOCATION` — a purely-online event should not read as
  happening at the makerspace. When a physical location IS set, keep it as the location (the
  join link still shows in the description). Keep the 1–100 char requirement satisfied.

## `.ics` export (`membership/ical.py`)

The event `.ics` (the "Add to calendar" link, `hub_event_ics`) builds a VEVENT. When
`video_url` is set, include it so downstream calendar apps get the link — add a `URL`
property (and/or fold `Join online: <url>` into the VEVENT `DESCRIPTION`, matching however
this module already emits `DESCRIPTION`/`LOCATION`). Read `membership/ical.py` to match its
existing escaping (`ical_escape`) and property-emission style; do not hand-roll new escaping.

## Out of scope (keep it small)

- The one-shot **launch announcement embed** (the `emit()` → Discord channel post / in-app /
  email) is **not** modified in this pass — it flows through the shared notification
  templating system, and widening that is more than this small feature warrants. The link
  still reaches Discord via the **Scheduled Event** above. (If, and only if, adding the join
  link to the announcement is a trivial, localized one-line change in an event-copy template
  the builder is already confident about, it may include it — otherwise leave it.)
- No `conferenceData` (see Google section).
- No new model beyond the one field; no changes to the moderation / sync lifecycle.

## Testing (BDD `*_spec.py`, 100% coverage)

Add/extend specs — `describe_*` blocks, `it_*` tests, factory-boy data (never `context_*`,
which is not collected):

- **Model / form:** a valid URL saves; a non-URL value fails form validation; blank is
  allowed; the field is absent from `StudioHoursForm`.
- **Google push** (`tests/core/integrations/google_calendar_spec.py`): the pushed body's
  `description` contains `Join online: <url>` when `video_url` is set, and does **not** when
  it is blank.
- **Discord push** (the discord_events spec): `_build_description` includes the join line
  when set; `_build_scheduled_event_body` uses the video URL as the location when there is
  no physical location, and keeps the physical location (with the link still in the
  description) when there is one.
- **Calendar entry:** a `CommunityEvent` with a `video_url` yields a `CalendarEntry` whose
  `video_url` is populated; a feed/class entry's `video_url` stays `""`.
- **`.ics`:** the exported VEVENT carries the link when set.
- **Template:** render event_detail with and without `video_url`; assert the "Join online"
  link appears only when set and points at the URL (an existing view/template spec pattern
  in the repo should be mirrored).

## UI/UX completeness check

- **Input:** "Video link" field present on create, edit, and propose forms (shared
  `Meta.fields`). Optional — blank is valid, no forced value. Bad URL ⇒ inline validation
  error (URLField), never a silent accept.
- **Empty state:** no `video_url` ⇒ no "Join online" button/link anywhere (detail page,
  calendar item, Google/Discord/ics simply omit it). No empty label, no dead link.
- **Populated state:** "Join online" primary CTA on the event detail page; a "Join online"
  link on the calendar item; join line in the Google event description; join line in the
  Discord Scheduled Event (and as the Discord location for online-only events); `URL` in the
  `.ics`.
- **Edit round-trip:** editing an event preserves/updates `video_url`; clearing the field
  removes the link from every surface on the next save/push.
- **Dark mode:** the new button/link inherits existing `hub-btn`/link styling — verify it
  reads correctly in both themes per FRONTEND.md (no hard-coded colors).

## Versioning & changelog

- Bump `VERSION` in `plfog/version.py` to **`1.1.0`** (net-new member-facing feature on top
  of the shipped `1.0.0`). If a concurrent branch already took `1.1.0`, coordinate at merge
  (a known worktree-collision hazard) — pick the next open minor.
- Add ONE new member-facing `CHANGELOG` entry stamped at the new `VERSION`, plain language,
  e.g. title **"Join events online with a video link"**:
  > Events can now include a video link. When an organizer adds one, you'll see a "Join
  > online" button on the event so you can hop into the meeting in one click. The link rides
  > along to Google Calendar and Discord too.
  Keep it to one entry whose `version == VERSION` so `announce_release` resolves.
