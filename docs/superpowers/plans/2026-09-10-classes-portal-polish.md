# Classes portal polish — nine fixes, one PR

Date: 2026-09-10
Branch: `fog/classes-portal-polish`
Target VERSION: 1.52.0

Nine member-visible fixes across the instructor portal (`/classes/teach/`), the class
admin (`/classes/admin/`) and the member home. They are small individually; they ship
together because they overlap in the same four templates.

Three scope calls were settled with Jo before this spec was written and are locked:

- **§8** the Catalog Activity tab goes in the **admin** area, not the instructor area.
  The Activity section and the `/classes/admin/activity/` page already live there;
  the instructor portal has no activity feed and is not gaining one.
- **§6** the merged-and-collapsing treatment covers the **admin Overview and the
  instructor Overview**, not every card on every page.
- **§4b** the Instructor Profile tab gets **bio, photo and instructor links** editable
  in place, not just the bio.

---

## 1. Instructor portal title

`templates/classes/teach/base.html`

`Manage Classes & Workshops` becomes `Manage My Classes`, in both the `{% block title %}`
and the `<h1>`.

Note the `&` is a literal ampersand in the current title; the new title has none.

## 2. The Overview "+ New Class" button has no border

`templates/classes/teach/overview.html` line 11 passes `action_label="+ New class"` to
`components/page_header.html` and no `action_class`, so it falls through to the
component's default `hub-btn hub-btn--sm` — a plain (borderless) button.

The same button on the Classes tab
(`templates/classes/teach/classes_list.html:5`) is
`hub-btn hub-btn--primary hub-btn--sm` and reads `+ New Class`.

Make the Overview match the Classes tab exactly: pass
`action_class="hub-btn hub-btn--primary hub-btn--sm"` and change the label to
`+ New Class` (capital C).

`data-help-key="teach.create-class"` must stay on it — it is the instructor tour's
step-2 target, and `tests/core/tours_spec.py` cares.

## 3. Classes tab becomes My Classes

`templates/classes/teach/base.html:18` — the tab label `Classes` becomes `My Classes`.
The `active_tab` value stays `classes`; only the visible label changes.

`templates/classes/teach/registrations.html:98` already calls it the "My Classes" tab in
its empty state, so this makes the two agree.

The admin area's `Classes` tab (`templates/classes/admin/base.html:17`) is **not**
renamed.

## 4a. Registrations tab: "Email selected students" hands off to the Announcement Composer

`templates/classes/teach/registrations.html` currently carries a deprecated inline email
form: a collapsible `Email selected registrants` disclosure with its own subject/body
textareas, POSTing to `classes:teach_registrations_email` (which sends through
`TeachEmailForm`).

Every other class-email surface already routes to the composer:
`templates/classes/teach/class_registrations.html:14`,
`templates/classes/admin/class_registrations.html:13` and
`templates/classes/teach/class_detail_base.html:11` all link to
`{% url 'hub_compose' %}?audience=class:<pk>&lock=1`.

This tab must do the same, and additionally carry the checked rows through as the
composer's pre-selected recipients.

**What changes**

- The disclosure button, the subject/body/`bcc_self` fields and the `Send` button all go.
  In their place: one submit button labeled exactly **`Email selected students`**
  (styled `hub-btn hub-btn--sm hub-btn--primary`), inside the existing form, which keeps
  the existing `registration_ids` checkboxes and `{% csrf_token %}`.
- The form keeps POSTing to `classes:teach_registrations_email`. That view is rewritten:
  instead of sending an email, it validates the selection and **redirects** to the
  composer.

**The rewritten `classes.views.teach_registrations_email`**

Still `@teaching_member_required @require_POST`. Logic (thin view; the mapping belongs on
the model/manager per CLAUDE.md §2 — put the registration-to-recipient-token mapping on
`ClassOffering` or a `Registration` manager method, not in the view):

1. Read `registration_ids` from POST. Empty selection: `messages.error` with
   `"Tick the students you want to email first."` and redirect back to
   `classes:teach_registrations`.
2. Resolve those registrations, scoped to classes this `teaching_member` owns. Any id
   that is not theirs is simply not resolved (do not 404 on a crafted POST, but do not
   silently email a stranger either — the resulting set is the intersection). If the
   intersection is empty, same error path as (1).
3. All selected registrations must belong to **one** class. The composer scopes to a
   single `class_offering`, so a cross-class selection cannot be expressed. If the
   selection spans classes: `messages.error` with
   `"Pick students from one class at a time."` and redirect back.
4. Map each registration to the composer's recipient token, exactly as
   `hub.forms.announcement_recipient_choices` builds them for a class audience:
   - registration with a linked `member.user` → `user:<user.pk>`
   - otherwise → `custom:<lowercased registration.email>` (skip blank; dedupe)
5. Redirect to `hub_compose` with a querystring carrying
   `audience=class:<offering.pk>`, `lock=1`, one `recipients=<token>` per recipient, and
   `include_waitlist=1` **when any selected registration is `WAITLISTED`** — without it
   `announcement_recipient_choices` will not build a choice for that registrant and the
   pre-selection would silently drop them. Build the querystring with
   `django.http.QueryDict` / `urlencode(doseq=True)`, never string concatenation.

**`hub.views.hub_compose` must read the new GET params**

Today the fresh-compose branch only puts `audience` into `initial`. Add: when there is no
`draft_pk`, also read `recipients` (via `request.GET.getlist("recipients")`) and
`include_waitlist` into `initial`.

`AnnouncementComposeForm.__init__` already honours both:
`if not self.is_bound and "recipients" not in self.initial` is what leaves the whole
roster checked by default, so a present `initial["recipients"]` narrows the checked set;
and `_raw_include_waitlist()` reads `self.initial.get("include_waitlist")` on an unbound
form. No form change should be needed — verify this rather than assuming it.

Guard the recipient tokens: only pass through values that appear in the audience's own
choice list, so a hand-crafted `?recipients=user:1` cannot pre-check someone off the
roster. The send path re-validates server-side anyway
(`AnnouncementComposeForm._clean_recipients`), but the checklist should not display a
name that is not on the roster.

**Dead code**

`classes.forms.TeachEmailForm` stays: `classes.views.teach_class_email` still uses it.
That view is already unreferenced by any template, but removing it is out of scope for
this PR — do not touch it.

`classes/spec/views/teach_email_spec.py` tests the old sending behaviour of
`teach_registrations_email` and **will** need rewriting to the new redirect behaviour.
Rewrite those `it_*` cases; do not delete coverage.

**Help content**

`membership/help_content.py` around line 955 documents the old flow:

> 2. Click **Email selected students**.
> 3. Write a subject and message. Leave **Send me a copy** checked to get your own copy.
> 4. Click **Send**.

Rewrite those steps for the composer hand-off, in the ELI14 register the rest of the file
uses. Also fix the tab names in that guide: it says "three tabs: **Overview**,
**Classes**, and **Registrations**" — the portal now has more, and Classes is My Classes.
Keep every existing permission caveat ("You can only email people registered for your own
classes").

## 4b. Profile tab becomes Instructor Profile, editable in place

**Tab label.** `templates/classes/teach/base.html:21` — `Profile` becomes
`Instructor Profile`. `active_tab` stays `profile`.

**The tab itself.** `templates/classes/teach/profile.html` is currently a stub: a status
card for the public page plus a link out to `/settings/?tab=profile`. It gains a real
form covering the three things the public instructor page shows:

- **Photo** — `Member.profile_photo`. This is the *shared* member photo, the same one in
  the member directory. Label it so that is obvious; do not imply it is instructor-only.
- **Bio** — `Member.instructor_bio`.
- **Links** — the member's `MemberContact` rows with `show_on_instructor_page` set: add,
  edit, remove, reorder-free. `MemberContactForm` already exists in `hub/forms.py` and is
  already used as a formset on the settings page — mirror how
  `templates/hub/user_settings.html` and the settings view drive it rather than inventing
  a second pattern. A row created here defaults `show_on_instructor_page=True`.

Keep the existing "Your Public Instructor Page" status card above the form.

The settings page keeps its Instructor panel — this is a second door to the same fields,
not a move. The line on the settings page pointing at Contact Methods stays correct.

Validation lives in the form, not the view (CLAUDE.md §2). `ProfileForm` already handles
the "photo rejected but keep the text edits" case via `has_only_photo_errors` /
`save_keeping_existing_photo`; reuse that behaviour here rather than writing a new
photo-error path — an instructor who uploads a 9 MB photo must not lose their bio edit.

`teach_profile` becomes GET + POST. It stays thin.

## 5. Class admin title

`templates/classes/admin/base.html` — `Manage Class Catalog` becomes
`Manage All Classes`, in both `{% block title %}` and the `<h1>`.

## 6. One "Needs Attention" section, quiet when empty

**Admin Overview** (`templates/classes/admin/overview.html`). Sections ①, ② and ③ —
`Waiting on You`, `With Guild Leads`, `Interested in Teaching` — become **one**
`hub-card` titled `Needs Attention`, with the three as labeled groups inside it.

- The card's count is the sum of the three.
- A group with no rows renders **nothing at all** — no heading, no "Nothing waiting on
  you." line. Those three empty-state paragraphs are exactly the bulk the user is
  complaining about.
- When all three are empty the whole card collapses to a single quiet strip: one short
  line, muted, roughly the height of the existing Waitlists strip
  (`padding:0.75rem 1rem`), reading something like `Needs Attention · all clear`. Not a
  2.5rem-tall empty card.
- Row markup, the confirm modals, the `Remind lead` htmx form and the per-row actions all
  carry over unchanged. This is a re-grouping, not a rewrite of the rows.
- `id="teaching-applications"` and `scroll-margin-top` must survive — something links to
  that anchor. Grep before moving it.
- `data-help-key="admin.review-queue"` must survive on the "Waiting on You" heading (Info
  View / tour target).

**Instructor Overview** (`templates/classes/teach/overview.html`). Same treatment for its
analogues: `Waiting on your review`, `Awaiting Admin Validation` (guild-lead only) and
`Needs your attention`. One `Needs Attention` card, per-group headings only when the
group has rows, whole card collapses to the quiet strip when all are empty.

`data-help-key="guild.approve-classes"` and `data-help-key="teach.review-pipeline"` must
survive on their respective groups.

Note `Needs your attention` renders only inside the `{% if has_classes %}` branch while
the two guild-lead sections render outside it. Preserve that: a guild lead with no
classes of their own still sees their review queue.

**Sitewide framing.** The literal cards live only on these two pages; "sitewide" in the
request means the treatment, not a third page. Do not go hunting for other cards to
collapse.

**Prefer one shared partial** over two hand-rolled copies if the two pages' rows are
close enough to share — but do not contort the markup to force sharing. Two clear
templates beat one clever one.

## 7. At a Glance moves above Upcoming Classes This Week

`templates/classes/admin/overview.html` — section ⑥ (`At a Glance`, `id="at-a-glance"`,
which also nests Recent Sign-Ups and owns the range filter) moves **above** section ④
(`Upcoming Classes This Week`).

Resulting order: Needs Attention (merged §6) → At a Glance → Upcoming Classes This Week →
Waitlists. (Activity leaves the page entirely — §8.)

The range links are `?range=<key>#at-a-glance`; the anchor still resolves after the move,
but confirm the `scroll-margin-top` still reads sensibly near the top of the page.

## 8. Catalog Activity becomes its own admin tab

`templates/classes/admin/base.html:14` currently groups activity under Overview:
`{% with overview_tabs="overview activity" %}` and the Overview tab lights for both.

- Split `activity` out: Overview lights only for `overview`, and a new tab
  `Catalog Activity` links to `classes:admin_activity` and lights for `activity`.
- Place it after `Registrations`, before `Settings`.
- It sits inside the `{% if request.view_as.actual_is_admin %}` guard — the activity feed
  spans the whole catalog, so it is admin-only like Overview and Classes. The existing
  view's own permission decorator is the real gate; the tab must not appear to a
  non-admin who can reach Registrations.
- `templates/classes/admin/activity.html:4` — the `<h2>Activity</h2>` becomes
  `Catalog Activity`. Its lead paragraph references "the Overview and follow 'View all
  registrations'" — that still resolves, but re-read it after §6/§7 land and fix it if it
  now describes something that moved.
- Remove section ⑦ (`Activity`) from `templates/classes/admin/overview.html` entirely,
  including its `View full log →` link, since the tab replaces it.
- The view supplies `active_tab`; make sure `admin_activity` sets `active_tab="activity"`
  (it likely already does — check) so the new tab lights.

## 9. Home: "Your Upcoming" becomes "Upcoming at Past Lives"

`templates/hub/home.html:91` — `<h2 class="pl-home-heading">Your Upcoming</h2>` becomes
`Upcoming at Past Lives`. The `{# --- Your upcoming --- #}` comment above it should be
updated to match.

---

## Test blast radius

Specs that assert on strings this PR changes — every one of these must be updated, not
deleted:

- `classes/spec/views/admin_lifecycle_actions_spec.py` — asserts `"Waiting on You"`,
  `"With Guild Leads"` and `"Nothing with guild leads."` (the last of those strings stops
  existing under §6; replace the assertion with one about the new empty behaviour).
- `classes/spec/views/admin_teaching_applications_spec.py` — asserts
  `"Interested in Teaching"`.
- `classes/spec/views/teach_overview_spec.py` — asserts
  `"Classes in your guild waiting on your review"`.
- `classes/spec/views/admin_class_email_spec.py` — asserts `b"Email selected students"`
  on the **admin** class page; check whether §4a's wording change touches it.
- `classes/spec/views/teach_email_spec.py` — the whole file tests the old send behaviour
  of `teach_registrations_email`. Rewrite to the redirect contract.
- `tests/hub/teach_sidebar_spec.py`, `tests/hub/admin_tools_spec.py` — assert
  `"Manage Classes"`, which is the Admin Tools **card** label, not the page `<h1>`. §1
  and §5 should not touch those. Confirm.
- `tests/core/tours_spec.py` — asserts the compose URL shape
  `?audience=class%3A{pk}&lock=1` and the `teach.create-class` target.
- `tests/hub/announcement_compose_spec.py` — the composer's audience/lock contract. §4a
  extends it; add cases for the new `recipients` / `include_waitlist` GET params there.

**New coverage required** (100% branch coverage, and the mutation gate is real):

- the empty-selection, cross-class, and not-your-registration paths of the rewritten
  `teach_registrations_email`
- the waitlisted-registrant → `include_waitlist=1` case
- the guest-checkout registrant → `custom:<addr>` token case
- `hub_compose` honouring `?recipients=` and rejecting an off-roster token
- the Instructor Profile tab's GET, successful POST, and rejected-photo POST
- the merged Needs Attention card: all-empty collapse, one-group-populated, all-populated

## e2e lane

`pytest -m e2e` is a **separate CI job** that a normal `pytest <paths>` run deselects.
This PR renames tabs and moves sections, which is exactly what breaks Playwright specs.

Before pushing, grep `tests/e2e/` for every string this PR changes and run any spec that
navigates the instructor portal, the class admin, or the member home:

```
grep -rn "Manage Class\|Email selected\|Waiting on\|Interested in Teaching\|Your Upcoming\|Activity" tests/e2e/
```

Known relevant: `tests/e2e/screenshots_spec.py` and `tests/e2e/help_screenshots_spec.py`
walk `/classes/teach/` and `/classes/admin/` and capture help screenshots by selector —
§4b changes the Profile tab's DOM and §6/§7/§8 change the admin Overview's, so the
`ShotSpec` drift guard in `docs/HELP_AUTHORING.md` may fire. Run them.

Run e2e the CI way, on Postgres, not local SQLite:

```
DATABASE_URL="postgres://plfog:plfog@localhost:5433/plfog" .venv/bin/pytest tests/e2e/<file> -m e2e --no-cov -o addopts="" -q
```

## Out of scope

- `classes.views.teach_class_email` and `classes.forms.TeachEmailForm` (orphaned but
  untouched).
- The admin Registrations tab's own email affordances.
- Renaming the admin area's `Classes` tab.
- Any change to what the public instructor page renders.
