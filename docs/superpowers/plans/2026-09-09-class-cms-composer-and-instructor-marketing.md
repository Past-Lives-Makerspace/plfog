# Class CMS: The Composer, Hero Placement, and the Instructor Marketing Page

**Status:** design spec, ready to plan
**Branch:** `fog/class-cms-round`
**Surfaces:**
`templates/classes/teach/class_form.html`, `templates/classes/admin/class_form.html`,
`templates/classes/teach/orientation.html`, `templates/classes/teach/overview.html`,
`templates/classes/teach/class_overview.html`, `templates/classes/teach/class_detail_base.html`,
`templates/classes/public/_list_results.html`, `templates/classes/_components/*`,
`hub/context_processors.py`, `static/css/hub.css`, `static/css/components.css`

---

## 0. Summary

Three changes, one round.

**A.** `/classes/teach/` stops being a locked door. The **Teaching** sidebar entry becomes
visible to every active member, and a member who cannot yet teach lands on a marketing page
that shows what instructors get, links to a real example class page they can open, and carries
one **Apply to Teach** action. Teaching access stops being self service: a member applies, an
admin approves. The acknowledge and unlock form is retired.

**B.** The catalog card and the detail hero are two different shapes, so the instructor makes
**two** placement decisions, not one. The 16:9 Cropper.js box keeps owning the detail banner. A
new focal point tool owns the catalog card, sitting directly beside a live, pixel accurate card
preview built from the real catalog markup. The detail page preview moves inside the editor as
an embedded frame instead of only a new tab link.

**C.** The 3,090px single form becomes a five step composer built exactly like the announcement
composer: one page, one `<form>`, one Alpine `x-data` holding `phase`, `x-show="phase === N"`
blocks, and a `.pl-phase-tabs` bar. The six sale fields leave the form entirely and become a
modal opened from the Manage Class action row, with a pill on the manage page when a sale is
live.

The step indicator and the review pipeline stay **two separate components**. Merging them would
claim that finishing step 5 means approved, which is false. §C.4 says exactly how they sit
together.

---

## 1. What already exists (reuse, do not reinvent)

| Thing | Where | How this spec uses it |
|---|---|---|
| Alpine phase stepper | `templates/hub/announcement_compose.html` | The composer copies its shape verbatim |
| `.pl-phase-tabs` / `.pl-phase-tab` / `--active` | `hub.css:4899-4917` | The composer's step bar, plus two new modifiers |
| `.pl-compose-section` / `__title` / `__note` | `hub.css:4864-4881` | Sub headings inside a step (gold, `text-transform: capitalize`, Rule 22) |
| `.pl-wizard-lead`, `.pl-wizard-actions`, `.pl-wizard-errors` | `hub.css:4766-4830` | Composer lead paragraph, action bar base, error block |
| `.pl-wizard-preview` / `__bar` / `__pane` / `__iframe` (`height:520px`) | `hub.css:4832-4856` | The embedded detail page preview on step 5 |
| `ClassOffering.readiness()` → 5 `ReadinessItem`s | `classes/models.py:537,548,1872` | Step completion marks and the step 5 checklist |
| `ClassOffering.review_pipeline()` → `ReviewPipeline` | `classes/models.py:608,1924` | Approval progress, kept separate from step progress |
| `classes/_components/readiness_list.html` (`link_hints`, `grid`) | as is | Step 5 checklist, `grid=True` |
| `classes/_components/review_pipeline.html` | as is | Pipeline card at the top of the composer |
| `.pl-pipeline*` incl. the sub 480px vertical stack + `--pl-pipeline-fill` | `hub.css:8117-8235` | Unchanged |
| `.pl-readiness*`, `.pl-readiness--grid` (720px breakpoint) | `hub.css:8260-8318` | Unchanged |
| `.pl-lifecycle-badge` pill recipe | `hub.css:8075` | The sale pill composes onto it |
| `components/modal.html`, `components/confirm_modal.html` (incl. `confirm_js` mode) | as is | Sale modal, apply modal, submit confirm |
| `components/form_field.html`, `components/toggle.html`, `.pl-help` | as is | Every field, every tooltip |
| `classes/_components/hero_image_field.html` + `static/js/hero_cropper.js` | as is | Detail banner crop, unchanged |
| `static/js/hero_placement.js` (`heroPlacement` Alpine component) | as is | The interaction model the card focus tool copies |
| `classes/_components/session_calendar.html`, `image_formset.html`, `faq_formset.html`, `scheduling_type_field.html`, `collapsible_field.html`, `class_qr_share.html` | as is | Moved between steps, not rewritten |
| `.pl-guild-hero` family + `.pl-help-hero` as the "compose a variant" precedent | `hub.css:3417-3425`, `3488` | The marketing hero |
| `.pl-tools-grid` / `.pl-tool-card` icon + title + body grid | `hub.css:5019-5055`, `templates/hub/admin_tools.html` | The shape the new feature grid copies |
| `.pl-profile-preview__sticky` live preview aside | `hub.css:3049`, `templates/hub/user_settings.html:235` | The shape the card preview copies |
| `.cp-page` token block (self contained, sources from hub tokens) | `cms-public.css:341` | Lets the real catalog card render inside a hub page |
| `Member.grant_teaching(granted_by=)` / `revoke_teaching` | `membership/models.py:1240,1260` | The approve action; already idempotent, already logs |
| `.pl-orientation*` card set | `hub.css:6483-6541` | The applied and approved state cards |

---

# A. The Instructor Marketing Page

## A.1 Routes and gates

Three changes, all small.

**1. The sidebar entry becomes universal.** `hub/context_processors.py:57` `_teach_nav()` currently
returns `None` unless `member.can_create_classes`. Drop that clause. The new condition is
`member is not None and member.status == Member.Status.ACTIVE`. The label, url, and `is_active`
logic are unchanged, so `templates/hub/base.html:124` and `:263` need no edit at all. The
adjacent Class Catalog de-activation keeps working.

**2. `teach_overview` stops being gated and starts branching.** `classes:teach_overview`
(`teach/`) drops `@teaching_member_required` and gains `@login_required` plus its own active
member check (same 403 body as today: `An active member account is required to access the
teaching portal.`). Then:

```
member.can_create_classes            -> today's teach/overview.html, unchanged
otherwise                            -> the marketing page
```

Every other `teach_*` view keeps `@teaching_member_required`. Its redirect target changes from
`classes:teach_orientation` to `classes:teach_overview`, so a locked member who deep links to
`teach/classes/new/` lands on the marketing page instead of a dead orientation form.

**3. The marketing page also gets a permanent route of its own.**
`classes:teach_why` at `teach/why/`, reachable by any logged in member including instructors, so
admins can link it and an approved member can still read it. `classes:teach_orientation` and
`classes:teach_orientation_complete` are **retired**: `teach_orientation` becomes a permanent
redirect to `classes:teach_why`, and `teach_orientation_complete` is deleted along with
`InstructorOrientationCompleteForm` (`classes/forms.py:439`) and
`templates/classes/teach/orientation.html`. Nothing else references them.

`Member.complete_instructor_orientation()` stays on the model (account deletion and the
grandfathering migration reference the field), but loses its only caller. Leave the method; it is
the admin-side grant's sibling and deleting it churns tests for nothing.

**Template:** `templates/classes/teach/why_teach.html`, extending `hub/base.html` directly (not
`classes/teach/base.html`, whose tab bar links five gated pages; showing locked tabs here would be
a wall of dead ends, exactly the reason the orientation page did the same).

## A.2 Application state

No new model. Four fields on `Member`, one migration, mirroring how `instructor_oriented_at`
already carries the whole teaching record.

```python
teaching_applied_at = models.DateTimeField(
    null=True, blank=True,
    help_text="When the member applied to teach. Null means they have never applied.",
)
teaching_application_note = models.TextField(
    blank=True, default="",
    help_text="What the member said they want to teach, in their own words.",
)
teaching_decided_at = models.DateTimeField(
    null=True, blank=True,
    help_text="When an admin approved or declined the teaching application.",
)
teaching_decline_reason = models.CharField(
    max_length=300, blank=True, default="",
    help_text="Why the application was declined. Shown to the member. Blank when approved.",
)
```

Derived state, one property, the single source of truth the template reads:

```python
class TeachingApplicationState(models.TextChoices):
    NONE = "none", "Not applied"
    PENDING = "pending", "Waiting on an admin"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined"

@property
def teaching_application_state(self) -> TeachingApplicationState: ...
```

Resolution order: `can_create_classes` → APPROVED (an admin grant with no application still reads
approved, which is correct and covers every grandfathered instructor); `teaching_decline_reason`
non blank and `teaching_decided_at` set → DECLINED; `teaching_applied_at` set → PENDING;
otherwise NONE.

Two model methods, both fat model, both idempotent, both logging `SiteActivity` the way
`grant_teaching` already does:

- `apply_to_teach(note: str) -> None` — raises `ValueError` when the member is not ACTIVE (same
  shape as `complete_instructor_orientation`), raises `ValueError` on a blank note, refuses to
  overwrite a PENDING application, clears any previous decline so a re-application is clean, sets
  `teaching_applied_at = timezone.now()`, logs `SiteActivity.Kind.TEACHING_APPLIED`, emits
  `instructor_application_received`.
- `decline_teaching(*, decided_by: Member | None, reason: str) -> None` — requires a non blank
  reason (`ValueError`), sets `teaching_decided_at` and `teaching_decline_reason`, logs
  `TEACHING_APPLICATION_DECLINED`, emits `instructor_application_declined`.

`grant_teaching()` gains three lines: stamp `teaching_decided_at`, clear
`teaching_decline_reason`, emit `instructor_application_approved` **only when
`teaching_applied_at` is set** (so an admin granting access to someone who never applied does not
send a "your application was approved" email). It stays idempotent.

**Emails.** Three new spine events, all following the FRONTEND.md email rules (branded shell,
absolute URLs, one clear CTA, `.txt` and `.html` in sync):

| Event key | To | Subject | Primary CTA |
|---|---|---|---|
| `instructor_application_received` | `AdminCapability.Capability.CLASS_APPROVER` holders | `New teaching application from {name}` | Teaching Applications queue |
| `instructor_application_approved` | the member | `You can now teach at Past Lives` | Create Your First Class |
| `instructor_application_declined` | the member | `About your teaching application` | Read the Instructor Guide |

The subject noun in each is a link: the applicant's member page, the teaching portal, the guide.

## A.3 Page layout

One column, `max-width: 980px`, centered. Sections in this order. Each section is a `hub-card`
unless noted.

```
┌─ status banner (only in PENDING / DECLINED / APPROVED) ───────────┐
├─ hero band (.pl-guild-hero .pl-teach-hero, full bleed inside col) ┤
├─ "What You Get" ......... 7 feature cards, auto fill grid         ┤
├─ "See It For Yourself" .. copy left, a REAL catalog card right    ┤
├─ "How It Works" ......... 3 numbered steps                        ┤
├─ "What We Ask Of You" ... 4 bullets                               ┤
├─ "Common Questions" ..... 4 <details> rows                        ┤
├─ "Read the Instructor Guide" ... collapsed help article           ┤
└─ apply card ............. the CTA again, state aware              ┘
```

**Status banner.** Rendered first, above the hero, so state is the first thing on screen. It is a
`hub-card` carrying `.pl-orientation-banner` (which supplies only a 4px gold left border and
assumes a `hub-card` parent, `hub.css:6485`) in PENDING and DECLINED, and the existing
`.pl-orientation-done` block verbatim in APPROVED. Omitted entirely in NONE.

**Hero band.** `<div class="pl-guild-hero pl-guild-hero--noimg pl-teach-hero">` with
`.pl-guild-hero__content`, `.pl-guild-hero__title`, `.pl-guild-hero__lead`, then a
`.pl-teach-hero__actions` row of two buttons. No photo: `--noimg` paints the brand gradient
(`--hub-hero-from/mid/to`), which is deliberate. A stock photo here would be a lie about the
space, and every real photo on this page belongs to the example class card below it.

`.pl-teach-hero` is a variant class over `.pl-guild-hero`, exactly as `.pl-help-hero`
(`hub.css:3488`) already composes over it. It sets `align-items: center`, `text-align: center`,
`min-height: clamp(200px, 26vw, 300px)`, and nothing else.

Buttons are `pl-btn` not `hub-btn`: `pl-btn` has a `:disabled` gap but is the anchor safe family
and dominates `templates/hub/partials/`, and the hero renders anchors.

- Primary: `pl-btn pl-btn--primary` **Apply to Teach** (NONE only) → `@click="$dispatch('open-modal', 'apply-to-teach')"`.
- Secondary: `pl-btn pl-btn--secondary` **See an Example Class Page** → the example class (§A.6), `target="_blank" rel="noopener"`.

In PENDING the primary is replaced by a non interactive `<span class="pl-lifecycle-badge pl-lifecycle-badge--awaiting_admin">Application sent</span>` and the example link becomes primary. In APPROVED the primary becomes **Go to the Teaching Portal**. In DECLINED the primary is **Apply Again**.

**Feature grid.** `<div class="pl-feature-grid">` of seven `<div class="hub-card pl-feature-card">`,
each `__icon` (a 22px inline SVG from the set already used in `templates/hub/admin_tools.html`),
`__title`, `__desc`. The card composes `hub-card` + `pl-feature-card`, which is exactly how
`pl-tool-card` works (`pl-tool-card` supplies no background or border of its own). Difference from
`pl-tool-card`: these are `<div>`s, not links, so the hover lift and `translateY` are dropped.

**"See It For Yourself".** A two column `.pl-teach-showcase` (`grid-template-columns: 1fr 1fr`,
stacking below 800px, matching the `.pl-edit-split` breakpoint). Left: heading, two short
paragraphs, a `pl-btn pl-btn--primary` link. Right: **a real, live catalog card** for the example
class.

The catalog card is not rendered from an offering. `_list_results.html` loops
`{% for group in page_obj %}{% with offering=group.representative %}` and the card body reads
`group.is_multi`, `group.members`, and `group.date_count` to build the "Pick a date:" block. So the
card partial takes a **group**, not an offering.

That is not a problem here, because the group is real, not fabricated. `_CatalogGroup(offering)` in
`classes/views.py:134` is a one argument constructor that yields a correct single member group
(`members == [representative]`, `date_count == 1`, `is_multi == False`). **Move `_CatalogGroup` and
`_grouped_catalog` out of `classes/views.py` and into `classes/grouping.py` as public
`CatalogGroup` and `grouped_catalog`** — that module exists for exactly this concern and currently
holds only the key helper. `classes/views.py` imports them; no behaviour changes.

The view then does:

```python
context["example_group"] = CatalogGroup(example) if example is not None else None
```

and the template renders

```django
<div class="cp-page"><div class="cls-grid pl-teach-showcase__grid">
  {% include "classes/public/_class_card.html" with group=example_group %}
</div></div>
```

with `cms-public.css` linked at the top of the template. Not a screenshot, not a mock: the same
partial the catalog renders, through the same real grouping class, with the same CSS. The example
class is genuinely a solo class, so the single member group is genuinely what the catalog would
build for it. Clicking the card opens the real class detail page.

`.pl-teach-showcase__grid` pins the grid to `grid-template-columns: minmax(0, 320px)` so the card
does not stretch to the full pane.

**"How It Works".** `<ol class="pl-teach-steps">` of three `.pl-teach-steps__item`, each with a
`__n` circle (the same 1.35rem gold circle recipe as `.pl-wizard-nav__n`, `hub.css:4796`), a
`__title`, and a `__desc`. Deliberately **not** `.pl-pipeline` markup: see §D.3.

**"What We Ask Of You".** A plain `<ul>` of four bullets in `.hub-text-muted`. No card chrome
beyond the section's `hub-card`.

**"Common Questions".** Four `<details class="pl-teach-faq__item">` inside
`<div class="pl-teach-faq">`, the same disclosure pattern the public class page already uses
(`.cp-detail__faq-item`), restyled for the hub with two rules.

**"Read the Instructor Guide".** One `<details>` wrapping
`<div class="pl-md pl-md--help">{{ article.body|page_content }}</div>` plus, when `guide` resolves,
the existing `.pl-orientation-guide-link` paragraph. This is where the retired orientation page's
content goes, so nothing written for the Help Center is lost. Same view context keys (`article`,
`guide`) as `teach_orientation` supplies today, and the same fallback line when the seed has not
run.

**Apply card.** Last. `hub-card` with `.pl-teach-apply`. Repeats the CTA and carries the state
detail, because a member who read the whole page should not have to scroll back up.

## A.4 The apply modal

`components/modal.html`, `modal_id="apply-to-teach"`, `modal_size="sm"`,
`modal_title="Apply to Teach"`, `modal_body_include="classes/teach/partials/apply_form.html"`.
Server rendered inline, not fetched by HTMX, so a POST that fails validation re-renders the page
with the bound form and the error inside the modal. The reopen is the established two line
`x-init` from `class_overview.html:8`:

```django
<div x-data{% if apply_form.is_bound %} x-init="$nextTick(() => $dispatch('open-modal', 'apply-to-teach'))"{% endif %}>
```

The body is one required textarea (`TeachingApplicationForm.note`, `max_length=2000`) rendered
through `components/form_field.html` and therefore wrapped in `.pl-form-group`, which styles the
textarea from the theme input tokens (Rule 13). Then a `.pl-modal__actions` row: **Send My
Application** (`pl-btn pl-btn--primary`) and **Cancel** (`pl-btn pl-btn--secondary`).

POST target `classes:teach_apply` (`teach/apply/`), `@login_required` + `require_POST`, active
member only. Success: `member.apply_to_teach(note=...)`, Django message (full page POST, so a
message and not a toast, Rule 6), redirect to `classes:teach_overview`.

## A.5 Every user facing string

Headings are Title Case (Rule 22). No em dashes or standard dashes anywhere below.

### Hero

| Slot | String |
|---|---|
| `__title` | `Teach at Past Lives` |
| `__lead` | `Share what you know with the people already in the shop. You get a full class page, a roster, and the tools to run it.` |
| Primary (NONE) | `Apply to Teach` |
| Primary (PENDING) | `Application sent` (a badge, not a button) |
| Primary (APPROVED) | `Go to the Teaching Portal` |
| Primary (DECLINED) | `Apply Again` |
| Secondary | `See an Example Class Page` |

### "What You Get"

Section heading: `What You Get`

| # | `__title` | `__desc` |
|---|---|---|
| 1 | `A Class Page Worth Sharing` | `Your class gets its own page with a wide banner photo, a photo gallery, your schedule, your bio, and a booking panel that follows the reader down the page.` |
| 2 | `Copy In Your Own Voice` | `Write the description, the prerequisites, what is included, what to bring, safety notes, and an age note. Fill in what fits your class and leave the rest blank.` |
| 3 | `Photos And A Banner` | `Upload a banner photo and as many gallery shots as you like. You pick which part of the photo shows on the banner and which part shows on the catalog card.` |
| 4 | `Waitlists That Run Themselves` | `When your class fills up, students join a waitlist. The moment a seat opens, the next person is offered it and held for three days.` |
| 5 | `Your Roster On One Screen` | `See who is coming, mark someone as paid, move a student to another date, and email everyone at once without leaving the page.` |
| 6 | `Sales And Discount Codes` | `Put a class on sale and the banner, the crossed out price, and the new price appear everywhere on their own. Or hand out a code to a group.` |
| 7 | `Run It Again In One Click` | `Taught it once and want to teach it again? Make a copy with new dates and keep everything else exactly as it was.` |

### "See It For Yourself"

| Slot | String |
|---|---|
| Heading | `See It For Yourself` |
| Body 1 | `This is a real class page, built with the same editor you would use. Open it and scroll the whole thing.` |
| Body 2 | `The card on the right is exactly how a class looks in the catalog. Everything on it comes from what the instructor typed in.` |
| Button | `Open the Example Class` |
| Card caption (muted, under the card) | `This is a live catalog card, not a picture of one.` |

### "How It Works"

Section heading: `How It Works`

| # | `__title` | `__desc` |
|---|---|---|
| 1 | `Apply` | `Tell us what you want to teach. It takes a minute. An admin reads every application.` |
| 2 | `Build Your Class` | `Once you are approved, the class editor walks you through it in five steps. Save a draft any time and come back.` |
| 3 | `Go Live` | `Submit it for review. Your guild lead and an admin take a look, and then it goes into the catalog and out to members.` |

### "What We Ask Of You"

Section heading: `What We Ask Of You`

- `Know your material and know the tools you are teaching on.`
- `Show up on time and leave the space the way you found it.`
- `Answer your students when they message you through the app.`
- `Tell an admin as early as you can if you need to cancel a date.`

### "Common Questions"

Section heading: `Common Questions`

| Summary | Body |
|---|---|
| `Do I Need to Be an Expert?` | `No. You need to be safe and clear. Plenty of good classes are taught by people two steps ahead of their students.` |
| `How Long Does Approval Take?` | `An admin usually gets to applications within a week. You can check this page any time to see where yours stands.` |
| `Can I Charge for My Class?` | `Yes. You set the price and an optional member discount when you build the class. You can also run it free.` |
| `What If Nobody Signs Up?` | `You can cancel a class from your dashboard and everyone registered is told automatically. Nothing is stuck.` |

### "Read the Instructor Guide"

| Slot | String |
|---|---|
| `<summary>` | `Read the Instructor Guide` |
| Fallback (no seeded article) | `The guide has not been loaded yet. Ask an admin to run the help center seed.` |

### Apply modal

| Slot | String |
|---|---|
| Modal title | `Apply to Teach` |
| Field label | `What Would You Like to Teach?` |
| Field hint | `A sentence or two is plenty. Tell us the subject, roughly how long a class would run, and anything you have taught before.` |
| Blank error | `Tell us a little about what you want to teach.` |
| Submit | `Send My Application` |
| Cancel | `Cancel` |
| Success message | `Your application is in. An admin will get back to you.` |

### The three states

**NONE.** No status banner. Hero primary is **Apply to Teach**. Apply card:

| Slot | String |
|---|---|
| Heading | `Ready to Teach?` |
| Body | `Tell us what you have in mind and an admin will take it from there.` |
| Button | `Apply to Teach` |

**PENDING.** Status banner at the top of the page:

| Slot | String |
|---|---|
| `.pl-orientation-banner__title` | `Your Application Is In` |
| `.pl-orientation-banner__body` | `An admin is reading it. You will get an email when they decide, and you can check this page any time to see where it stands.` |
| Muted meta line | `Applied on {{ member.teaching_applied_at\|date }}.` |
| Apply card heading | `Waiting on an Admin` |
| Apply card body | `Nothing else to do right now. Have a look around the example class while you wait.` |
| Apply card button | `See an Example Class Page` |
| Apply card footnote (muted) | `Want to add something to your application? Ask an admin and they can update it for you.` |

**APPROVED.** Status banner is the existing `.pl-orientation-done` block verbatim:

| Slot | String |
|---|---|
| `.pl-orientation-done__title` | `You Can Teach` |
| `.pl-orientation-done__meta` | `Approved on {{ member.teaching_decided_at\|date }}.` |
| Action 1 | `Go to the Teaching Portal` |
| Action 2 | `Create a Class` |

Members in this state are only on this page via `classes:teach_why`; `classes:teach_overview`
sends them to the real portal.

**DECLINED.** Status banner, same `.pl-orientation-banner` shell:

| Slot | String |
|---|---|
| Title | `Not This Time` |
| Body | `An admin looked at your application and is not able to approve it yet. Here is what they said.` |
| Reason block | rendered in `.pl-review-note` with `.pl-review-note__label` reading `Note from the admin`, falling back to `The admin left no note.` |
| Closing line | `You can apply again whenever you like.` |
| Apply card button | `Apply Again` |

## A.6 Where the example class link points

The example class today is `Shaker Side Table: Hand-Cut Joinery` at
`/classes/shaker-side-table-hand-cut-joinery/`. It is a **hand created production row**: no seed
command, no fixed pk, no test guard. It is past dated so `bookable()` drops it from the catalog,
but it is `published` and not private, so `public()` keeps it and the direct URL works for anyone.
Hard coding that slug in a template is a 404 waiting to happen the first time someone archives it.

**Design:** one nullable setting on the classes singleton.

```python
# classes/models.py, ClassSettings
example_class = models.ForeignKey(
    "classes.ClassOffering", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    help_text="The class page shown as the worked example on the Teach at Past Lives page. Leave blank to hide that link.",
)
```

A data migration points it at the class whose slug is `shaker-side-table-hand-cut-joinery` when
that row exists, and reverses by nulling the field. `SET_NULL` means archiving or deleting the
example degrades to "no link" instead of a 500.

The view resolves it once:

```python
example = ClassSettings.load().example_class
example_url = example.get_absolute_url() if example is not None and example.status == "published" else None
```

`example_url is None` is a real state, not an oversight:

- The hero secondary button is not rendered.
- The "See It For Yourself" section is replaced by a single line and one button:
  `Have a Look at the Catalog` / `Browse the classes members are taking right now.` linking to
  `{{ BOOK_BASE_URL }}/classes/`.
- The card preview is not rendered (it has nothing to render).

Admins set it in Site Settings alongside the demo toggles.

## A.7 The admin side

Applications need somewhere to land or the queue never clears.

**Queue card** on `templates/classes/admin/overview.html`, placed after the existing
**Waiting on You** and **With Guild Leads** cards, gated on
`AdminCapability.Capability.CLASS_APPROVER` like the rest of that page.

| Slot | String |
|---|---|
| Card heading | `Teaching Applications ({{ n }})` |
| Empty state | `No applications waiting.` |
| Row | member name (links to the member page), days waiting in `.hub-text-muted`, the note truncated to 140 characters |
| Action 1 | `Approve` (`hub-btn hub-btn--sm hub-btn--success`) behind `confirm_modal` |
| Action 2 | `Decline` (`pl-btn pl-btn--danger pl-btn--sm`) behind `confirm_modal` with `confirm_note_name="reason"` |

Approve confirm: title `Let {{ name }} teach?`, message `They get the teaching portal and can
start building a class. They will be emailed.`, button `Approve`, `confirm_button_style="primary"`.

Decline confirm: title `Decline this application?`, note label `Why? (the applicant sees this)`,
note hint `Say what would change your answer, so they know whether to apply again.`, button
`Decline`. The reason is required by `decline_teaching`, so a blank note re-renders with
`Tell them why. They see this note.`

Approve posts to `classes:admin_teaching_approve`, decline to `classes:admin_teaching_decline`,
both `@classes_admin_access_required` + `require_POST`, both calling the model method and
returning a Django message.

The existing per member Instructor toggle on the member edit Permissions tab keeps working
untouched; `grant_teaching` is the same call.

## A.8 New CSS

All in `hub.css`, all on theme tokens, all verified in both themes.

```
.pl-teach-hero                 variant over .pl-guild-hero: centered content,
                               min-height clamp(200px, 26vw, 300px)
.pl-teach-hero__actions        flex, gap .75rem, wrap, justify-content center, margin-top 1rem

.pl-feature-grid               grid, repeat(auto-fill, minmax(16.5rem, 1fr)), gap 1rem, margin-top 1.5rem
.pl-feature-card               flex column, gap .65rem  (composes onto .hub-card; no hover lift)
.pl-feature-card__icon         2.5rem square, radius .6rem, background rgba(238,180,75,.12),
                               color var(--color-tuscan-yellow)
.pl-feature-card__title        700, 1.05rem, var(--hub-text)
.pl-feature-card__desc         var(--hub-text-muted), .875rem, line-height 1.45

.pl-teach-showcase             grid 1fr 1fr, gap 1.5rem, align-items center;
                               single column under 800px
.pl-teach-showcase__caption    var(--hub-text-muted), .8125rem, margin-top .5rem

.pl-teach-steps                list-style none, margin/padding 0, flex column, gap 1rem
.pl-teach-steps__item          flex row, gap .85rem, align-items flex-start
.pl-teach-steps__n             1.35rem circle, background var(--color-tuscan-yellow),
                               color var(--color-navy), 700, .75rem  (copied from .pl-wizard-nav__n)
.pl-teach-steps__title         700
.pl-teach-steps__desc          var(--hub-text-muted), .875rem

.pl-teach-faq__item            border-bottom 1px solid var(--hub-border); padding .75rem 0
.pl-teach-faq__item summary    cursor pointer, 600, list-style none, ::marker suppressed

.pl-teach-apply                text-align center; its .pl-btn gets margin-top 1rem  (Rule 18)
```

`.pl-feature-*` is new rather than a reuse of `.pl-tools-grid` / `.pl-tool-card` on purpose: those
names say "admin tools", they are built for links, and their hover lift is wrong on a static card.
The declarations are copied verbatim so the two grids stay visually identical. See §D.2.

## A.9 Mobile

- The hero's `clamp()` already handles width; `__actions` wraps and both buttons go full width
  under 480px, matching the existing `.pl-orientation-done__actions .pl-btn { width: 100% }` rule.
- `.pl-feature-grid` auto fills to one column on a phone with no media query.
- `.pl-teach-showcase` collapses to one column at 800px with the copy first and the card second
  (source order, no `order` needed).
- `.pl-teach-steps` is already a vertical list.
- The `<details>` blocks are full width and have full row tap targets.
- The apply modal is `--sm` (400px max) and the backdrop already has `padding: 1rem`.

---

# B. Hero Placement for the Card, and Previews Inside the Editor

## B.1 The problem, stated plainly

The detail banner is `.cp-detail__hero` at `height: clamp(280px, 42vw, 520px)`, full width, which
at a 1100px `--max` is roughly **2.6:1**. The catalog card is `.cp-page .cls-media` at a **fixed
150px height, full width**, which in a 3 column grid is roughly **2.2:1** and in a single column
phone grid is closer to **2.5:1**. Both are `object-fit: cover`.

One stored value cannot serve both. Worse, today's two tools disagree with each other:

- **Cropper.js** (`static/js/hero_cropper.js`) writes a pixel box with `w > 0`. `hero_object_position`
  reads the source image, takes the box centre, and returns a percentage pair.
- **`heroPlacement`** (`static/js/hero_placement.js`, the "Adjust" tool on the rendered detail page)
  posts `w: 0, h: 0`, which `HeroCropMixin.hero_object_position` treats as "x and y are already
  percentages" (`core/models.py:56`).

And `_list_results.html` only applies the position **when `hero_crop_w` is truthy**:

```django
{% if offering.hero_crop_w %}style="object-position: {{ offering.hero_object_position }};"{% endif %}
```

So an instructor who uses the Adjust sliders on the detail page changes the banner and the card
silently ignores them. That is a live bug, not just a gap.

## B.2 Data

Two fields on `ClassOffering` (not on `HeroCropMixin`: no other model has a catalog card), one
migration, plus one property.

```python
card_focus_x = models.PositiveSmallIntegerField(
    null=True, blank=True,
    help_text="Horizontal focal point for the catalog card, 0 to 100. Null follows the banner.",
)
card_focus_y = models.PositiveSmallIntegerField(
    null=True, blank=True,
    help_text="Vertical focal point for the catalog card, 0 to 100. Null follows the banner.",
)

@property
def card_object_position(self) -> str:
    """CSS object-position for the 150px catalog card."""
    if self.card_focus_x is None or self.card_focus_y is None:
        return self.hero_object_position
    return f"{self.card_focus_x}% {self.card_focus_y}%"
```

Null meaning "follow the banner" is the whole trick. Every existing class keeps working with no
data migration, an instructor who never touches the card tool gets a sensible derived crop from
their 16:9 box, and the override only exists when someone deliberately made one.

The form carries it the way `hero_crop` already does, not by AJAX. `classes/forms.py:102`
`_HeroCropMixin` adds a hidden `hero_crop` CharField holding `{"x","y","w","h"}` JSON, validated in
`clean`. A sibling `_CardFocusMixin.add_card_focus_field()` adds a hidden `card_focus` CharField
holding `{"x": int, "y": int}`, validates both are integers in 0 to 100 (`Focal point must be
between 0 and 100.`) and that an empty string clears both fields to null, and writes
`card_focus_x` / `card_focus_y` in `save`. Both mixins go on `ClassOfferingForm` and
`TeachClassOfferingForm`.

Consistency argument: the banner crop already saves with the form rather than instantly, so the
card focus behaving the same way means the Photos step has one save rule, not two.

## B.3 Where the card tool lives, and how it reads

Step 2 of the composer, **Photos And Video**, in a section headed **Your Photo, Two Shapes**.

The section opens with one sentence that does the whole job of making the two decisions legible:

> `The class page shows a wide banner. The catalog card shows a short strip. They crop your photo differently, so you choose the important part of the photo twice.`

Then a two pane row, `.pl-crop-pair` (`grid-template-columns: 1fr 1fr`, gap `1.25rem`, one column
under 800px):

```
┌─ .pl-crop-pane ───────────────────┐ ┌─ .pl-crop-pane ────────────────────┐
│ Class Page Banner                 │ │ Catalog Card                       │
│ Drag the box. Everything outside  │ │ Drag the photo. This is the exact  │
│ it is cut off.                    │ │ size and shape members see.        │
│                                   │ │                                    │
│  [ existing hero_image_field +    │ │  [ live .cls-card, 150px media ]   │
│    Cropper.js 16:9 drag box ]     │ │  [ V slider ]  [ H slider ]        │
│                                   │ │  [ Match the Banner ]              │
└───────────────────────────────────┘ └────────────────────────────────────┘
```

Left pane: `classes/_components/hero_image_field.html` unchanged, cropper and all. The upload zone
lives here, so uploading a photo is the first thing in the pane and both panes update from it.

Right pane: a new component `classes/_components/card_focus_field.html`.

### What the preview renders, and why it is the media block only

The whole card cannot be included here. There is no `group` in the editor's context, and the
editor is editing one offering that may later be one of several date sets collapsed into a single
card. Two shapes were available:

1. Extract only the **media block** (the `<a class="cls-media">` image portion,
   `_list_results.html:41-58`, which reads `offering` and nothing else) and render it inside a real
   card shaped frame.
2. Extract the whole card and have the editor build a synthetic single member group.

**Chosen: option 1.** Not as a compromise, as the more truthful of the two on this surface. Option
2 would render a full body from a group that does not exist yet, and for a class offered on several
date sets it would show a confident single date card that is flatly wrong about the thing the body
exists to communicate. The positioning tool affects the image and only the image, so previewing the
image and framing it honestly is the whole job. The marketing page (§A.3) is the opposite case: the
example class really is solo and really has a group, so it renders the whole card.

**Two partials, nested:**

| Partial | Params | Contents |
|---|---|---|
| `templates/classes/public/_class_card_media.html` | `offering`, optional `preview` | the `.cls-media` block and its five image branches |
| `templates/classes/public/_class_card.html` | `group` (derives `offering=group.representative`) | the whole `.cls-card`; includes the media partial |

`preview=True` renders `<span class="cls-media">` instead of `<a class="cls-media">` with no
`href`. `.cp-page .cls-media` already declares `display: block`, so a `<span>` renders identically
and the editor loses an accidental navigation.

### The two frames

The card is 150px tall and **fluid** in width, so a single frame cannot be honest about the crop.
`.cp-page .cls-grid` is `repeat(auto-fill, minmax(min(260px,100%),1fr))` with `gap: 16px` inside a
`--max: 1100px` column, which packs **four** columns at roughly **264px** on a wide screen, and
falls to **one** column at roughly **358px** on a 390px phone.

That is the difference that matters:

| Frame | Width | Aspect | Effect on a 16:9 photo |
|---|---|---|---|
| `On a Laptop` | 264px | 1.76:1 | almost identical to 16:9, nearly nothing extra is cut |
| `On a Phone` | 358px | 2.39:1 | a real crop, the top and bottom of the photo go |

So the pane renders **both**, stacked vertically (the pane is roughly 430px wide at the composer's
content width, so 358px fits and 264+358 side by side would not), each labelled, both driven by the
same sliders and the same live `object-position`:

```django
<div class="cp-page pl-card-focus" x-data="cardFocus({ initial: '{{ offering.card_object_position }}' })">
  <div class="pl-card-focus__frame pl-card-focus__frame--laptop">
    <span class="pl-card-focus__frame-label">On a Laptop</span>
    <div class="cls-card">{% include "classes/public/_class_card_media.html" with offering=offering preview=True %}</div>
  </div>
  <div class="pl-card-focus__frame pl-card-focus__frame--phone">
    <span class="pl-card-focus__frame-label">On a Phone</span>
    <div class="cls-card">{% include "classes/public/_class_card_media.html" with offering=offering preview=True %}</div>
  </div>
  <div class="pl-card-focus__sliders">…</div>
  <input type="hidden" name="card_focus" data-card-focus-input value="{{ form.card_focus.value|default:'' }}">
</div>
```

`cms-public.css` is linked at the top of the composer template beside the existing
`session-calendar.css` link. `.cp-page`'s token block (`cms-public.css:341`) is fully self
contained and sources from the hub tokens, so both frames are correct in both themes with no extra
work.

**Verify the two widths against the rendered catalog before hardcoding them.** 264 and 358 are
derived from `--max: 1100px`, `gap: 16px`, and a 390px phone; if the catalog container is capped
somewhere other than 1100px the laptop number moves. The derivation belongs in a comment above the
CSS rule so the next person can redo it.

### The rest of the tool

- Both `<img>`s get `:style="'object-position: ' + posX + '% ' + posY + '%'"` from a new Alpine
  component **`cardFocus`** in a new file `static/js/card_focus.js`. It is `heroPlacement` with the
  network half removed: it holds `posX`, `posY`, `initialX`, `initialY`, parses an initial
  `"X% Y%"` string, and instead of `fetch`ing it writes `JSON.stringify({x, y})` into the hidden
  `card_focus` input on every change. `matchBanner()` clears the input to `""` and resets the
  sliders to the banner's derived position.
- Two `<input type="range" min="0" max="100">` sliders sit **below** both frames, labelled
  `Up and down` and `Left and right` (not `V` and `H`; the detail page tool's one letter labels are
  a cramped overlay, this one has room). They are wrapped in `.pl-card-focus__sliders`.
- Because only the media block renders, there is no body copy to be stale about, and the pane's
  muted caption says what the frames are: `This is the photo only, at the two widths members
  actually see. Your title and dates sit underneath it.`

**Ordering note.** Both sliders move the image, and on the laptop frame the horizontal slider
barely does anything. That is honest and worth leaving in rather than hiding: a portrait photo on a
wide card makes the horizontal slider the one that matters, and the tool should not guess which
case the instructor is in.

## B.4 The detail page preview

Two ways in, both from the composer.

**1. New tab, from the action bar, on every step.** The existing
`classes:class_preview` link, label `Preview ↗`, `target="_blank" rel="noopener"`. This is what
exists today; it stays because a full width page in its own tab is the honest way to judge a
banner.

**2. Embedded, on step 5.** A `.pl-wizard-preview` block reusing the composer's own preview
classes verbatim:

```django
<div class="pl-wizard-preview">
  <div class="pl-wizard-preview__bar">
    <button type="button" class="hub-btn hub-btn--sm hub-btn--ghost" @click="reloadPreview()">Refresh Preview</button>
    <a class="hub-btn hub-btn--sm hub-btn--ghost" href="{% url 'classes:class_preview' pk=offering.pk %}" target="_blank" rel="noopener">Open in a New Tab ↗</a>
  </div>
  <div class="pl-wizard-preview__pane">
    <iframe class="pl-wizard-preview__iframe" x-ref="previewFrame" src="{% url 'classes:class_preview' pk=offering.pk %}" title="Preview of your class page" loading="lazy"></iframe>
  </div>
</div>
```

`reloadPreview()` is one line of Alpine: `$refs.previewFrame.contentWindow.location.reload()`.

**The gotcha that will otherwise cost an afternoon.** `plfog/settings.py:221` loads
`django.middleware.clickjacking.XFrameOptionsMiddleware` and never sets `X_FRAME_OPTIONS`, so
Django's default `DENY` applies and the iframe renders blank with a console error and no visible
failure. `classes.views.class_preview` must be decorated
`@xframe_options_sameorigin`. It is same origin: the composer links it with `{% url %}`, so it is
served from whatever host the instructor is already on, not from `BOOK_BASE_URL`.

The iframe is 520px tall (`.pl-wizard-preview__iframe`) and scrolls internally.

## B.5 Before the class has been saved once

`class_preview` needs a pk, `hero_image_field.html` needs a pk for its AJAX upload URL, and
`image_formset.html` needs a pk for instant gallery saves. Today the create form falls back to
non AJAX variants of all three, which is why the create and edit experiences diverge.

**Design: the composer mints the draft at the end of step 1, and from step 2 onward a pk always
exists.** §C.6 has the mechanics. The consequence for this section is that the Photos step never
runs without a pk, so the create-mode fallbacks in `hero_image_field.html` and `image_formset.html`
become dead code for the composer path. Leave them in place (they are load bearing for the admin
create flow until that is migrated too), but the composer never reaches them.

### Everything in this spec that needs a saved row

This is the full list, so the implementer can check it against §C.6 rather than discover it one
`NoReverseMatch` at a time. Everything below lives on step 2 or later, which is exactly why step 1
is the boundary.

| Needs a pk | Where | Step |
|---|---|---|
| AJAX hero upload (`teach_class_hero_upload`) | `hero_image_field.html` | 2 |
| Instant gallery save, reorder, alt text, delete | `image_formset.html` | 2 |
| `offering.image.url` in the card preview | `_class_card_media.html` | 2 and 5 |
| `offering.slug` for the card link | `_class_card_media.html` (suppressed by `preview=True`, but real on step 5's card) | 2 and 5 |
| `card_object_position` reading saved crop fields | the card frames | 2 and 5 |
| Discount codes editor | `class_discount_codes_section.html` | 3 |
| `readiness()` gallery check (`self.gallery_images.exists()`) | the readiness card and the tab marks | 5, and the tab marks on 1 to 3 |
| `review_pipeline()` reading `ClassApproval` rows | the pipeline card above the tabs | all |
| `class_preview` iframe and new tab link | step 5 preview, action bar | 5 |
| `class_qr_share.html` | step 5 | 5 |
| Sale modal and sale pill | the Manage Class page, not the composer | n/a |
| `teach_class_detail` as the action bar's Cancel target | action bar | all |

Two consequences to build:

- **On step 1 with no pk**, the pipeline card and the tab completion marks are not rendered at all
  (there is nothing to read), tabs 2 to 5 are disabled (§C.6), and the action bar's **Cancel**
  link falls back to `classes:teach_dashboard` instead of `classes:teach_class_detail`.
- **`readiness()` and `review_pipeline()` must never be called on an unsaved instance.** The view
  guards on `offering.pk` before putting `readiness`, `pipeline`, or `step_marks` into the context,
  and the template guards on the context keys, not on `offering`.

The empty states that remain, and their copy:

| Situation | What renders |
|---|---|
| Step 2, no photo uploaded yet | Left pane: the `.cls-image-upload-zone` alone. Right pane: `.pl-preview-empty` block, dashed border, muted, reading `Upload a photo and your card shows up here.` |
| Step 5, class has a photo but no gallery photo | The readiness list already says `Add one gallery photo.` and links to `#gallery-manager`. Nothing extra. |
| Step 5, preview iframe still loading | The existing `.pl-wizard-preview__loading` paragraph, text `Building your preview…` |
| `card_focus` cleared (Match the Banner) | Sliders snap to the banner derived position; a muted line under them reads `Following the banner.` |

`.pl-preview-empty` is new: `border: 1px dashed var(--hub-border-strong); border-radius: 8px;
padding: 2rem 1rem; text-align: center; color: var(--hub-text-muted); font-size: 0.875rem;` with a
`height: 150px` so the empty state occupies exactly the space the card will.

## B.6 Copy strings

| Slot | String |
|---|---|
| Section title (`.pl-compose-section__title`) | `Your Photo, Two Shapes` |
| Section note (`.pl-compose-section__note`) | `The class page shows a wide banner. The catalog card shows a short strip. They crop your photo differently, so you choose the important part of the photo twice.` |
| Left pane label | `Class Page Banner` |
| Left pane hint | `Drag the box. Everything outside it is cut off.` |
| Right pane label | `Catalog Card` |
| Right pane hint | `Drag the sliders. These are the exact sizes members see in the catalog.` |
| Frame label 1 | `On a Laptop` |
| Frame label 2 | `On a Phone` |
| Frames caption (muted) | `This is the photo only, at the two widths members actually see. Your title and dates sit underneath it.` |
| Slider 1 label | `Up and down` |
| Slider 2 label | `Left and right` |
| Reset button | `Match the Banner` |
| Following state (muted) | `Following the banner.` |
| Empty preview | `Upload a photo and your card shows up here.` |
| Preview bar button | `Refresh Preview` |
| Preview bar link | `Open in a New Tab ↗` |
| Preview loading | `Building your preview…` |
| Existing `.pl-help` bubble on the upload label | unchanged (`1600 × 900 px (16:9)` etc.) |

## B.7 New CSS

```
.pl-crop-pair                  grid 1fr 1fr, gap 1.25rem, align-items start; 1 column under 800px
.pl-crop-pane                  min-width 0
.pl-crop-pane__label           .6875rem, 700, uppercase, letter-spacing .06em,
                               var(--hub-text-muted), margin 0 0 .5rem
                               (copied from .pl-profile-preview__heading, hub.css:3053)
.pl-crop-pane__hint            .8125rem, var(--hub-text-muted), margin .35rem 0 .75rem

.pl-card-focus                 flex column, gap 1rem
.pl-card-focus__frame          flex column, gap .35rem, max-width 100%
.pl-card-focus__frame--laptop  width 264px      /* see the derivation comment in B.3 */
.pl-card-focus__frame--phone   width 358px
.pl-card-focus__frame-label    .6875rem, 700, uppercase, letter-spacing .06em,
                               var(--hub-text-muted)
.pl-card-focus__sliders        flex column, gap .5rem, margin-top .25rem
.pl-card-focus__slider-row     flex row, align-items center, gap .75rem, min-height 2.75rem
.pl-card-focus__slider-label   .75rem, var(--hub-text-muted), min-width 6.5rem
.pl-card-focus__note           .8125rem, var(--hub-text-muted), margin-top .5rem

.pl-teach-showcase__grid       grid-template-columns: minmax(0, 320px)   (overrides .cls-grid)

.pl-preview-empty              dashed border, radius 8px, height 150px, grid place-items center,
                               var(--hub-text-muted), .875rem
```

Both card frames sit inside `.cp-page`, so the `.cls-card` inside them picks up the real border,
radius, and `overflow: hidden` for free, and `.cls-media`'s own `height: 150px` supplies the
height. The frame classes set width only, and `max-width: 100%` keeps the 358px phone frame from
overflowing a narrow pane.

`.pl-teach-showcase__grid` overrides `.cp-page .cls-grid`, which is a two class selector.
`cms-public.css` is linked inline in the template and therefore loads **after** `hub.css`, so a
bare `.pl-teach-showcase__grid` in `hub.css` would lose the cascade. A template `<style>` block is
not allowed (Rule 9). Write it in `hub.css` as `.cp-page .cls-grid.pl-teach-showcase__grid`, which
outranks `.cp-page .cls-grid` on specificity regardless of source order.

## B.8 Mobile

- `.pl-crop-pair` goes to one column at 800px with the banner pane first. That is the right order:
  the upload zone lives in the banner pane, so on a phone you upload, crop, then scroll to the card.
- Both card frames are fixed width and carry `max-width: 100%`, so on a 320px phone the 358px phone
  frame shrinks to the viewport rather than causing a horizontal page scroll. It is then no longer
  pixel exact, which is acceptable: an instructor cropping on a phone is looking at approximately
  the width they are holding.
- Sliders keep a 44px tall touch row via `.pl-card-focus__slider-row { min-height: 2.75rem }`.
- The step 5 iframe stays 520px tall and scrolls internally; do not shrink it on mobile, a squashed
  page preview is worse than a scrollable one.
- `Open in a New Tab ↗` is the phone escape hatch and is why it stays in the bar rather than being
  replaced by the iframe.

## B.9 Every place a catalog card renders

`card_object_position` has to reach all of them or the tool lies. The two partials from §B.3 are
what make that true by construction.

| File | Today | Change |
|---|---|---|
| `templates/classes/public/_class_card_media.html` | — | **new**; owns the `.cls-media` block and its five image branches. Params `offering`, optional `preview` |
| `templates/classes/public/_class_card.html` | — | **new**; owns the whole `.cls-card`. Param `group`; derives `offering=group.representative`; includes the media partial |
| `templates/classes/public/_list_results.html` | inline card, `{% if offering.hero_crop_w %}` guard | includes `_class_card.html` per group; guard removed |
| `templates/classes/public/detail.html` (related classes, ~line 430) | a second, simpler inline card with **no** object-position at all | includes `_class_card_media.html` only, keeping its existing simplified body. Related offerings are individual rows, not catalog groups, so giving them the full group aware card would be wrong |
| `classes/_components/card_focus_field.html` | — | the composer's two preview frames, media partial only (§B.3) |
| `templates/classes/teach/why_teach.html` | — | the marketing showcase, full card with a real `CatalogGroup` (§A.3) |

In the **media** partial the `<img>` becomes:

```django
<img class="cls-img" src="{{ offering.image.url }}" alt="" loading="lazy"
     style="object-position: {{ offering.card_object_position }};">
```

Unconditional. `card_object_position` falls back through `hero_object_position` to `"50% 50%"`, so
there is no case where the attribute is wrong to emit.

**This is the fix for the live bug in §B.1**: a focal point set by the detail page Adjust sliders
(`w=0`) now reaches the card, because the card no longer gates on `hero_crop_w`.

The `cp-detail__other-dates` list and the flyer (`class-flyer.css`) render no image and are
untouched.

---

# C. The Multi-Step Class Composer

## C.1 Shape

Identical in kind to `templates/hub/announcement_compose.html`:

- **One page.** No server side wizard, no per step URL, no session state.
- **One `<form method="post" enctype="multipart/form-data">`** wrapping all five steps.
- **One Alpine `x-data`** on the wrapping `hub-card` holding `phase`, plus the small amount of
  derived state the tabs need.
- **`x-show="phase === N"`** blocks. Every step's layout comes from a CSS class, never an inline
  `display` (Rule 12).
- **`.pl-phase-tabs` / `.pl-phase-tab` / `.pl-phase-tab--active`** for the bar, `role="tablist"`,
  `:aria-selected`.
- **htmx only for sub fragments.** In this composer that means nothing at first: the gallery
  formset already does its own AJAX and the preview is an iframe. No htmx is added.

**One template, not two.** Today `templates/classes/teach/class_form.html` and
`templates/classes/admin/class_form.html` are 105 and 77 line near duplicates that have already
drifted (the teach one has `data-help-key` attributes and a readiness card the admin one lacks).
Extract the whole composer into `templates/classes/_components/class_composer.html`, included by
both with a single `is_admin` flag that controls the `instructor`, `is_private`, and
`private_for_name` fields and the discount codes URL names. The twins shrink to a heading, the
context includes, and the include.

## C.2 The steps

Five steps. Four hold fields; the fifth is review and submit, exactly as the announcement
composer's phase 2 is preview and send.

| # | Tab label | Step heading (`<h2>`) | Fields, in render order |
|---|---|---|---|
| 1 | `1. Basics` | `The Basics` | `title`, `category` (labelled Guild Type), `instructor` *(admin only)*, `description`, then a `Free Or Paid` section: `is_free`, `price_cents` |
| 2 | `2. Photos` | `Photos And Video` | `image` + `hero_crop` (banner cropper), `card_focus` (card tool + live card preview), gallery formset, `video_url` |
| 3 | `3. Dates & Price` | `Dates, Seats And Price` | `scheduling_model`, `scheduling_type`, sessions formset, `flexible_note`, `capacity`, `member_discount_pct`, `is_private` + `private_for_name` *(admin only)*, discount codes editor *(flag gated, saved classes only)* |
| 4 | `4. Details` | `What Students Need To Know` | `prerequisites`, `materials_included`, `materials_to_bring`, `safety_requirements`, `age_minimum`, `age_guardian_note`, FAQ formset |
| 5 | `5. Review` | `Review And Submit` | no fields; pipeline, readiness, previews, share card, submit |

**Removed from the form entirely:** `sale_enabled`, `sale_kind`, `sale_percent`,
`sale_amount_cents`, `sale_banner_text`, `sale_allow_discount_codes`. They come out of
`ClassOfferingForm.Meta.fields` and `TeachClassOfferingForm.Meta.fields` and move to a dedicated
`ClassSaleForm` (§C.13). `_SaleMixin.clean_sale_fields()` moves with them, unchanged.
`classes/_components/class_sale_section.html` is deleted.

### Why the fields land where they do

- **Step 1 carries `description`.** It is the pitch and it is a readiness item. Today it sits below
  pricing and the sale block, roughly 1,800px down the page, which is why classes arrive at review
  with a two line description. Putting it beside the title makes writing it the second thing you do.
- **Step 1 also carries `is_free` and `price_cents`.** `price_cents` is NOT NULL with no default and
  the form demands a price unless free is ticked, so it is the one field a draft row cannot be saved
  without; with it on step 3, Save Draft from step 1 would bounce the instructor to step 3, which
  breaks "save a draft any time". Everything else on steps 2 to 4 is optional or defaulted, so a step
  1 save always succeeds. The member discount stays on step 3 with seats and dates.
- **Step 2 is media only.** The banner, the card focus, the gallery, and the video are one job:
  what the class looks like. Grouping them is also what makes §B's two pane comparison possible.
- **Step 3 is the commercial and logistical shape of one run.** Dates, seats, and price answer
  "when, how many, how much", and they are the fields an admin has to lock after publication
  (`TeachPublishedClassForm` excludes exactly these). Splitting them across two steps would split
  a single decision. The step is the heaviest, so it is divided by two `.pl-compose-section`
  headings: **`When It Meets`** and **`Seats And Price`**.
- **Step 4 is everything a student reads to prepare.** All seven of these are `collapsible_field`
  optional fields plus the FAQ. Putting them behind their own step is what removes the most raw
  page height, and none of them block submission.
- **`age_minimum` and `age_guardian_note` go on step 4, not step 3.** They read as "who this class
  suits", not as capacity. They also render on the detail page under a `Age` heading next to the
  other prose blocks.

## C.3 One source of truth for the step map

The step map is data, in one place, because three consumers must agree: the template that renders
the tabs, the view that decides which step to land on after a failed save, and the test that
proves no field went missing.

New module `classes/composer.py`:

```python
@dataclass(frozen=True)
class ComposerStep:
    number: int
    key: str
    tab_label: str
    heading: str
    fields: tuple[str, ...]

COMPOSER_STEPS: tuple[ComposerStep, ...] = (...)

def step_for_field(name: str) -> int: ...
def error_steps(form, formsets) -> list[int]: ...
```

`error_steps` maps every bound error to its step: form field errors via `step_for_field`, the
sessions formset to step 3, the gallery formset to step 2, the FAQ formset to step 4, and non field
errors to step 1.

**The guard test** (`classes/spec/composer_spec.py`): for each of `ClassOfferingForm` and
`TeachClassOfferingForm`, assert that every name in `Meta.fields` plus the injected `is_free`,
`hero_crop`, and `card_focus` appears in exactly one `ComposerStep.fields`, and that no
`ComposerStep` names a field the form does not have. A new field then fails the suite instead of
silently vanishing from the UI, which is the failure mode a five step form invites.

## C.4 The step indicator and the review pipeline are two components

They are two different facts and they must stay two components.

| | Step tabs | Review pipeline |
|---|---|---|
| Answers | "How much of the form have I filled in?" | "Where is my class with the reviewers?" |
| Owner | the instructor, right now | the guild lead and the admin, over days |
| Component | `.pl-phase-tabs` (this spec) | `classes/_components/review_pipeline.html` (exists) |
| Also rendered on | nowhere else | teach class overview, admin class detail, the review page, and the review email |
| Changes when | you click a tab | someone approves or bounces |

Merging them would say that reaching step 5 means approved. It does not; it means you may now
submit. And the pipeline already renders on four other surfaces from `review_pipeline()`, so a
merged component would have to grow a second mode for each of them.

**How the ask for "a nice visual of the current step in the pipeline to approval" is answered:**
both are on screen at once, stacked, with the pipeline above the tabs.

```
┌ hub-card, pl-pipeline-card ───────────────────────────────┐
│ Where Your Class Is                                        │
│  ✓ Submitted ── ● Guild lead ── ○ Admin ── ○ Live          │
│ Waiting on the guild lead (Woodshop)                       │
└────────────────────────────────────────────────────────────┘
┌ hub-card, pl-composer ─────────────────────────────────────┐
│ Step 3 of 5                                                │
│ [1. Basics ✓][2. Photos ✓][3. Dates & Price][4. Details][5. Review] │
│ ...                                                        │
```

The pipeline card is the block that already exists at the top of
`templates/classes/teach/class_form.html:5-19`, rendered under the same condition
(`pipeline and not pipeline.is_live and not pipeline.muted`), with its `Where Your Class Is`
heading, its `.pl-review-note` bounce block, and its two conditional footnotes. It moves into the
shared composer template unchanged. On a fresh draft it reads `Not submitted yet` with the
Submitted step current, so a first time instructor sees the whole road before they type anything.

**Tab completion marks.** The tabs carry a per step readiness mark, derived from
`ClassOffering.readiness()`, not invented:

| Step | Readiness items it owns | Mark shown when all are `ok` |
|---|---|---|
| 1 | `Description` | `✓` |
| 2 | `Hero photo`, `Gallery photo` | `✓` |
| 3 | `Dates`, `Capacity` | `✓` |
| 4 | none | never marked |
| 5 | none | never marked |

Rendered as `<span class="pl-phase-tab__mark" aria-hidden="true">✓</span>` inside the button, with
the tab getting `.pl-phase-tab--done`. Steps 4 and 5 have no readiness items and correctly show no
mark: nothing on step 4 blocks submission, and pretending otherwise would train people to fill in
optional fields to clear a checkmark.

## C.5 The action bar

One bar, bottom of the composer card, **outside** every `x-show` block so it is present on all five
steps. Sticky.

```django
<div class="pl-composer-bar">
  <button type="button" class="hub-btn hub-btn--sm hub-btn--ghost" x-show="phase > 1" @click="goTo(phase - 1)">← Back</button>
  <button type="submit" class="hub-btn hub-btn--sm hub-btn--ghost" name="action" value="save">Save Draft</button>
  <span class="pl-composer-bar__spacer"></span>
  <button type="button" class="hub-btn hub-btn--sm hub-btn--primary" x-show="phase < 5" @click="goTo(phase + 1)">Next →</button>
  <button type="button" class="hub-btn hub-btn--sm hub-btn--primary" x-show="phase === 5"
          @click="$dispatch('open-confirm', 'submit-class')">Submit for Review</button>
  <a class="hub-btn hub-btn--sm hub-btn--ghost" href="{{ cancel_url }}">Cancel</a>
</div>
```

`cancel_url` is computed in the view, not the template, because on step 1 of a brand new class
there is no pk to reverse against: `classes:teach_class_detail` when `offering.pk` is set,
`classes:teach_dashboard` otherwise.

**Save Draft is a real submit on every step**, so the answer to "where does Save Draft live so it is
reachable from every step" is: in a bar that never scrolls away. It posts the whole form, the view
saves and redirects back to the composer with `?step=N` so the user lands where they left off, and
a toast is not used because this is a full page POST (Rule 6): Django message `Draft saved.`

This satisfies Rule 21's intent (the save control is the last thing in the form, with nothing
stranded beneath it) while acknowledging that a composer has two primary actions. The label stays
**Save Draft**, matching the string already on `class_form.html:96`, rather than a bare `Save`,
because next to a **Submit for Review** button a bare `Save` reads as "save and submit".

## C.6 Draft creation, and the one navigation that posts

`class_preview` needs a pk. The hero AJAX upload needs a pk. The gallery instant save needs a pk.

**Rule: the composer mints the draft when you leave step 1, and never navigates the browser again.**

```
GET  teach/classes/new/      -> composer, phase 1, no pk, tabs 2-5 disabled
"Next →" on step 1 with no pk -> real submit (action=save), server saves the DRAFT,
                                 redirects to teach/classes/<pk>/edit/?step=2
"Next →" anywhere else        -> pure Alpine, no request
"Save Draft"                  -> submit, redirect back to ?step=<current>
```

`goTo(n)` is three lines: if `n > 1 && !this.hasPk`, set the hidden `next_step` input and
`$refs.composerForm.requestSubmit()`; otherwise `this.phase = n`. `hasPk` and the initial `phase`
come from the server (`{{ initial_phase|default:1 }}`), read from `?step=` and clamped to 1..5.

While there is no pk, tabs 2 to 5 render `disabled` with a `.pl-help` bubble reading
`Add a title and a guild type first, then this unlocks.` Step 1's own validation (title and
category are required on the model form) is what gates the first submit, so a member who clicks
Next with an empty title gets the field errors, not a half created row.

**Why not create the row on GET?** Because class drafts are user visible: an abandoned "New Class"
would sit in the instructor's Classes list and in the admin's counts forever. The announcement
composer can create a draft eagerly because its drafts are invisible. This one cannot. The cost is
one page load between step 1 and step 2, once, per class.

## C.7 Errors on a step you are not looking at

This is the failure mode a multi step form invites, so it gets three mechanisms, not one.

**1. Land on the first broken step.** On a POST that fails validation the view computes
`error_steps(form, formsets)` (§C.3) and re-renders with `initial_phase = min(error_steps)`. The
user opens the page already looking at the first problem.

**2. Mark the broken tabs.** `x-data` carries `errorSteps: {{ error_steps|json_script-ish }}`. Each
tab binds `:class="{ 'pl-phase-tab--error': errorSteps.includes(N) }"` and
`:aria-invalid="errorSteps.includes(N)"`. `.pl-phase-tab--error` paints the label and the bottom
border with `--pl-lifecycle-cancelled` and shows a `●` in `.pl-phase-tab__mark` in place of the `✓`.

**3. A summary block above the tabs**, `.pl-composer-errors`, rendered server side only when
`error_steps` is non empty:

```
┌────────────────────────────────────────────────┐
│ Some Things Need Fixing                        │
│ • Photos: Hero photo                           │
│ • Dates & Price: Price, Capacity               │
└────────────────────────────────────────────────┘
```

Each line is a `<button type="button" class="pl-composer-errors__jump" @click="goTo(N)">` naming
the step and listing the failing field **labels** (not names). Clicking jumps to the step. The
block reuses `.pl-wizard-errors` for its margin and `.pl-field-error` for the text colour.

Copy:

| Slot | String |
|---|---|
| Heading | `Some Things Need Fixing` |
| Row | `{{ step.heading }}: {{ labels|join:", " }}` |
| Non field errors | rendered as today, in `.pl-field-errors.pl-wizard-errors` above the summary |

## C.8 Readiness

The `Ready to Submit?` card (`templates/classes/teach/class_form.html:88-94`) moves to **step 5**
and keeps its heading, its `data-help-key="teach.submit-for-review"`, and its
`readiness_list.html` include, but gains `grid=True` because step 5 is a full width card and the
grid variant has a 720px breakpoint built for exactly that (`hub.css:8314`).

`link_hints=True` stays. The anchors it links to (`#hero-preview`, `#gallery-manager`,
`#id_description`, `#class-dates`, `#id_capacity`) now live on steps that are `x-show`n false, so a
bare `href="#anchor"` scrolls to a hidden element and appears to do nothing.

**Fix:** the readiness list on step 5 renders its hints as
`<button type="button" @click="goToField('{{ item.anchor }}')">` instead of an anchor.
`goToField(id)` looks the anchor up in a template level map of anchor to step number, sets
`phase`, then `$nextTick(() => document.getElementById(id)?.scrollIntoView({block: 'center'}))`.
The map is generated from `COMPOSER_STEPS` so it cannot drift.

The `readiness_list.html` component gains one optional param, `jump=True`, that swaps the anchor
for the button. Every other caller (the admin review page, which passes `link_hints=False`) is
unaffected.

## C.9 Step 5 and the submit

Step 5, top to bottom:

1. `<h2>Review And Submit</h2>`
2. **`Ready to Submit?`** card (§C.8), `grid=True`.
3. **`How Your Page Looks`** section (`.pl-compose-section`), holding the `.pl-wizard-preview`
   iframe block from §B.4.
4. **`How Your Card Looks`** section, holding a single live `.cls-card` in a `.cp-page` wrapper.
   Same include as step 2's tool, without the sliders. It is here as well as on step 2 because step
   5 is where you check your work, and scrolling back two steps to see the card is the exact
   friction this redesign exists to remove.
5. **`Share & Print`** card: `classes/_components/class_qr_share.html`, moved here from the
   `.pl-edit-split` beside the title. A QR code you print belongs with "your class is ready", not
   next to the title input. The `.pl-edit-split` wrapper is deleted from both twins; step 1 is a
   plain single column.
6. The action bar (§C.5), which on this step shows **Submit for Review**.

**Submit** goes through `components/confirm_modal.html` in `confirm_js` mode, because the composer
must submit its own form rather than POST to a different URL:

```django
{% include "components/confirm_modal.html" with confirm_id="submit-class" confirm_button_style="primary" confirm_title="Submit This Class for Review?" confirm_message="Your guild lead and an admin take a look. You can still edit it while it waits, or take it back to draft." confirm_button_text="Submit It" confirm_js="document.getElementById('composer-action').value='submit'; document.getElementById('composer-form').requestSubmit();" %}
```

with `<input type="hidden" id="composer-action" name="action" value="save">` in the form. The
existing view already branches on `action == "submit"` and already surfaces
`readiness_error("submit")` as a Django message when the class is not ready, so an unready submit
re-renders with `Not ready to submit: Add a hero photo. Add one gallery photo.` and the readiness
card showing the same items.

When the class is not ready, the Submit button renders `disabled` with a `.pl-help` bubble reading
`Finish the checklist above first.` The server side guard stays regardless; the disabled button is
courtesy, not enforcement.

For an already published class the composer is not used at all: `class_form_published.html` and
`TeachPublishedClassForm` keep the light edit path exactly as they are.

## C.10 Composer copy strings

| Slot | String |
|---|---|
| Page heading (create) | `New Class` |
| Page heading (edit) | `Edit Class: {{ offering.title }}` |
| Lead (`.pl-wizard-lead`) | `Five steps. Save a draft any time and come back to it. Nothing goes live until an admin approves it.` |
| Mobile step count | `Step {{ n }} of 5` |
| Tab 1 | `1. Basics` |
| Tab 2 | `2. Photos` |
| Tab 3 | `3. Dates & Price` |
| Tab 4 | `4. Details` |
| Tab 5 | `5. Review` |
| Locked tab tooltip | `Add a title and a guild type first, then this unlocks.` |
| Step 1 heading | `The Basics` |
| Step 1 note | `What the class is and which guild it belongs to. The description is the first thing a member reads.` |
| Step 2 heading | `Photos And Video` |
| Step 2 sub sections | `Your Photo, Two Shapes` / `Gallery` / `Video` |
| Gallery note | `Extra photos shown on your class page. Finished pieces, the studio, tool close ups. Images save the moment you drop them in. You need at least one before you can submit.` |
| Video note | `Paste a YouTube link and it plays right on your class page. Optional.` |
| Step 3 heading | `Dates, Seats And Price` |
| Step 1 sub section | `Free Or Paid` |
| Step 3 sub sections | `When It Meets` / `Seats And Member Discount` |
| Dates note | `Add a single date for a one off class, or every date in the series. Students enroll once for all of them. You can add or remove dates here any time.` |
| Free Or Paid note | `Tick free for a no cost workshop. Otherwise set the full price. The member discount is on step 3.` |
| Seats note | `How many can attend, and the discount members get on top of the price from step 1.` |
| Step 4 heading | `What Students Need To Know` |
| Step 4 note | `All optional. Fill in what fits your class and leave the rest closed.` |
| Step 5 heading | `Review And Submit` |
| Step 5 sub sections | `Ready to Submit?` / `How Your Page Looks` / `How Your Card Looks` / `Share & Print` |
| Back | `← Back` |
| Next | `Next →` |
| Save | `Save Draft` |
| Submit | `Submit for Review` |
| Submit (bounced) | `Fix and Resubmit` |
| Cancel | `Cancel` |
| Save success message | `Draft saved.` |
| Submit blocked tooltip | `Finish the checklist above first.` |
| Error summary heading | `Some Things Need Fixing` |

The `Fix and Resubmit` variant matches the existing string on
`templates/classes/teach/class_overview.html:73` and is shown under the same condition
(`pipeline.is_bounced`).

## C.11 Composer CSS

```
.pl-composer                   flex column, gap 1.5rem            (mirrors .pl-wizard)
.pl-composer-count             .75rem, 700, uppercase, letter-spacing .06em,
                               var(--hub-text-muted), margin 0 0 .5rem; display none ≥ 641px
.pl-composer-errors            border 1px solid var(--pl-lifecycle-cancelled), radius 8px,
                               padding .85rem 1rem, margin-bottom 1.25rem
.pl-composer-errors__title     700, margin 0 0 .5rem
.pl-composer-errors__jump      background none, border none, padding 0, cursor pointer,
                               color var(--pl-lifecycle-cancelled), text-align left,
                               text-decoration underline, font-family var(--font-body)
.pl-composer-bar               position sticky, bottom 0, z-index 5,
                               display flex, gap .75rem, flex-wrap wrap, align-items center,
                               margin-top 1.5rem, padding 1rem 0,
                               border-top 1px solid var(--hub-border),
                               background var(--hub-card-bg)
.pl-composer-bar__spacer       flex 1
.pl-phase-tab__mark            margin-left .35rem, font-size .8em
.pl-phase-tab--done            color var(--hub-text);
                               .pl-phase-tab__mark { color: var(--pl-lifecycle-live) }
.pl-phase-tab--error           color var(--pl-lifecycle-cancelled);
                               border-bottom-color var(--pl-lifecycle-cancelled)
```

Plus two additions to the existing `.pl-phase-tabs` rule, guarded so the announcement composer is
unchanged above 640px:

```css
@media (max-width: 640px) {
    .pl-phase-tabs {
        flex-wrap: nowrap;
        overflow-x: auto;
        scrollbar-width: none;
        scroll-snap-type: x proximity;
    }
    .pl-phase-tabs::-webkit-scrollbar { display: none; }
    .pl-phase-tab { flex: 0 0 auto; scroll-snap-align: start; }
}
```

`.pl-composer-bar` needs an opaque background or the sticky bar shows page content through it. It
uses `--hub-card-bg` because it sits inside a `hub-card`.

## C.12 Mobile

- **Tabs scroll horizontally**, one row, snapping, no scrollbar. Five tabs at roughly 110px each is
  550px, so on a 390px phone two and a half are visible and the active one is scrolled into view by
  `goTo()` calling `$el.scrollIntoView({inline: 'center', block: 'nearest'})` on the active tab.
- **`Step 3 of 5` appears above the tabs on phones only** (`.pl-composer-count`, hidden at 641px
  and up). The tab bar alone does not communicate position when only half of it is visible.
- **The action bar is sticky and full width.** It already wraps; on a phone `Next →` and
  `Submit for Review` take `flex: 1 1 100%` under 480px so the primary action is a full width
  target, with `← Back`, `Save Draft`, and `Cancel` on the row above it.
- `.pl-crop-pair`, `.pl-teach-showcase`, and `.pl-edit-split` all collapse at 800px.
- The sessions formset, gallery grid, FAQ formset, and readiness grid keep the responsive behaviour
  they already have. Nothing in this spec changes them.
- The step 5 iframe keeps its 520px height and scrolls internally.

## C.13 The sale modal

The six sale fields leave the composer and become an action on the Manage Class page,
`templates/classes/teach/class_overview.html` (and its admin twin
`templates/classes/admin/class_detail.html`).

**Trigger,** in the existing `.admin-toolbar` action row, rendered when
`offering.lifecycle` is not `cancelled` / `archived` / `completed`:

| Condition | Button | Class |
|---|---|---|
| `offering.price_cents == 0` | nothing; a muted line `A free class cannot go on sale.` | — |
| `not offering.sale_is_active` | `Set Up a Sale` | `hub-btn hub-btn--sm` |
| `offering.sale_is_active` | `Edit the Sale` | `hub-btn hub-btn--sm` |

Both `@click="$dispatch('open-modal', 'class-sale')"`.

**Modal:** `components/modal.html`, `modal_id="class-sale"`, `modal_size="md"`,
`modal_title="Put This Class On Sale"`,
`modal_body_include="classes/teach/partials/sale_form.html"`. Server rendered inline, reopened on a
bound form by the same `x-init` the cancel and change-request modals already use
(`class_overview.html:8`). `md` (560px) rather than `sm`, because the body holds five controls and a
live price line.

**Body,** in order, all through `components/form_field.html` so they are wrapped in `.pl-form-group`
and inherit the theme input tokens (Rule 13):

1. `sale_kind` (select: `Percent off` / `Dollar amount off`)
2. `sale_percent`, `x-show="kind === 'percent'"`
3. `sale_amount_cents` (a `CentsAsDollarsField`, label `Amount off ($)`), `x-show="kind === 'fixed'"`
4. `sale_banner_text`
5. `sale_allow_discount_codes` (a toggle, via `components/toggle.html`)
6. The live price line
7. `.pl-modal__actions`

Alpine on the modal body root:

```django
x-data="{ kind: '{{ form.sale_kind.value|default:'percent' }}',
          pct: {{ form.sale_percent.value|default:0 }},
          off: {{ form.sale_amount_cents.value|default:0 }},
          full: {{ offering.price_cents }} }"
```

with a computed `salePrice()` mirroring `ClassOffering.sale_price_cents` exactly (percent:
`Math.floor(full * (100 - pct) / 100)`; fixed: `Math.max(0, full - off * 100)`). The server remains
the authority; this is a preview line, and `_SaleMixin.clean_sale_fields()` still rejects anything
that drops below the $0.50 minimum.

Because `x-show` is on a wrapper that must be `display: block` when revealed, the wrapper carries a
class and no inline `display` (Rule 12).

**Actions:**

| Condition | Buttons |
|---|---|
| Sale off | `Turn the Sale On` (`pl-btn pl-btn--primary`), `Cancel` (`pl-btn pl-btn--secondary`) |
| Sale on | `Save Sale Changes` (`pl-btn pl-btn--primary`), `Turn the Sale Off` (`pl-btn pl-btn--danger`), `Cancel` |

`sale_enabled` is **not** a field in the modal; the buttons carry it. `Turn the Sale On` and
`Save Sale Changes` submit with `name="action" value="on"`, `Turn the Sale Off` with
`value="off"` and skips validation of the amount fields entirely, so an instructor can always
switch a sale off even if the stored values are stale.

POST to `classes:teach_class_sale` / `classes:admin_class_sale`, `require_POST`, same scoping as
`teach_class_cancel` (`_teach_class_or_404`) and `admin_class_cancel` respectively.

**Copy:**

| Slot | String |
|---|---|
| Modal title | `Put This Class On Sale` |
| Intro (`.pl-field-hint` at the top of the body) | `A sale banner and the new price show up on your class page and on the catalog card automatically.` |
| `sale_kind` label | `How Much Off?` |
| `sale_percent` label | `Percent off` |
| `sale_amount_cents` label | `Amount off ($)` |
| `sale_banner_text` label | `Banner text` |
| `sale_banner_text` hint | `Leave it blank to use the standard sale banner.` |
| `sale_allow_discount_codes` toggle label | `Allow discount codes on top` |
| `sale_allow_discount_codes` toggle description | `Off by default, so a sale price cannot be stacked with another offer.` |
| Live price line | `Students will pay {{ new }} instead of {{ old }}.` |
| Live price line, invalid | `Enter how much off and we will show the new price.` |
| Button, off | `Turn the Sale On` |
| Button, on | `Save Sale Changes` |
| Button, danger | `Turn the Sale Off` |
| Cancel | `Cancel` |
| Message, on | `Sale is on. Members see it now.` |
| Message, saved | `Sale updated.` |
| Message, off | `Sale is off.` |
| Free class note | `A free class cannot go on sale.` |

`Turn the Sale Off` is a state change that members see immediately, so it is danger styled but does
**not** get a nested confirm modal: nesting a confirm inside a modal is the exact case
`modal_static` exists to paper over, and turning a sale off is trivially reversible.

## C.14 The sale pill

In `templates/classes/teach/class_detail_base.html`, directly after the
`lifecycle_badge.html` include in the header block, and the same in the admin twin:

```django
{% if offering.sale_is_active %}
<span class="pl-lifecycle-badge pl-sale-pill">Sale: {{ offering.sale_savings_display }}</span>
{% endif %}
```

It composes onto `.pl-lifecycle-badge` so it matches the badge beside it by construction (same
999px pill, same size, same uppercase treatment) and `.pl-sale-pill` overrides only two properties:

```css
.pl-sale-pill {
    background: var(--color-error-bg);
    color: var(--pl-lifecycle-cancelled);
}
```

Those are the tokens the catalog's `.badge.sale` already uses in spirit (a red tint), and both have
light theme values.

`sale_savings_display` returns `20% off` or `$15 off`, so the pill reads `Sale: 20% off`.

**It is a `<span>`, not a button.** The header renders on all five sub tabs (Overview,
Registrations, Waitlist, Discount Codes, Emails) but the sale modal and its POST handler live only
on the Overview tab. A pill that opens nothing on four of five tabs is worse than a pill that never
opens anything. The action is one scroll away in the action row on Overview. See §D.6.

---

# D. Decisions a reviewer might disagree with

### D.1 Five steps, not three, and not seven

Three steps would put roughly 1,000px of form on each and lose most of the benefit. Seven would
make the tab bar unreadable on a phone even with horizontal scroll. Five maps onto five real
decisions (what it is, what it looks like, when and how much, what to bring, check it) and gives a
tab bar that fits at 550px, which is scrollable but not endless.

The most arguable boundary is step 3. Price and dates are two decisions living on one step, and a
reviewer could reasonably split them. I combined them because they are the set of fields that
becomes admin-only after publication (`TeachPublishedClassForm` excludes exactly `title`,
`category`, dates, `capacity`, and price), which makes "the things you lock in for this run" a real
grouping and not just a way to hit five.

### D.2 New `.pl-feature-*` classes instead of reusing `.pl-tools-grid` / `.pl-tool-card`

`pl-tool-card` is a verbatim usable icon plus title plus body grid and reusing it would add zero
CSS. I did not, for two reasons: the names say "admin tools" and would mislead the next person
grepping for where they render, and the card is built for `<a>` with a hover lift that is wrong on
a static marketing card. The new rules are copied declaration for declaration, so the two grids
look identical and a later merge is trivial. A reviewer who values zero new CSS over honest naming
would reuse them, and that is a defensible call.

### D.3 The marketing page's "How It Works" does not reuse `.pl-pipeline`

Reusing the pipeline strip on the marketing page would be pretty and would teach the visual
language the instructor meets later. I used a plain numbered list instead, because the marketing
strip would show **Apply, Build, Go Live** while the real pipeline shows **Submitted, Guild lead,
Admin, Live**. Two strips with the same styling and different steps, one describing becoming an
instructor and one describing publishing a class, is a false mapping that would cost more in
confusion than it buys in polish. `.pl-pipeline` stays reserved for real per class review state.

### D.4 Four fields on `Member` rather than a `TeachingApplication` model

A real model would keep a history: every application, every decision, who decided, and a clean
re-application after a decline. Fields on `Member` overwrite the previous attempt. I chose the
fields because the entire existing teaching record is already one nullable timestamp on `Member`
(`instructor_oriented_at`), because `SiteActivity` already logs each grant and decline so the audit
trail exists outside the row, and because the makerspace will have single digit applications per
year. A reviewer who expects this to grow into a workflow (references, a guild lead sign off, a
required orientation booking) should push for the model now, because the migration later is
awkward.

### D.5 Two stored crops, not one clever one

The alternative is storing one crop and deriving the card position from it, which is what happens
today and which is why photos get beheaded on the card. A second alternative is storing a single
"focal point" percentage pair and deriving both, dropping the 16:9 box entirely; that would be
simpler and would delete `hero_cropper.js` and its CDN dependency, but it loses the ability to say
"use only this part of a large photo" on the banner, which the 16:9 box does well. I kept both
tools and added one nullable override. The cost is two fields and a UI that has to explain itself,
which §B.3's opening sentence and two labelled panes are there to do.

### D.5a The editor previews the card's image only, while the marketing page previews the whole card

Two surfaces show a catalog card and they do it differently, which looks like an inconsistency
worth flagging. The reason is that the catalog card is rendered from a `CatalogGroup`, not from an
offering, and only one of the two surfaces has a real one. The marketing page's example class is a
genuine solo class, so `CatalogGroup(example)` is the real object the catalog would build. The
editor is editing an offering that may later collapse into a multi date card, so any group it built
would be a guess about the exact part of the card that exists to express grouping.

The alternative is a synthetic single member group in the editor, which buys a full width preview
that is truthful about the photo and quietly wrong about the schedule block for every class offered
on more than one date set. I would rather show less and have all of it be true. A reviewer who
weights "the preview looks like the real thing" above that would take option 2, and the two
partials are nested so switching costs one include change.

The cost of my choice is that the editor preview does not show the title, price, or spots line, so
an instructor cannot check that a long title wraps badly on a card. That is a real gap and the
right place to close it later is step 5's `How Your Card Looks` section, which does have a saved
row and could build a real group from it.

### D.5b Two card frames, not one

A single 264px frame would be simpler and would match the desktop catalog. It would also
under-report the crop: a 16:9 photo loses almost nothing at 1.76:1 and a visible band at the 2.39:1
phone width, and the phone is where photos get beheaded. Showing only the laptop frame would let an
instructor sign off on a crop that is broken for most of the traffic. Showing only the phone frame
would make them over-correct. So both, stacked, labelled. A reviewer who wants one frame should
pick the phone one, not the laptop one.

### D.6 The sale pill does not open the sale modal

Making the pill clickable everywhere would need the sale modal and its POST handler on all five
sub tab views. Making it clickable only on Overview would give the same pill two behaviours
depending on which tab you are on, which is worse than one consistent behaviour. So it is a status
pill and the action lives in the action row. A reviewer who wants one click could move the modal
into `class_detail_base.html` and route every sub tab's POST through one view; that is a real
option, it is just four views' worth of change for one click saved.

### D.7 The first "Next" posts the page

Every other step transition is instant Alpine. The first one is a real round trip, because the
Photos step cannot function without a pk (AJAX hero upload, instant gallery save, preview). The
alternatives were creating the row on GET (which litters the instructor's Classes list and the
admin's counts with abandoned untitled drafts, and unlike the announcement composer these drafts
are user visible) or making step 2 work without a pk (which means keeping and testing two upload
paths forever). One page load per class, once, is the cheapest of the three.

### D.8 `Save Draft` rather than `Save`

Rule 21 says the primary submit says "Save". This form has two submits with different consequences,
and next to `Submit for Review` a bare `Save` reads as an abbreviation of it. `Save Draft` is also
the string already on today's form, so nobody has to relearn it. The rule's intent, no controls
stranded below the save button, is met by the sticky bar being structurally last in the form.

### D.9 The example class becomes a setting instead of a hard coded slug

`shaker-side-table-hand-cut-joinery` is a hand made production row with no seed command, no fixed
pk, and no test guarding its existence. Hard coding it means the marketing page's most important
link 404s the first time someone tidies up. A nullable FK with `SET_NULL` plus an explicit "no
example configured" state costs one field and one data migration. The alternative a reviewer might
prefer is writing a `seed_example_class` command so the row becomes managed like the example guild
already is; that is strictly better and strictly more work, and the FK does not block it.

### D.10 The retired orientation page

`classes:teach_orientation` and its acknowledge toggle disappear, and the seeded article moves into
a collapsed `<details>` on the marketing page. A reviewer could argue the reading should stay
mandatory and become a step in the application. I did not make it one because an acknowledgement
checkbox never proved anyone read anything, and the new gate is a human admin who can simply ask.
The content is still one click away and still linked from the approval email.

### D.11 Extracting the composer into one shared template

The teach and admin class forms have already drifted (`data-help-key` attributes and the readiness
card exist on one and not the other). Collapsing them into
`classes/_components/class_composer.html` with an `is_admin` flag stops that, at the cost of a
template with conditionals in it. A reviewer who prefers two explicit templates over one flagged
one has a point; my read is that this pair has already demonstrated it drifts when left alone.

---

## Checklist walk (FRONTEND.md)

- **§1 list editors:** the sessions, gallery, and FAQ formsets are reused unchanged. No new formset.
- **§2 forms:** every field goes through `components/form_field.html`; every boolean through
  `components/toggle.html`; validation lives in `ClassSaleForm`, `TeachingApplicationForm`, and the
  existing `_SaleMixin` / `_CardFocusMixin` `clean` methods, never in a view.
- **§3 destructive:** submit and approve and decline are behind `confirm_modal`; `Turn the Sale Off`
  is danger styled and reversible (§C.13 says why it has no confirm).
- **§4 states:** every list, preview, and card has an empty state (§B.5, §A.6, §A.7); modals
  re-render bound errors and reopen themselves; successes are Django messages because every POST
  here is a full page POST (Rule 6).
- **§5 themes:** every new rule uses theme tokens. `.cp-page`'s own token block is self contained
  and already handles both themes, which is why the card preview is safe to embed.
- **§6 mobile:** §A.9, §B.8, §C.12.
- **§7 buttons:** the action bar has `padding: 1rem 0` and a top border, so nothing butts against
  the section above it (Rule 18); the marketing apply button gets `margin-top: 1rem`.
- **§8 components:** everything named above by exact class and path; nothing reinvented except the
  `pl-` classes explicitly listed in §A.8, §B.7, and §C.11.
- **§9 emails:** the three new events follow the email rules, link the subject noun, and ship
  `.txt` and `.html` together.
- **§10 rules that bite:** no inline `display` on an `x-show` element (Rule 12); no bare textarea
  outside `.pl-form-group` (Rule 13); no `<input type="time">` anywhere (Rule 20, the session
  calendar already uses half hour selects); every `{# … #}` on one line and
  `tests/template_comment_lint_spec.py` run before commit (Rule 17); every heading Title Case
  (Rule 22).
- **Django system checks:** run `python manage.py check` after the migrations. The `card_focus_x/y`
  fields add no index, but the `ClassSettings.example_class` FK and the four `Member` fields do
  touch model state that `pytest` alone does not exercise.
