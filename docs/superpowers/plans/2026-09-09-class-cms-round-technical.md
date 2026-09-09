# Class CMS Round — Technical Spec

**Date:** 2026-09-09
**Branch:** `fog/class-cms-round`
**Companion:** `2026-09-09-class-cms-composer-and-instructor-marketing.md` (the UX design spec for the
instructor marketing page, the card positioning tool and previews, and the multi-step composer).
This file covers the model, view, form, permission and notification plumbing, plus the three smaller
asks that need no visual design.

Baseline verified in the tree at `VERSION` 1.45.1.

---

## 0. Scope

Six asks. The seventh (staging demo data for the CMS training) was pulled out of this round: the user
is staging it by hand from the UI. Worth knowing, and worth telling them once: the
`stage_training_demo` management command referenced in earlier notes **does not exist** in the working
tree, in any branch, or in either worktree. Only a stale
`core/management/commands/__pycache__/stage_training_demo.cpython-314.pyc` remains. There is nothing
to run.

| # | Ask | Verdict from research |
|---|---|---|
| 2 | Teaching sidebar always visible, marketing page for non-instructors, apply-to-teach | Build. Carries a **policy change** (§2). |
| 3 | Card image positioning, card preview, detail preview, draft save-and-continue | Partly built. Draft save works; detail preview exists. Card work is real, and there is a **bug** (§3.1). |
| 4 | Soften the review decision copy | Build. Small (§4). |
| 5 | Admin published to draft; admin override of guild lead | Half built. Override works; unpublish does not (§5). |
| 6 | Move-student dropdown scoping | Instructor side correct; admin side changes (§6). |
| 7 | Multi-step composer, sale out of the form into a modal | Build. The large one. Design in the companion spec; plumbing in §7. |

Everything lands as **one PR**, opened as a **draft**, after local screenshots are approved.

---

## 1. Established baseline (do not re-derive)

| Thing | Location |
|---|---|
| Stored status: DRAFT / PENDING / PUBLISHED / CANCELLED / ARCHIVED | `classes/models.py:638-643` |
| Derived `Lifecycle` + `lifecycle` property | `classes/models.py:645-655`, `:1806-1828` |
| `submit_for_review` / `approve` / `publish` / `cancel` / `withdraw_submission` / `request_change` / `archive` / `restore` | `classes/models.py:1023`, `:1088`, `:1115`, `:1166`, `:1259`, `:1279`, `:1340`, `:1358` |
| `ClassApproval.decide` — the single choke point for every decision | `classes/models.py:2273-2303` |
| Teaching gate `Member.can_create_classes` (driven by `instructor_oriented_at`) | `membership/models.py:1209-1218` |
| Public-page flag `Member.is_instructor` (driven by `instructor_slug`) | `membership/models.py:1204-1207` |
| `grant_instructor` / `revoke_instructor` (admin path, Permissions tab) | `membership/models.py:1319-1364`, view `hub/views.py:6398-6421` |
| Self-serve unlock (**being removed**, §2) | `classes/views.py:1174-1186`, `Member.complete_instructor_orientation` |
| `teaching_member_required` (403 for non-member, 302 to orientation for locked) | `classes/views.py:1002-1027` |
| Sidebar Teaching entry, hidden when locked | `hub/context_processors.py:60-84`, `templates/hub/base.html:124`, `:263` |
| `HeroCropMixin` fields + `hero_object_position` | `core/models.py:29-91` |
| Generic hero-adjust endpoint | `hub/views.py:377-420`, url `hub_hero_adjust` |
| Catalog card markup | `templates/classes/public/_list_results.html` |
| Detail preview (works, includes drafts) | `classes/views.py:2099-2150`, url `classes:class_preview` |
| `RegistrationMoveForm` destination queryset | `classes/forms.py:1233-1252` |
| Review decision form + page | `classes/forms.py:607-639`, `templates/classes/admin/class_review.html` |
| Event spine: `emit()`, registry, resolvers, copy | `core/events/` |
| Composer pattern to mirror | `templates/hub/announcement_compose.html`, `static/css/hub.css:4914-4925` |

---

## 2. Ask 2 — Teaching for everyone, and applying to teach

### 2.1 The policy change, stated plainly

Today **any active member can grant themselves teaching access**: open `/classes/teach/orientation/`,
tick the acknowledge toggle, press "Unlock teaching", and `instructor_oriented_at` is stamped on the
spot with no admin involvement (`classes/views.py:1174-1186`). The user has chosen to replace this
with an admin-approved application. This spec removes the self-unlock. Every future instructor waits
on an admin. That is the intent, confirmed, and it is the single riskiest change in the round because
it takes away a capability members have today.

### 2.2 Data

One new field on `Member` (`membership/models.py`), one migration:

```python
instructor_application_at = models.DateTimeField(
    null=True, blank=True,
    help_text="When this member asked to become an instructor. Cleared when teaching is revoked.",
)
```

An optional short note field is left to the design spec; if the design uses one, add
`instructor_application_note = models.CharField(max_length=300, blank=True, help_text=...)` in the
same migration.

Deliberately **no** declined state. The user asked for two visible states (applied, approved); an
admin who does not want to approve simply does not, and the member keeps seeing "applied". Adding a
decline path is scope the ask does not carry.

State resolution, in order:
1. `member.can_create_classes` is True — approved. The sidebar entry goes to the portal.
2. `instructor_application_at` is set — applied and waiting.
3. Otherwise — never applied.

### 2.3 Model methods

```python
def apply_to_teach(self) -> None:
    """Record this member's request to become an instructor and tell the admins."""
```
on `Member`. Guards, each raising `ValueError` with a plain message:
- `status != ACTIVE` — "Only active members can apply to teach."
- `can_create_classes` — "You can already teach."
- `instructor_application_at is not None` — "You have already applied."

Side effects: stamp `instructor_application_at = timezone.now()`, save with `update_fields`, log a
`SiteActivity` row, and `emit("instructor_application_submitted", ...)` with `period=f"member:{pk}:instructor_application"`
so a double submit cannot double notify.

`revoke_instructor()` (`membership/models.py:1319-1364`) additionally clears
`instructor_application_at`, so a revoked member can apply again. `grant_instructor()` leaves the
timestamp alone — it is the historical record of when they asked.

### 2.4 Notification

New `EventType` in `core/events/registry.py`, keyed `instructor_application_submitted`:
- `recipient=Recipients.FOG_ADMINS` (the ask says "fire off a notification to admin"; `fog_admins()`
  at `core/events/resolvers.py:95-119` is every `fog_role=ADMIN` member plus the configured notify
  addresses, and unlike `class_approvers()` it does not require a capability grant nobody has yet).
- `channels=(_IN_APP_ON, _EMAIL_ON)`.
- `url` points at the member's Permissions tab, so the admin's next click is the Instructor toggle
  that approves them.

Copy in `core/events/copy.py`. Per FRONTEND.md's email rules the subject noun (the member's name)
links to their member page, and the body names the one action: turn on Instructor in Permissions.
Both `.txt` and `.html`, kept in lockstep. No dashes in member-facing copy.

### 2.5 Views and routing

| Change | Where |
|---|---|
| `_teach_nav()` returns an entry for **every active member**, not only those who can teach. Label and destination flip: "Teaching" to the portal when `can_create_classes`, otherwise "Teaching" to the marketing page. | `hub/context_processors.py:60-84` |
| `teaching_member_required` redirects a locked member to `classes:teach_marketing` instead of `classes:teach_orientation`. Non-member and inactive still 403. | `classes/views.py:1002-1027` |
| New `teach_marketing` view, `@active_member_required`, renders the marketing page with the three states. | `classes/views.py`, url `teach/why-teach/` name `classes:teach_marketing` |
| New `teach_apply` view, `@active_member_required @require_POST`, calls `member.apply_to_teach()`, catches `ValueError` into a Django message, redirects back. | url `teach/apply/` name `classes:teach_apply` |
| `teach_orientation_complete` **deleted**, along with its url and `InstructorOrientationCompleteForm`. | `classes/views.py:1174-1186`, `classes/urls.py:21`, `classes/forms.py` |
| `teach_orientation` kept as the reference reading page for people who already teach; its acknowledge/unlock card is removed. Whether the marketing page absorbs its content is the design spec's call. | `classes/views.py:1162` |
| `Member.complete_instructor_orientation()` **deleted** if nothing else calls it; if the admin grant path uses it, keep it and only remove the self-serve view. Check `find_referencing_symbols` before deleting. | `membership/models.py` |

### 2.6 Tests

- `apply_to_teach`: each guard raises; success stamps the timestamp, logs activity, emits exactly
  once; a second call raises rather than re-emitting.
- `revoke_instructor` clears the application timestamp; `grant_instructor` does not.
- Sidebar: entry present for a plain active member (new behavior, regression on `#329`), absent for
  anonymous and inactive, destination flips on `can_create_classes`.
- `teaching_member_required`: locked member 302s to the marketing page, not the orientation.
- Marketing page renders all three states.
- Crafted POST: an inactive member posting to `teach_apply` gets the error, no timestamp.
- **e2e:** `tests/e2e/instructor_orientation_spec.py` is rewritten end to end. The old loop (gate to
  orientation to acknowledge toggle to "Unlock teaching" to "Teaching unlocked — welcome,
  instructor.") no longer exists. The new spec drives: locked member lands on the marketing page,
  presses Apply, sees the applied state, an admin grants Instructor, the member reloads and reaches
  the portal. Run it on Postgres 5433, not SQLite.

---

## 3. Ask 3 — Card positioning and previews

### 3.1 The bug to fix first

`templates/classes/public/_list_results.html` applies a stored position to the card only under
`{% if offering.hero_crop_w %}`. The slider tool on the detail page (`static/js/hero_placement.js`)
writes focal-point mode, which is `w=0, h=0`. Zero is falsy, so **every adjustment made with the
slider tool is silently ignored on the catalog card**. The condition must go: render
`style="object-position: {{ offering.hero_object_position }};"` unconditionally, since
`hero_object_position` already returns `50% 50%` when nothing is stored (`core/models.py:57-91`).

### 3.2 Card position is its own value

The detail hero is 16:9. The card is a fixed 150px-tall full-width strip (`.cls-media`,
`static/css/cms-public.css:464-465`). One stored crop cannot be correct for both, so the card gets its
own field pair on `ClassOffering`, in the same migration as §2:

```python
card_crop_x = models.PositiveIntegerField(null=True, blank=True, help_text="Horizontal focal point for the catalog card, as a percent.")
card_crop_y = models.PositiveIntegerField(null=True, blank=True, help_text="Vertical focal point for the catalog card, as a percent.")
```

Plus a `card_object_position` property returning `f"{x}% {y}%"` when both are set and `"50% 50%"`
otherwise. Percentages only, no pixel box: the card is a single fixed rectangle, so a focal point is
the whole decision and a Cropper.js box would be a heavier tool that answers the same question.

The card template reads `card_object_position`. The detail hero keeps reading `hero_object_position`.
Both fall back to centre, so nothing regresses for the 611 existing published classes.

Saving is an extension of the existing generic endpoint `hub_hero_adjust` (`hub/views.py:377-420`)
with a `target=card` parameter, reusing its permission checks verbatim rather than adding a second
endpoint with its own auth surface. If the design puts the tool inside the composer instead of on the
rendered page, the values ride the composer's own form POST and this endpoint change is unnecessary;
the design spec decides, and the builder implements exactly one of the two.

### 3.3 Previews

The detail preview already exists (`classes:class_preview`) and already renders drafts. Two gaps:
- It is only reachable once the class has a pk. Before first save there is nothing to preview; the
  design spec specifies that empty state.
- There is no card preview at all. The card preview must render **the real card markup**, not a
  lookalike: extract the card from `templates/classes/public/_list_results.html` into
  `templates/classes/public/_class_card.html`, include it from both the catalog and the composer, so
  the preview cannot drift from the catalog. This refactor is the only reason the preview is
  trustworthy, so it is not optional.

### 3.4 Tests

- `card_object_position`: both set, neither set, one set (falls back to centre).
- The catalog card applies focal-point mode (the regression that motivates §3.1): a class with
  `hero_crop_x/y` set and `w=0` renders a non-default `object-position` on the card.
- `_class_card.html` renders identically when included from the catalog and from the preview.
- Permission edges on the adjust endpoint if §3.2's endpoint route is taken.

---

## 4. Ask 4 — Softer review decision copy

The reviewer's decision UI reads as a verdict. It should read as a step in a conversation, because
classes routinely go round more than once.

| Where | Today | Becomes |
|---|---|---|
| `templates/classes/admin/class_review.html:125` submit button | `Submit decision` | `Send Response` |
| Same file, fieldset legend | `Decision` | `Your Response` |
| New line above the fieldset | — | `Most classes go through a round or two of notes before they go live. Asking for changes is normal and the instructor can resubmit as many times as they need.` |
| `classes/forms.py:610` choice label | `Request changes` | `Ask for changes` |
| `classes/forms.py:611` choice label | `Decline` | `Decline` (unchanged; declining genuinely is final) |
| `classes/forms.py:626` notes placeholder | `Optional on approve; required on request-changes and decline.` | `Optional when you approve. Required when you ask for changes or decline.` |
| `classes/forms.py:629-630` notes help text | as today | `Optional when you approve. Required when you ask for changes or decline, so the instructor knows what to work on.` |

`Approve` stays `Approve`: a guild lead approving escalates to an admin rather than publishing, so any
label promising publication would be a lie on half the surfaces this form renders on.

`classes/spec/views/class_review_spec.py:140` asserts `html.index("Submit decision")` and must be
updated. `grep -rn "Submit decision" tests/ templates/` returns no e2e hits, verified.

---

## 5. Ask 5 — Admin publish-to-draft, and the override that already works

### 5.1 Already works, nothing to build

An admin or a `CLASS_APPROVER` holder can publish a class whose guild lead has not approved.
`admin_class_approve` (`classes/views.py:2868-2890`) calls `approve()`, and
`on_review_decision_recorded` (`classes/models.py:1500-1541`) closes any still-open guild-lead gate as
auto-approved and publishes. This is documented in the PR body as confirmed, not rebuilt.

### 5.2 Unpublish is new

Today the only route from PUBLISHED back to DRAFT is Archive then Restore, two actions on two
screens, and Archive is **refused** for an upcoming class that has active registrations
(`archive_blocker`, `classes/models.py:1326-1338`) — exactly the class an admin most often wants to
pull. So the common case is currently impossible.

New model method on `ClassOffering`:

```python
def unpublish(self, actor) -> None:
    """Take a live class back to draft so it can be reworked and resubmitted."""
```
- Guard: `status != PUBLISHED` raises `ValueError("Only a published class can be taken back to draft.")`.
- Sets `status = DRAFT`, clears `published_at` and `approved_by`.
- Deletes every `ClassApproval` row, matching `restore()` — the class re-enters review from the top.
- Logs a new `CmsActivity.Kind.CLASS_UNPUBLISHED`.
- **Emits nothing.** This is quiet housekeeping, deliberately unlike `cancel()`, which is the
  member-facing event. Registrations are untouched and registrants are not told.

View `admin_class_unpublish`, `@classes_admin_access_required @require_POST`, url
`admin/<int:pk>/unpublish/`, name `classes:admin_class_unpublish`. Admin only, not `CLASS_APPROVER` —
it sits with Edit / Cancel / Archive / Restore in the admin-only block of
`templates/classes/admin/class_detail.html:92-118`, so a CMS Administrator never sees a control that
403s.

Confirm modal via `components/confirm_modal.html`, wording that names the blast radius honestly:

> Take this class back to draft? It comes out of the catalog straight away. The people already
> registered keep their spots and are not emailed. You will need to submit it for review again before
> it can go live.

When the class has active registrations the modal adds the count. The action does not appear on a
completed or cancelled class.

### 5.3 Tests

- `unpublish`: from PUBLISHED works; from DRAFT / PENDING / CANCELLED / ARCHIVED raises; approval rows
  deleted; `published_at` and `approved_by` cleared; activity logged; **zero** events emitted
  (assert no `class_cancelled`, no `class_published`).
- The class drops out of `public()` and `bookable()` immediately; existing registrations survive with
  their status untouched.
- Resubmitting after unpublish walks the full pipeline again (`review_pipeline()` resets).
- Permission edges by crafted POST: plain member 403, instructor 403, `CLASS_APPROVER` holder 403.
- The button renders for an admin on a published class and not on a draft, completed or cancelled one.

---

## 6. Ask 6 — Move-student destination scoping

Verified behavior of `RegistrationMoveForm.__init__` (`classes/forms.py:1233-1252`):

```python
if instructor is not None:
    offerings = ClassOffering.objects.bookable().filter(instructor=instructor)
else:
    offerings = ClassOffering.objects.upcoming()
```

- **Instructor side is already exactly right** and needs no change: `bookable()` is published,
  non-private, not yet started, and it is filtered to their own classes.
- **Admin side is broader than asked.** `upcoming()` applies no status or visibility gate, so drafts,
  private classes and unscheduled classes are all offered.

The decision taken: **private classes stay, drafts go.** Parking a student in a private class is a
real thing an admin does; parking one in a draft is not, because the student's class page would point to
something that is not live.

```python
offerings = ClassOffering.objects.upcoming().filter(status=ClassOffering.Status.PUBLISHED)
```

`upcoming()` keeps the not-yet-happened constraint. Private classes survive because the filter adds
only a status condition and never routes through `public()`. Admin overfill stays allowed —
`clean_target()` only rejects a full destination when `self._instructor is not None`
(`classes/forms.py:1258-1263`), which the ask did not touch.

The docstring at `classes/forms.py:1211-1220` currently states the old asymmetry as deliberate and
must be rewritten to state the new one.

`admin_registration_move` (`classes/views.py:3576-3588`) builds the same admin-scoped form and
inherits the change for free.

### 6.1 Tests

- Admin destination list: includes a published private class, includes a published public class,
  **excludes a draft**, excludes a class already started, excludes the source class itself.
- Instructor destination list unchanged (regression block): own published non-private future classes
  only, another instructor's published class absent.
- Admin may still overfill; an instructor still cannot.

---

## 7. Ask 7 — Composer plumbing

Visual design lives in the companion spec. The plumbing that spec depends on:

### 7.1 Sale comes out of the class form

- `_SaleMixin` (`classes/forms.py:209-283`) is removed from `ClassOfferingForm` and
  `TeachClassOfferingForm`, and the six `sale_*` fields drop out of both `Meta.fields`.
- A new `ClassSaleForm` carries them alone, reusing `_SaleMixin`'s validation **unchanged** — the
  Stripe floor check and the free-class check are the reason this validation exists and must not be
  reimplemented.
- New views `teach_class_sale` (GET the modal body, POST to save) and the admin twin, scoped by the
  same `_teach_class_or_404` / admin decorators as the rest of the workspace.
- `templates/classes/_components/class_sale_section.html` is deleted from the form templates and its
  markup moves into the modal body partial.
- The sale pill on the manage-class page keys off the existing `sale_is_active` property
  (`classes/models.py:1635-1677`); no new model state.

### 7.2 The composer is one form, not a wizard

Following the announcement composer verbatim: one page, one `<form>`, one Alpine `x-data` holding a
`phase` integer, `x-show` per step, and the `.pl-phase-tabs` / `.pl-phase-tab` bar. **No
`SessionWizardView`, no `django-formtools`, no per-step POST.** The `ClassOffering` draft row is the
persistence layer that `AnnouncementDraft` is for the composer, and it already works.

Consequences the builder must respect:
- Every field of every step is in the DOM on every render, so server-side validation is unchanged and
  a single POST still saves everything. Errors on a step the user is not looking at must be surfaced
  on the step bar; the design spec specifies how.
- Rule 12: never put `display` in an inline style on an `x-show` element. Step bodies get a CSS class
  that provides the real display value.
- The formsets (sessions, gallery, FAQ) keep their existing components and their `extra=0` plus
  "+ Add" template-clone pattern. Cloned rows must be driven by a **delegated** listener bound to the
  rows container, per Rule 16, because cloned `innerHTML` never executes inline scripts.

### 7.3 Step indicator versus review pipeline

There are two different progress ideas and they must not be conflated: the composer's step bar is
"where am I in filling this out", and `review_pipeline()` is "where is this class in getting
approved". They are separate components with separate meanings. The design spec states the
relationship; the builder does not invent one.

### 7.4 Tests

- Every field that exists today still saves through the new template (a field silently dropped from
  `Meta.fields` during the split is the likeliest regression, so assert the full round trip).
- The sale form validates identically to the old inline path: free class plus sale, missing price,
  percent out of range, fixed amount at or above price, and the Stripe floor dead zone.
- A sale saved through the modal shows the pill; switching it off removes it.
- Crafted POST to the sale endpoint by a non-owner instructor 404s.
- `manage.py check` after the model changes — CI runs system checks that local pytest skips, and the
  index-name 30 character cap has bitten this repo before.
- `tests/template_comment_lint_spec.py` after every template change.
- `tests/e2e/screenshots_spec.py` covers `teach_class_create` and `teach_class_edit`; both are
  rebuilt here, so run that spec on Postgres 5433 before pushing.

---

## 8. Build order

One branch, one PR, opened as a draft after screenshots are approved.

1. Migration and model layer: `Member.instructor_application_at`, `ClassOffering.card_crop_x/y` and
   `card_object_position`, `unpublish()`, `CmsActivity.Kind.CLASS_UNPUBLISHED`, `apply_to_teach()`,
   the new event type and copy.
2. Asks 4, 5, 6 — small, self-contained, land them early so the diff has a green spine.
3. Ask 2 — marketing page, apply flow, sidebar, removal of the self-serve unlock, e2e rewrite.
4. Ask 3 — card focal point, the `_class_card.html` extraction, the previews.
5. Ask 7 — the composer and the sale modal, last, because it is the piece most likely to churn.
6. `VERSION` bump and one curated changelog entry, written after everything else so it describes what
   actually shipped. Next free minor at commit time, not the number remembered from today.

## 9. Gates before pushing

- `ruff format . && ruff check .`
- `DATABASE_URL="sqlite:///tmp-check.sqlite3" .venv/bin/python manage.py check` (delete the file after)
- `tests/template_comment_lint_spec.py`
- The targeted specs for every section above
- `tests/e2e/instructor_orientation_spec.py` (rewritten) and `tests/e2e/screenshots_spec.py`, both on
  `postgres://plfog:plfog@localhost:5433/plfog`, because browser-writing e2e flakes on local SQLite
- The pre-push hook runs real mypy; full annotations, no bypass
