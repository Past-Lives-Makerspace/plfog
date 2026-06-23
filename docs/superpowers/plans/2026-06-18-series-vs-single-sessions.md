# Series vs Single Sessions — Distinguishing Class Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is the largest plan in the current set and **touches real payments** — read the "Risks & rollout" subsection before writing any checkout code, and run every Stripe-touching task with mocked Stripe (never live keys).

**Goal:** Let an operator mark a class as either a **Single Session** (the existing behaviour — one date, one seat, one payment) or a **Series Package** (one purchase enrolls the registrant in *all* of that class's scheduled dates under a **single Stripe payment**, e.g. "Blacksmithing 101 — 3-Part Multi-Day"). Public cards and the detail page must render a distinct badge — "Series Package" vs "Single Session" — so a visitor knows what they're buying before they click Register.

**The defect / gap (today):** A `ClassOffering` already owns many `ClassSession` rows (`classes/models.py:718–734`, `related_name="sessions"`), and the catalog already groups *same-title* offerings across separate dates into one card via `grouping_key` (`classes/grouping.py`, `classes/models.py:229–237`). But there is **no `scheduling_type`** distinguishing "this one offering's many sessions are a single package you buy once" from "each date is its own thing". Registration is 1:1 to one `ClassOffering` (`classes/models.py:913–918`) and checkout creates one Stripe Checkout Session for one offering's price (`classes/views.py:521–542`). So a multi-week course is today only expressible as *either* one offering with several `ClassSession` rows (which already bills once, but is indistinguishable from a single class with one optional makeup date and carries no badge), *or* as several grouped offerings (which bill separately, once per date). Neither communicates "buy once → attend all dates" to the buyer, and nothing renders a Series/Single badge.

> **Do not conflate with `SchedulingModel`.** `ClassOffering.SchedulingModel` (`classes/models.py:143–145`) is a *different* axis: FIXED ("Fixed sessions") vs FLEXIBLE ("arrange with instructor"). It answers "are dates fixed or arranged per-student?" — orthogonal to "is this one purchase a series or a single?". The "Flex" badge at `templates/classes/public/_list_results.html:42` is for `scheduling_model == 'flexible'` and stays. We are adding a **new** enum, `scheduling_type`.

**Architecture:** Reuse the existing one-offering-owns-many-`ClassSession` relationship as the series container. Add `scheduling_type` (`SINGLE_SESSION` / `SERIES_PACKAGE`) to `ClassOffering`. For a `SERIES_PACKAGE`, one `Registration` + one `create_class_checkout_session` call (priced at the offering's `price_cents`) grants attendance to every session the offering owns — capacity/seat accounting is already per-offering (`spots_remaining`, `classes/models.py:505–510`), so a series consumes one seat across all its dates with zero new capacity machinery. This is option **(A)** below; the full trade-off analysis and the rejection of option (B) are in "Decisions baked into this plan". This plan is large and is sequenced so it **can land as one focused branch** (`series-vs-single`) but the tasks are grouped so a reviewer could split it into 2 PRs (PR-1: model + builder + badges; PR-2: checkout semantics + reminders) if desired — write it as one coherent plan and bump the version once.

**Tech Stack:** Django 5 models/migrations, `factory-boy` factories, `pytest` + `pytest-describe` (`*_spec.py` in `classes/spec/`), `unittest.mock.patch` for the Stripe boundary (the established pattern — `classes/spec/views/register_spec.py:121`), Django templates + the existing `cls-card` / `cp-detail` CSS, `ruff` (line-length 120) + `mypy`. No new dependencies. Stripe stays in test mode throughout.

---

## Background / context for the implementer

### The model as it exists today (verified)
- `ClassOffering` — `classes/models.py:136–299`. Fields of note: `price_cents` (`:167`), `capacity` default 6 (`:169`), `scheduling_model`/`flexible_note` (`:170–176`), `grouping_key` (`:229–237`), `status` lifecycle (`:137–141`, `:198–200`). `save()` recomputes `grouping_key` from title+category on every save (`:277–284`) and syncs siblings when category changes (`:286–294`).
- `ClassOffering.spots_remaining` — `classes/models.py:505–510` — `capacity` minus CONFIRMED+PENDING registrations. **Per offering.** Batched variant `spots_remaining_map()` at `classes/models.py:119–133`.
- `ClassSession` — `classes/models.py:718–734`. 1:many child of `ClassOffering` via `related_name="sessions"`. Has `starts_at`, `ends_at`, `sort_order`. `CheckConstraint` ends>starts (`:731`). **This is the series container we reuse.**
- `Registration` — `classes/models.py:905–1125`. `class_offering` FK is `on_delete=PROTECT`, `related_name="registrations"` (`:913–918`). 1:1 with one offering. `amount_paid_cents` (`:947`), `status` enum incl. REFUNDED (`:906–911`), `stripe_session_id`/`stripe_payment_id` (`:954–955`), `self_serve_token` (`:956–961`). `cancel()` at `:1094–1110` flips status, logs activity, and calls `promote_next_from_waitlist()`. **There is NO real Stripe refund anywhere** — `admin_registration_cancel` (`classes/views.py:2000–2006`) calls `registration.cancel()` which sets CANCELLED; the REFUNDED status is only ever reached by an external/manual status set and logs `REGISTRATION_REFUNDED` (`classes/models.py:1050–1061`). Confirmed: `grep` for `refunds.create` / `create_refund` returns nothing in `classes/` or `billing/`. **Refunds are a Stripe-dashboard manual action today; this plan does not change that, but documents the series implication.**
- `grouping_key` — `classes/grouping.py:17–28`. Slugified date-stripped title + category id. Built on save. `_grouped_catalog()` (`classes/views.py:106–118`) collapses offerings sharing a key into one `_CatalogGroup` (`classes/views.py:83–103`) with `.representative`, `.members`, `.date_count`, `.is_multi`. The detail page lists `sibling_offerings` sharing the key (`classes/views.py:277–293`).

### The checkout path (verified — the one place money moves)
- `register` view, paid branch — `classes/views.py:477–542`. On a valid POST it calls `form.save()` → `Registration` (one row), computes `final_price = form.compute_final_price_cents()`, and if non-zero calls `stripe_utils.create_class_checkout_session(amount_cents=final_price, product_name=offering.title, …, metadata={"registration_id", "class_slug", "kind": "class_registration"}, idempotency_key=f"class-checkout-reg-{registration.pk}")` (`:521–534`). On Stripe error it **deletes the half-created registration** (`:535–537`) — keep this rollback for series. Free classes (`final_price == 0`) confirm immediately, no Stripe (`:481–507`).
- `create_class_checkout_session` — `billing/stripe_utils.py:162–203`. Single line item, `mode="payment"`, `unit_amount=amount_cents`, metadata copied onto the PaymentIntent. **A series is still ONE line item at the series price — no Stripe-side change needed.**
- Webhook confirm — `classes/webhook_handlers.py:27–106`. On `checkout.session.completed` with `kind=class_registration` and `payment_status=paid`, loads the registration `select_for_update` by `metadata["registration_id"]`, flips to CONFIRMED, stamps `stripe_payment_id`, sets `amount_paid_cents = session.amount_total`, bumps discount use, sends confirmation/instructor/admin emails + Mailchimp. **Because a series is still one Registration with one metadata id, the webhook needs NO structural change** — but the confirmation email copy should reflect "all N sessions". See Task 6.

### The class builder (where Series/Single is chosen)
- `ClassOfferingForm` (admin) — `classes/forms.py:182–231`. Includes `scheduling_model`, `flexible_note` (`:202–203`). We add `scheduling_type`.
- `TeachClassOfferingForm` (member/teach) — `classes/forms.py:234–293`. Same — add `scheduling_type`.
- `ClassSessionForm` — `classes/forms.py:304–311`. The per-session date/time form. Sessions are edited via a JS month-view calendar (`sessions_json` is serialized into `class_form.html`, `classes/views.py:1480–1497`). **No new session model is needed** — the existing session calendar already attaches multiple dates to one offering. The builder change is: add the Series/Single toggle, and (Task 3) help text + a guard so a SINGLE_SESSION offering doesn't silently carry many dates that a buyer would expect to attend on one ticket.
- The session-calendar JS and `sessions_json` round-trip already work (changelog 2.5.4). We are not touching the calendar widget — only adding the type toggle above it.

### The public templates (where the badge renders)
- Card — `templates/classes/public/_list_results.html`. Title row at `:37–43` already renders the "Flex" badge (`:42`). The multi-date "Pick a date" block (`:50–79`) is the `grouping_key` group view; the single-offering schedule block is `:82–106`. **Badge goes in the title row next to "Flex".**
- Detail — `templates/classes/public/detail.html`. Category/pill row at `:129–138` (`cp-detail__cat-row`, `cp-detail__next-pill`). Schedule section at `:189–205`. **Badge goes in `cp-detail__cat-row`; the schedule `<h2>` already shows total duration for multi-session.**

### Tests & fixtures (where new specs go)
- Specs live in `classes/spec/` mirroring source: `models/`, `views/`, `forms/`, `templatetags/`. Factories in `classes/factories.py` — `ClassOfferingFactory:75`, `ClassSessionFactory:99`, `RegistrationFactory:117`.
- Stripe is mocked with `@patch("billing.stripe_utils.create_class_checkout_session")` returning `{"id":…, "url":…}` (`classes/spec/views/register_spec.py:121–138`); the rollback path is tested by `side_effect=RuntimeError` (`:140–142`). **Reuse this exact pattern** — do NOT introduce respx for Stripe here (respx is the standard for raw HTTP services; Stripe is already wrapped behind `stripe_utils`, so patch the wrapper). Webhook specs build a fake event dict and patch emails (`classes/spec/webhook_handlers_spec.py`).

### Version (verified)
- `main` is at `VERSION = "2.5.7"` (`git show main:plfog/version.py`). PR #108 (`release-2.5.8`) is **open/in flight**. The working tree's `plfog/version.py:5` already reads `2.5.8`. **The next available patch is `2.5.9`** — use it (Task 9). Do not assume; re-verify with `git show main:plfog/version.py` before committing the bump in case 2.5.8 has merged and a newer number is in flight.

---

## Decisions baked into this plan

### KEY DECISION — Architecture (A) vs (B): use (A), reuse `ClassSession`

Two viable shapes were considered. **(A) is chosen.**

**(A) — `scheduling_type` on `ClassOffering`; the existing `sessions` are the series.**
One purchase → one `Registration` → grants all of that offering's `ClassSession` rows. Capacity is already per-offering, so a 3-week course with capacity 6 has 6 seats total, each seat covering all 3 weeks — exactly the desired semantic with **zero new capacity code**.

**(B) — A new wrapper entity (`ClassSeries`) linking several `ClassOffering` rows under one payment.**
Mirrors how `grouping_key` links dated siblings. One purchase → registrations against several offerings → one payment.

| Axis | (A) reuse `ClassSession` | (B) new wrapper over offerings |
|---|---|---|
| **Data-model churn** | One enum field + migration. `Registration`, capacity, webhook, reminders all unchanged in shape. | New `ClassSeries` model, M2M/FK from `ClassOffering`, a join, and either a new `SeriesRegistration` or N `Registration` rows per purchase. Large surface. |
| **Capacity semantics** | Trivial — `spots_remaining` is already per-offering; a series seat = one offering seat. | Hard — capacity now lives on the *wrapper* OR must be reconciled across N offerings (what if dates have different capacities? what if one date sells out?). Requires "min seat across offerings" logic and partial-availability rules. |
| **Single payment** | Natural — one `Registration`, one Checkout Session, one `registration_id` in metadata. Webhook unchanged. | Awkward — one payment must confirm N registrations atomically; webhook must fan out by series id and handle partial failure. |
| **Refund / cancel** | One `Registration.cancel()` releases the one series seat; `promote_next_from_waitlist` already works per-offering. | Must cancel N rows transactionally; refund is one Stripe charge but N rows — partial cancel is ambiguous. |
| **Interaction with `grouping_key`** | Clean separation: `grouping_key` keeps doing "same single class on many *separate, independently-bookable* dates" (the legacy-import case); `scheduling_type=SERIES` is "*one* offering whose sessions are a package". A series offering is NOT grouped with anything (it stands alone — see guard in Task 3). | Direct collision: (B) *is* a second grouping mechanism over offerings, competing with `grouping_key`. Would need one to subsume the other → migration risk on live data. |
| **Reporting** | Series registrations appear like any other registration, filterable by offering. | Reports must learn the wrapper to avoid double-counting N offerings as N sales. |
| **Reminders** | `send_class_reminders` already iterates an offering's sessions and dedupes via `RegistrationReminder(registration, session)` unique constraint (`classes/models.py:880–899`) — a series registrant naturally gets a reminder per session. | Must walk the wrapper to find sessions. |

**Verdict:** (A) reuses the most existing structure, adds the least churn, and keeps `grouping_key` and `scheduling_type` as two clean, non-overlapping concepts. (B) reinvents grouping and pushes complexity into capacity, payment fan-out, and refunds. **Choose (A).** The single durable move is one enum field.

### Other decisions
- **`scheduling_type` is independent of `scheduling_model`.** A class can be `SERIES_PACKAGE` + `FIXED` (a 3-week fixed course — the main use case) or `SINGLE_SESSION` + `FLEXIBLE` (a one-off arranged 1:1). `SERIES_PACKAGE` + `FLEXIBLE` is allowed but odd; we do not forbid it (YAGNI) — the badge logic just shows "Series Package" and the existing Flex badge can coexist.
- **Default for existing rows = `SINGLE_SESSION`.** This is the safe, behaviour-preserving default: every offering today bills once for its (usually one) session, which is exactly Single semantics. The data migration sets all existing rows to SINGLE explicitly (with a reverse function that is a no-op-but-defined — see Task 1). **No existing offering's price or capacity changes.**
- **Capacity for a series is the offering's existing `capacity`** — unchanged. One seat = the whole series. No per-session capacity.
- **Partial availability does not apply to (A).** Because a series is one seat on one offering, there is no "some dates full, some open" state to handle — the offering is either has-spots or sold-out as a whole. (This is the simplicity payoff of (A) and is called out as an explicit non-feature so a reviewer doesn't ask "where's partial-availability handling?".)
- **A `SERIES_PACKAGE` offering opts out of `grouping_key` collapsing.** A series is a self-contained card; it must never be merged with same-title siblings (that would imply "pick a date", which contradicts "buy once for all dates"). Implemented by making `grouping_key` blank for series offerings (Task 3, Step 2) so `_grouped_catalog` treats it as solo (`offering.grouping_key or f"solo:{offering.pk}"`, `classes/views.py:111`).
- **Series requires ≥1 session to make sense, and the price is the whole-series price.** The price field already means "full price" (`price_cents` help_text `:167`); for a series the operator enters the total package price. No per-session pricing (YAGNI). Builder help text makes this explicit (Task 3).
- **No real Stripe refund is added.** Refund-of-a-series is identical to refund-of-a-single today: a manual Stripe-dashboard refund + setting status REFUNDED. Documented in Risks; out of automated scope.
- **Webhook + Stripe util are unchanged in shape.** One series = one Checkout Session = one registration id. Only `product_name` passed to Stripe gains a "(N-session series)" suffix for clearer line-item descriptions on the receipt (Task 5, Step 2) — a cosmetic, non-load-bearing change.

---

## File Structure

- Modify: `classes/models.py` — add `ClassOffering.SchedulingType` TextChoices + `scheduling_type` field (`~:142`, next to `SchedulingModel`); add `is_series` / `is_single` helper properties and a `series_session_count` property; adjust `save()` grouping-key logic so series offerings don't group (`:277–284`).
- New migration: `classes/migrations/XXXX_classoffering_scheduling_type.py` — `AddField` (default SINGLE_SESSION) **+** a `RunPython` data step (with a defined reverse) regrouping series offerings to blank keys. (One logical change; keep schema add and data backfill in this single migration since the backfill depends on the new field.)
- Modify: `classes/forms.py` — add `scheduling_type` to `ClassOfferingForm.Meta.fields` (`:187`) and `TeachClassOfferingForm.Meta.fields` (`:241`); add a `clean()` guard (Task 3).
- Modify: `classes/views.py` — paid checkout branch (`:509–542`): pass a series-aware `product_name`; (no metadata/structural change). Optionally surface `is_series` in `register`/`public_class_detail`/`public_list` contexts if the template needs it (templates can read `offering.scheduling_type` directly — prefer that, no view change).
- Modify: `templates/classes/public/_list_results.html` — badge in the title row (`:37–43`).
- Modify: `templates/classes/public/detail.html` — badge in `cp-detail__cat-row` (`:129–138`).
- Modify: `classes/factories.py` — `ClassOfferingFactory` default `scheduling_type` (`:75–87`) + a `SeriesClassOfferingFactory` (or trait) for convenience.
- Modify: `classes/emails.py` — confirmation copy reflects N sessions for a series (Task 6) — verify the template/sender first.
- New specs: `classes/spec/models/class_offering_series_spec.py`, `classes/spec/views/register_series_spec.py`, `classes/spec/forms/class_offering_form_series_spec.py` (path mirrors existing forms specs — confirm `classes/spec/forms/` layout), `classes/spec/templatetags/` or `classes/spec/views/public_spec.py` addition for the badge render.
- Modify: `plfog/version.py` — version bump + member-friendly changelog (Task 9).

---

## Task 1: Add the `scheduling_type` field + data migration

**Files:** `classes/models.py`, new migration.

- [ ] **Step 1 (failing test):** In `classes/spec/models/class_offering_series_spec.py` write:
  ```python
  from classes.factories import ClassOfferingFactory
  from classes.models import ClassOffering

  def describe_ClassOffering_scheduling_type():
      def it_defaults_to_single_session(db):
          offering = ClassOfferingFactory()
          assert offering.scheduling_type == ClassOffering.SchedulingType.SINGLE_SESSION

      def describe_is_series():
          def it_is_true_for_series_package(db):
              offering = ClassOfferingFactory(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE)
              assert offering.is_series is True
              assert offering.is_single is False

          def it_is_false_for_single_session(db):
              assert ClassOfferingFactory().is_series is False
  ```
- [ ] **Step 2 (confirm fail):** `pytest classes/spec/models/class_offering_series_spec.py` → fails (`AttributeError`/no field).
- [ ] **Step 3 (implement):** In `classes/models.py`, right after `SchedulingModel` (`:143–145`), add:
  ```python
  class SchedulingType(models.TextChoices):
      SINGLE_SESSION = "single_session", "Single Session"
      SERIES_PACKAGE = "series_package", "Series Package"
  ```
  Add the field next to `scheduling_model` (`:170–176`):
  ```python
  scheduling_type = models.CharField(
      max_length=20,
      choices=SchedulingType.choices,
      default=SchedulingType.SINGLE_SESSION,
      help_text=(
          "Single Session: one date, one seat, one payment. "
          "Series Package: one purchase enrolls the registrant in every "
          "scheduled date of this class under a single payment."
      ),
  )
  ```
  Add helper properties (near `spots_remaining`, `:505`):
  ```python
  @property
  def is_series(self) -> bool:
      return self.scheduling_type == self.SchedulingType.SERIES_PACKAGE

  @property
  def is_single(self) -> bool:
      return self.scheduling_type == self.SchedulingType.SINGLE_SESSION

  @property
  def series_session_count(self) -> int:
      """Number of sessions a series ticket covers (0 for none scheduled yet)."""
      return self.sessions.count()
  ```
- [ ] **Step 4:** `python manage.py makemigrations classes` — produces an `AddField`. Then **hand-add a data step** in the *same* migration (one logical change), with a defined reverse:
  ```python
  def backfill_single_session(apps, schema_editor):
      ClassOffering = apps.get_model("classes", "ClassOffering")
      ClassOffering.objects.filter(scheduling_type="").update(scheduling_type="single_session")

  def reverse_backfill(apps, schema_editor):
      # No-op-but-defined: the field default already covers a down-migration;
      # we never want to wipe a deliberately-set value on reverse.
      pass
  ```
  Wire `migrations.RunPython(backfill_single_session, reverse_backfill)` after the `AddField`. (The `AddField` default already populates rows; the RunPython is belt-and-suspenders for any row written between schema add and deploy, and gives the migration an explicit, reviewable backfill. The reverse is intentionally a no-op — note this in the docstring so a future reader knows it was deliberate, per the standards rule against unexplained `noop` reverses.)
- [ ] **Step 5 (confirm pass):** `pytest classes/spec/models/class_offering_series_spec.py` → green. `python manage.py migrate` clean; `python manage.py migrate classes <prev>` reverses clean.
- [ ] **Step 6 (lint+commit):** `ruff format . && ruff check . && mypy .` (export `DATABASE_URL` first for mypy), then commit `feat(classes): add scheduling_type (series vs single) to ClassOffering`.

---

## Task 2: Factory support for series offerings

**Files:** `classes/factories.py`, `classes/spec/models/class_offering_series_spec.py`.

- [ ] **Step 1 (failing test):** Add to the series spec:
  ```python
  from classes.factories import SeriesClassOfferingFactory

  def it_factory_builds_a_three_session_series(db):
      offering = SeriesClassOfferingFactory(session_count=3)
      assert offering.is_series
      assert offering.series_session_count == 3
  ```
- [ ] **Step 2 (confirm fail):** import error / no factory.
- [ ] **Step 3 (implement):** In `classes/factories.py`, set `ClassOfferingFactory.scheduling_type = models.ClassOffering.SchedulingType.SINGLE_SESSION` (explicit, matches default) and add:
  ```python
  class SeriesClassOfferingFactory(ClassOfferingFactory):
      scheduling_type = models.ClassOffering.SchedulingType.SERIES_PACKAGE

      @factory.post_generation
      def session_count(self, create, extracted, **kwargs):
          if not create or not extracted:
              return
          base = timezone.now()
          for i in range(extracted):
              ClassSessionFactory(
                  class_offering=self,
                  starts_at=base + timedelta(days=7 * i),
                  ends_at=base + timedelta(days=7 * i, hours=2),
              )
  ```
  (Confirm `timezone`/`timedelta` are already imported in `factories.py` — `ClassSessionFactory` uses both.)
- [ ] **Step 4 (confirm pass):** spec green.
- [ ] **Step 5 (lint+commit):** ruff/mypy clean; commit `test(classes): SeriesClassOfferingFactory`.

---

## Task 3: Builder — choose Series/Single + grouping guard

**Files:** `classes/forms.py`, `classes/models.py`, templates `class_form.html` (admin) and the teach class form template.

- [ ] **Step 1 (failing test):** In `classes/spec/forms/class_offering_form_series_spec.py` (confirm `classes/spec/forms/` exists — it holds the forms specs):
  ```python
  from classes.forms import ClassOfferingForm
  from classes.models import ClassOffering

  def describe_ClassOfferingForm_scheduling_type():
      def it_exposes_the_scheduling_type_field(db):
          assert "scheduling_type" in ClassOfferingForm().fields

      def it_saves_a_series_offering(db, ...):  # build minimal valid POST data; mirror existing form spec setup
          ...
          assert offering.scheduling_type == ClassOffering.SchedulingType.SERIES_PACKAGE
  ```
  And a model spec asserting the grouping guard:
  ```python
  def describe_series_grouping():
      def it_does_not_group_series_offerings(db):
          offering = SeriesClassOfferingFactory(title="Blacksmithing 101", session_count=3)
          offering.refresh_from_db()
          assert offering.grouping_key == ""
  ```
- [ ] **Step 2 (confirm fail):** form has no field; series gets a non-empty grouping_key.
- [ ] **Step 3 (implement form):** Add `"scheduling_type"` to `ClassOfferingForm.Meta.fields` (after `scheduling_model`, `classes/forms.py:202`) and `TeachClassOfferingForm.Meta.fields` (`:254`).
- [ ] **Step 4 (implement grouping guard):** In `ClassOffering.save()` (`classes/models.py:277–284`), make a `SERIES_PACKAGE` offering stand alone:
  ```python
  from classes.grouping import grouping_key_for
  self.grouping_key = "" if self.is_series else grouping_key_for(self.title, self.category_id)
  ```
  Keep the existing `update_fields` augmentation. **Verify** the sibling-category-sync block (`:286–294`) is harmless for series (it only fires when `old.grouping_key` was non-empty; a series always had/has blank, so it won't sweep siblings — confirm with a test).
- [ ] **Step 5 (implement template toggle):** In the admin `class_form.html` and the teach form template, render `{{ form.scheduling_type }}` above the session calendar with help text: *"Series Package: one purchase covers all the dates you schedule below. Single Session: each ticket is for one date."* Place it near `scheduling_model`. (Find the current `scheduling_model` widget placement and mirror it — grep the template for `scheduling_model`.)
- [ ] **Step 6 (confirm pass):** specs green.
- [ ] **Step 7 (lint+commit):** ruff/mypy; commit `feat(classes): builder toggle for series vs single + series stands alone in catalog`.

---

## Task 4: Card badge — "Series Package" / "Single Session"

**Files:** `templates/classes/public/_list_results.html`, a view/template spec.

- [ ] **Step 1 (failing test):** In `classes/spec/views/public_spec.py` (or a new `public_series_badge_spec.py`):
  ```python
  def it_shows_a_series_badge_on_series_cards(db, client):
      offering = SeriesClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, session_count=3)
      resp = client.get(reverse("classes:public_list"))  # confirm public list URL + that PUBLISHED shows
      assert "Series Package" in resp.content.decode()
  ```
  (Mirror how `public_spec.py` sets up a browsable/published offering with upcoming sessions so the card actually renders.)
- [ ] **Step 2 (confirm fail):** badge absent.
- [ ] **Step 3 (implement):** In the title row (`:37–43`), after the title and the existing Flex badge (`:42`), add:
  ```django
  {% if offering.scheduling_type == 'series_package' %}<span class="badge series">Series Package</span>{% else %}<span class="badge single">Single Session</span>{% endif %}
  ```
  Add `.badge.series` / `.badge.single` styles next to the existing `.badge.fx` rule (grep CSS for `.badge.fx` — likely `cms-public.css`). Keep them visually distinct: series = gold/filled, single = subtle/outline. Confirm the design matches FRONTEND.md tokens.
  > Decide with the operator whether "Single Session" should render on *every* single card (could be noisy) or only series get a badge. **Default in this plan: show both** so the distinction is explicit (spec asks for "distinct badges: Series Package vs Single Session"). If it reads cluttered in the manual pass (Task 8), drop the single badge to series-only and note it in the changelog.
- [ ] **Step 4 (confirm pass):** spec green.
- [ ] **Step 5 (lint+commit):** ruff/mypy; commit `feat(classes): series/single badge on catalog cards`.

---

## Task 5: Checkout — one payment enrolls the whole series

**Files:** `classes/views.py` (`:509–542`), spec.

This is the **payments task** — read "Risks & rollout" first. Architecturally (A) means **almost nothing changes**: one series = one `Registration` = one Checkout Session at `price_cents`. The capacity check (`spots_remaining`) already governs the whole offering. The only changes are (1) a clearer Stripe line-item name and (2) tests proving the semantics.

- [ ] **Step 1 (failing test):** In `classes/spec/views/register_series_spec.py`, mirroring `register_spec.py:121`:
  ```python
  from unittest.mock import patch

  @patch("billing.stripe_utils.create_class_checkout_session")
  def it_charges_the_series_price_once_for_all_sessions(mock_checkout, db, client):
      mock_checkout.return_value = {"id": "cs_test_series", "url": "https://checkout.stripe.com/c/pay/x"}
      offering = SeriesClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, price_cents=15000, capacity=6, session_count=3)
      # POST a valid registration (reuse register_spec's valid-form helper)
      resp = client.post(reverse("classes:register", kwargs={"slug": offering.slug}), data=...)
      assert resp.status_code == 302
      kwargs = mock_checkout.call_args.kwargs
      assert kwargs["amount_cents"] == 15000  # series price, charged ONCE
      assert "series" in kwargs["product_name"].lower()
      # exactly one registration created (not one-per-session)
      assert offering.registrations.count() == 1

  @patch("billing.stripe_utils.create_class_checkout_session")
  def it_consumes_one_seat_for_the_whole_series(mock_checkout, db, client):
      ...  # after a confirmed series reg, spots_remaining drops by exactly 1
  ```
  Also add a capacity/sold-out test: a series at capacity routes to the waitlist branch like any offering (the existing `is_waitlist` logic, `classes/views.py:466–475`, is per-offering and needs no change — assert it still works for a series).
- [ ] **Step 2 (confirm fail / then implement):** In the paid branch (`classes/views.py:521–534`), make `product_name` series-aware:
  ```python
  product_name = offering.title
  if offering.is_series and offering.series_session_count > 1:
      product_name = f"{offering.title} ({offering.series_session_count}-session series)"
  ...
  checkout = stripe_utils.create_class_checkout_session(amount_cents=final_price, product_name=product_name, ...)
  ```
  Leave metadata, idempotency key, and the rollback-on-error (`:535–537`) exactly as-is. **Do not loop or fan out** — one registration, one session, per (A).
- [ ] **Step 3 (confirm pass):** specs green; existing `register_spec.py` still green (single classes unaffected).
- [ ] **Step 4 (lint+commit):** ruff/mypy; commit `feat(classes): series checkout — one payment, one seat, clearer Stripe line item`.

> **Webhook:** no change. Re-run `classes/spec/webhook_handlers_spec.py` to confirm a series registration confirms identically (one `registration_id`). Add one webhook spec confirming `amount_paid_cents` = series total after confirm if not already covered.

---

## Task 6: Confirmation email reflects the full series

**Files:** `classes/emails.py` (+ its template), spec.

- [ ] **Step 1:** Read `classes/emails.py` `send_registration_confirmation` and its template. The confirmation already lists the offering's sessions (the schedule). For a series, ensure the copy reads as "you're enrolled in all N sessions" rather than implying one date. **Verify first** — the existing template may already iterate `sessions` (the detail page does). If it does, only the intro line needs a conditional; if it shows just the next session, add the full list for series.
- [ ] **Step 2 (failing test → implement → pass):** Spec that a confirmed series registration's email body mentions all session dates (use the existing email-spec pattern in `classes/spec/emails_spec.py`; emails are captured via Django's `mail.outbox` or the project's send helper — match the existing convention).
- [ ] **Step 3 (lint+commit):** ruff/mypy; commit `feat(classes): series confirmation email lists all sessions`.

---

## Task 7: Detail-page badge + schedule framing

**Files:** `templates/classes/public/detail.html`, spec.

- [ ] **Step 1 (failing test):** In `public_spec.py` (detail block): GET a published series detail page; assert `"Series Package"` appears and the schedule section header conveys "N sessions".
- [ ] **Step 2 (implement):** In `cp-detail__cat-row` (`detail.html:129–138`), add the same conditional badge as the card (Task 4), styled with the `cp-detail` pill classes (mirror `cp-detail__next-pill`). The Schedule section (`:189–205`) already shows "total duration" for multi-session; for a series, prefer a header like "Series schedule · N sessions · {{ total }} total" (the existing `total_session_minutes` filter is at `:191`). Keep single-session pages unchanged.
- [ ] **Step 3 (confirm pass + lint+commit):** spec green; ruff/mypy; commit `feat(classes): series badge + schedule framing on class detail`.

---

## Task 8: Manual verification (run skill)

Start the dev server (project `run` skill) on the booking host, in Stripe **test mode**.

- [ ] Build a Series Package class in the admin builder: toggle Series, schedule 3 dates on the calendar, set a price, publish. Confirm it saves with `scheduling_type=series_package` and a **blank** grouping_key (Django admin or shell).
- [ ] Catalog card shows the "Series Package" badge and does NOT collapse with any same-title single offering. A single class shows "Single Session".
- [ ] Detail page shows the badge and all 3 sessions in the schedule with a series-aware header.
- [ ] Register for the series → Stripe Checkout shows ONE line item at the series price with the "(3-session series)" name → pay with test card `4242 4242 4242 4242` → webhook confirms → one Registration, status CONFIRMED, `amount_paid_cents` = series total.
- [ ] `spots_remaining` on the series offering dropped by exactly 1 (one seat for the whole series).
- [ ] Confirmation email lists all 3 dates.
- [ ] Cancel the series registration (admin) → seat returns, waitlist promotion fires if anyone's waiting. (Refund itself is still a manual Stripe-dashboard action — confirm the UI/email don't promise an automatic refund.)
- [ ] A legacy grouped multi-date single class still renders "Pick a date" exactly as before (no regression to `grouping_key`).

---

## Task 9: Version bump + changelog

**Files:** `plfog/version.py`.

- [ ] **Step 1:** Re-verify the latest merged version: `git show main:plfog/version.py | grep 'VERSION ='`. At plan time `main`=2.5.7 and 2.5.8 is in flight (PR #108), so the next free patch is **2.5.9** — use it unless a newer one has merged.
- [ ] **Step 2:** Set `VERSION = "2.5.9"` and prepend a member-friendly entry (plain language — this posts to Discord):
  ```python
  {
      "version": "2.5.9",  # re-verify
      "date": "2026-06-18",  # set to merge date
      "title": "Multi-day class packages — buy once, attend every session",
      "changes": [
          "Classes can now be set up as a 'Series Package' — a multi-day course where one sign-up and one payment covers every date in the series (for example, a 3-part weekend workshop). Single-date classes keep working exactly as before.",
          "Class cards and class pages now show a clear 'Series Package' or 'Single Session' label so you know whether you're booking one date or the whole course before you sign up.",
          "When you register for a series, your confirmation email lists all the dates you're now enrolled in.",
      ],
  }
  ```
- [ ] **Step 3:** Commit `chore: bump version to 2.5.9 — series vs single sessions`.

---

## Final verification

- [ ] `pytest` — all pass, **100% coverage** on new code (model field/properties, factory, form field+guard, checkout, badge renders, email). Watch the migration's RunPython branches — cover both the backfill and a no-op reverse, or add a focused migration test.
- [ ] `ruff format . && ruff check . && mypy .` — clean (export `DATABASE_URL` before mypy/push).
- [ ] Manual Stripe-test-mode pass (Task 8) signed off end-to-end, including the no-regression check on legacy grouped dates.
- [ ] Migration reverses cleanly (`migrate classes <prev>` then forward again).

---

## Risks & rollout (payments)

**This change touches the live payment path. Mitigations:**

1. **Stripe stays mocked in tests, test-mode in manual QA.** Every automated test patches `billing.stripe_utils.create_class_checkout_session` (`register_spec.py:121` pattern) — no network, no keys. Manual verification uses Stripe **test mode** (card `4242…`) only. Never run this plan's flows against live keys. The Stripe util and webhook are structurally unchanged, which is the single biggest risk-reducer: the money-moving code is the same code paying for single classes today.

2. **Behaviour-preserving default + explicit backfill.** Existing offerings default to `SINGLE_SESSION` and the migration backfills explicitly. **No existing offering's price, capacity, sessions, or checkout behaviour changes.** The `AddField` default covers the schema; the `RunPython` is the auditable backfill with a deliberate no-op reverse (documented).

3. **One-seat-per-series is the load-bearing invariant.** Verify in tests AND manual QA that a confirmed series consumes exactly one seat and charges exactly once. A bug here (e.g. accidentally looping over sessions) would over-charge or double-book — the spec `it_charges_the_series_price_once_for_all_sessions` + `it_consumes_one_seat_for_the_whole_series` are the guards. Because (A) reuses per-offering capacity, there is intentionally no new capacity code to get wrong.

4. **Refunds remain manual.** Today there is no automated Stripe refund (verified — no `refunds.create` in the codebase; `cancel()` only sets status and `REFUNDED` is reached manually). A series refund is therefore identical to a single refund: refund the one charge in the Stripe dashboard, set status REFUNDED. **Do not let any new UI/email imply an automatic series refund.** A future plan could add real `stripe_utils.refund_charge` + an admin button — explicitly out of scope here (Follow-up).

5. **`grouping_key` coexistence.** Series offerings opt out of grouping (blank key, Task 3) so the two mechanisms never fight. Risk: a class that was previously expressed as N grouped single offerings and is now *also* offered as a series could appear twice. Mitigation: that's an operator data-modelling choice, not a code bug — document in the builder help text that a series should be a single offering with multiple scheduled dates, not several offerings. The manual QA step checks legacy grouped dates are unaffected.

6. **Partial-availability is a non-feature by design** (decision above) — there is no "some dates full" state for a series under (A). If the makerspace later wants per-date seats within a series, that's option (B) and a separate, larger plan. Flag, don't build.

7. **Rollout order:** ship behind nothing (no feature flag needed — the default is the existing behaviour). Deploy to Hetzner QA, build one test series, run the full Task 8 checklist in Stripe test mode there before merging to `main`/Render. Render is production; do not test live there.

---

## Follow-up (out of scope for this plan)

- **Automated series refunds:** add `stripe_utils.refund_charge(payment_intent_id)` + an admin "Refund & cancel" button that issues the Stripe refund and sets REFUNDED atomically. Today refunds are manual for *all* registrations; series doesn't change that, so it's a separate, general improvement.
- **Per-session attendance / check-in for series:** if instructors want to mark who showed up to each session of a series, that needs a per-session attendance model. Not requested; YAGNI.
- **Per-date capacity within a series (option B):** only if the space ever needs independently-sold dates inside one course. Larger model change; revisit if asked.
- **"Single Session" badge noise:** if showing the badge on every single-date card reads cluttered in production, switch to series-only badging (Task 4 note).
