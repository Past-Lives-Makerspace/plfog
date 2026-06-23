# Smart Pre-fill Onboarding — Registration ↔ Profile Field Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Each numbered task follows strict TDD: write the failing test → confirm it fails → implement → confirm it passes → lint + commit. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop returning users from re-typing data they already gave us. Two halves of one round-trip:
1. **Cache forward** — when a *logged-in* user registers for a class, persist the overlapping registration answers onto their `UserProfile` (without clobbering values the user already set).
2. **Pre-fill back** — when that user later enters the CMS onboarding wizard at `book.pastlives.space/account/onboarding/`, initialize each step's form from their existing `UserProfile` so the matching inputs auto-populate.

**Architecture:** This is a **wiring + mapping change, not new models.** `UserProfile` (`core/models.py:352`) already has every target field. The work is: (a) a small profile-caching method called from the registration view after `form.save()`, (b) a `get_initial()` on the onboarding parent view, and (c) one precedence rule (profile value wins as the form *initial*; the user can still edit). No migration is needed unless the mapping table below turns up a genuinely missing target field — it does not (verified, see "Decisions").

**Tech Stack:** Django (fat models / skinny views / forms own validation), pytest + pytest-describe BDD specs (`*_spec.py`), factory-boy, ruff (line-length 120) + mypy, full type hints. No new dependencies, no JS, no template changes (the onboarding templates already render the form fields and reflect `field.value`).

---

## Background / context for the implementer

### What already exists (re-verified — do not rebuild)
- **Onboarding flow exists.** `classes/account/views.py:217` `OnboardingStepView` (parent) + `OnboardingStep1View/2View/3View` at `:263/:273/:283`. URLs: `classes/account/urls.py:15-17` (`account:onboarding_step1/2/3`).
  - `OnboardingStepView.form_valid` (`:242-260`) writes answers to the profile with a plain loop: `for field_name, value in form.cleaned_data.items(): setattr(profile, field_name, value)`. **This means every onboarding form field name is already identical to its `UserProfile` attribute name** — which is exactly why pre-fill can be a symmetric `get_initial()` keyed on the same names.
  - **There is no `get_initial()` anywhere in this view hierarchy** → every onboarding visit currently renders blank for fields the user already has on file. That is gap #2.
- **Onboarding forms.** `classes/account/forms.py:89-147`:
  - Step1 `OnboardingStep1Form` → `first_attendance_status` (`:89`).
  - Step2 `OnboardingStep2Form` → `preferred_name`, `pronouns`, `phone`, `referral_source` (`:101`).
  - Step3 `OnboardingStep3Form` → `interest_category_slugs` (`_OpenMultipleChoiceField`), `accessibility_note` (`:131`).
- **`UserProfile` model.** `core/models.py:352-440`. Relevant fields (all `blank=True`, defaults safe):
  - `preferred_name` (`:380`), `pronouns` (`:381`), `phone` (`:386`), `first_attendance_status` (`:391`), `referral_source` (`:397`), `interest_category_slugs` JSONField `default=list` (`:403`), `accessibility_note` (`:408`).
  - One-to-one to user via `related_name="profile"` (`:377`) → reachable as `user.profile`.
  - **No auto-create signal** (verified: `core/signals.py` / `membership/signals.py` create a `Member`, never a `UserProfile`). So both halves must use `UserProfile.objects.get_or_create(user=...)` — the onboarding view already does (`:246`).
- **Registration form + fields.** `classes/forms.py:502` `RegistrationForm(forms.ModelForm)`; `Meta.fields` (`:533-542`) = `first_name`, `last_name`, `pronouns`, `email`, `phone`, `prior_experience`, `looking_for`, `wants_newsletter`. `save()` at `:690` returns the `Registration`. **The form is constructed with `member=` but NOT `user=`** (`:553-562`), so caching must happen in the *view*, which has `request.user`.
- **Registration view.** `classes/views.py:430` `register(request, slug)`. After a successful non-waitlist `form.save()` at `:478` it already does a pile of post-save side effects (confirm email, Mailchimp subscribe at `:504-506`). This is the natural seam for the cache-forward call. The waitlist branch saves at `:469`.
- **Registration ALREADY pre-fills the form FROM `Member`** (the *other* direction, and a different source): `_registration_initial_for_user` (`classes/views.py:411-427`) seeds `first_name/last_name/email/phone/pronouns` from `user.member` on GET only (`:454`). This proves the precedence pattern ("profile/member value wins as initial; user edits") is already the house style. We are NOT touching this helper — it reads from `Member`, not `UserProfile`, and it is the registration form, not onboarding.

### Existing test scaffolding to reuse
- `classes/spec/conftest.py` — `member_user` (`:28`) and `admin_user` (`:10`) fixtures. `member_user` is a logged-in user with a `Member`; ideal for the cache-forward test.
- `classes/spec/views/register_spec.py` — `paid_offering`/`free_offering` fixtures (`:24/:43`), `_post_data()` helper (`:340`-ish), `it_prefills_form_for_a_logged_in_member` (`:89`) shows the exact `client.force_login(...)` + GET-and-assert pattern, and `it_confirms_immediately_for_a_free_class` (`:110`) shows the free-class POST path (no Stripe mock needed) — **use the free-class POST path for the cache-forward test so no Stripe round-trip is required.**
- `classes/factories.py:34` `UserFactory`. **No `UserProfileFactory` exists** — see Task 1.
- There is currently **no spec file for `classes/account/` views or forms.** New specs go under `classes/spec/account/` (new dir; mirror the existing `classes/spec/views/` / `classes/spec/forms/` layout).

---

## Decisions baked into this plan

### The field mapping (registration field → UserProfile field)
Only fields with a **genuine 1:1 semantic match** are auto-cached. Everything else is deliberately left alone and listed as a non-mapping with the reason.

| Registration field (`classes/forms.py:533`) | UserProfile field (`core/models.py`) | Map? | Rationale |
|---|---|---|---|
| `pronouns` | `pronouns` (`:381`) | **YES — cache** | Identical meaning, identical type (CharField). Clean 1:1. |
| `phone` | `phone` (`:386`) | **YES — cache** | Identical meaning (day-of contact). Clean 1:1. |
| `first_name` + `last_name` | `preferred_name` (`:380`) | **NO** | Composition mismatch: registration captures *legal-ish* first/last; `preferred_name` is a single self-chosen roster name. Joining "first last" into `preferred_name` would put a name the user never chose as their "preferred" name and would fight the registration view's own Member-derived prefill. Leave for the user to set in onboarding Step 2. |
| `looking_for` (free text) | `interest_category_slugs` (slug list, `:403`) | **NO** | Type + semantics mismatch: free prose ("hoping to make a mug for my mom") cannot be mechanically mapped to a list of `Category` slugs. No reliable parse. The spec's "Areas of Curiosity" maps to onboarding Step 3's category chips, which have **no registration-form equivalent to cache from.** |
| `prior_experience` (free text) | *(none)* | **NO** | No profile counterpart. `first_attendance_status` is a closed choice, not free text — not the same field. |
| `wants_newsletter` (bool) | *(none)* | **NO** | Newsletter opt-in is per-registration consent, tracked on `Registration` + Mailchimp; no profile mirror, and silently flipping a profile-wide preference from one class signup would be wrong. |
| `email` | *(none on UserProfile)* | **NO** | Lives on `User.email` / `Member`, not `UserProfile`. Already handled by the existing Member-prefill path. |

**Net: exactly two fields cache forward — `pronouns` and `phone`.** This is intentionally conservative per the brief ("only map fields with a genuine 1:1 semantic match — flag fuzzy ones, don't force-map"). If the team later wants name/interest mapping, that is a follow-up requiring product decisions (and possibly a parse step), not this wiring change.

### No-clobber precedence (applies to BOTH directions)
- **Cache forward (registration → profile):** only fill a profile field that is currently **empty** (`""`). If `profile.pronouns` already has a value, a later registration does NOT overwrite it. The profile is the durable record of the user's own stated preference; a one-off class form should seed it, never stomp it. Implemented as: write only when the existing profile value is falsy AND the incoming registration value is non-empty.
- **Pre-fill back (profile → onboarding form):** the profile value is supplied as the form **`initial`**. Initial only shows on an unbound (GET) form; on POST the user's submitted value always wins, so editing is never blocked. This is identical to how `_registration_initial_for_user` already behaves.

### Where the logic lives (fat models / skinny views)
- **Cache forward** is a `UserProfile` method: `UserProfile.cache_from_registration(registration)` — business logic on the model, called from the view with one line. Not in the form (the form has no `user`), not inline in the view (keeps the view skinny). It is a model method, not a property, because it has side effects (a `save`).
- **Pre-fill back** is `OnboardingStepView.get_initial()` plus a tiny per-step declaration of which profile attrs that step reads. Pure form wiring, lives on the view (initial is a view/form concern, not business logic).

### Skip already-onboarded? (out of scope, flagged)
This plan does **not** change *whether* the wizard shows for already-onboarded users (`UserProfile.is_onboarded`, `core/models.py:438`). It only makes the wizard pre-fill. Routing/gating is a separate concern — see Follow-up.

---

## File Structure

- Modify: `core/models.py` — add `UserProfile.cache_from_registration(self, registration) -> None` (the no-clobber forward cache, ~12 lines, fully typed, with a `TYPE_CHECKING` import of `Registration`).
- Modify: `classes/views.py` — in `register()`, after each successful `form.save()` (the free/paid branch at `:478` and the waitlist branch at `:469`), call `UserProfile`'s cache method when `request.user.is_authenticated`. One helper call so both branches share it.
- Modify: `classes/account/views.py` — add `get_initial()` to `OnboardingStepView` (`:217`) and a per-step `profile_fields: tuple[str, ...]` class attr on each of `OnboardingStep1View/2View/3View`.
- Add: `core/factories.py` (new) — `UserProfileFactory` (factory-boy) for the new specs. (No factories file exists in `core/` yet; create it minimally.)
- Add: `classes/spec/account/__init__.py`, `classes/spec/account/conftest.py` (if step-specific fixtures help), `classes/spec/account/onboarding_prefill_spec.py` (pre-fill back), and `classes/spec/views/register_profile_cache_spec.py` (cache forward — kept under `views/` next to the other register specs).
- Modify: `plfog/version.py` — version bump + member-friendly CHANGELOG entry.

---

## Task 1: `UserProfileFactory` + the no-clobber forward-cache model method

**Files:** `core/factories.py` (new), `core/models.py`, plus a model spec.

- [ ] **Step 1 (factory):** Create `core/factories.py` with a `UserProfileFactory`:
  ```python
  """factory-boy factories for the core app."""

  from __future__ import annotations

  import factory
  from django.contrib.auth import get_user_model

  from core.models import UserProfile

  User = get_user_model()


  class CoreUserFactory(factory.django.DjangoModelFactory):
      class Meta:
          model = User
          django_get_or_create = ("username",)

      username = factory.Sequence(lambda n: f"profileuser{n}")
      email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")


  class UserProfileFactory(factory.django.DjangoModelFactory):
      class Meta:
          model = UserProfile

      user = factory.SubFactory(CoreUserFactory)
      preferred_name = ""
      pronouns = ""
      phone = ""
  ```
  > `classes/factories.py:34` already has a `UserFactory`; reuse it instead if its signature fits the spec, but `core` having its own keeps the dependency direction clean (`core` must not import from `classes`).

- [ ] **Step 2 (failing test):** Write `core/spec/models/user_profile_cache_spec.py`:
  ```python
  from __future__ import annotations

  import pytest

  from core.factories import UserProfileFactory


  def describe_UserProfile():
      def describe_cache_from_registration():
          def it_fills_empty_pronouns_and_phone(db):
              from classes.factories import RegistrationFactory

              profile = UserProfileFactory(pronouns="", phone="")
              reg = RegistrationFactory(pronouns="they/them", phone="503-555-0100")
              profile.cache_from_registration(reg)
              profile.refresh_from_db()
              assert profile.pronouns == "they/them"
              assert profile.phone == "503-555-0100"

          def context_when_the_profile_already_has_values():
              def it_does_not_clobber_them(db):
                  from classes.factories import RegistrationFactory

                  profile = UserProfileFactory(pronouns="she/her", phone="111")
                  reg = RegistrationFactory(pronouns="they/them", phone="999")
                  profile.cache_from_registration(reg)
                  profile.refresh_from_db()
                  assert profile.pronouns == "she/her"
                  assert profile.phone == "111"

          def context_when_the_registration_value_is_blank():
              def it_leaves_the_empty_profile_field_empty(db):
                  from classes.factories import RegistrationFactory

                  profile = UserProfileFactory(pronouns="", phone="")
                  reg = RegistrationFactory(pronouns="", phone="")
                  profile.cache_from_registration(reg)
                  profile.refresh_from_db()
                  assert profile.pronouns == ""
                  assert profile.phone == ""

          def it_only_writes_changed_fields(db):
              # Guard the update_fields optimization: a no-op call saves nothing.
              from classes.factories import RegistrationFactory

              profile = UserProfileFactory(pronouns="she/her", phone="111")
              reg = RegistrationFactory(pronouns="", phone="")
              profile.cache_from_registration(reg)  # nothing to copy → no error, no change
              profile.refresh_from_db()
              assert profile.pronouns == "she/her"
  ```
  > Confirm `RegistrationFactory` (`classes/factories.py`, used in `register_spec.py:17`) accepts `pronouns`/`phone` kwargs — it builds a full `Registration`, which has both fields (`classes/models.py:929/931`). If it requires a `class_offering`, the factory's `SubFactory` already supplies one.

- [ ] **Step 3: Confirm it fails** — `pytest core/spec/models/user_profile_cache_spec.py` → fails with `AttributeError: 'UserProfile' object has no attribute 'cache_from_registration'`.

- [ ] **Step 4: Implement** the method on `UserProfile` (`core/models.py`, after `is_onboarded` at `:440`). Use `TYPE_CHECKING` for the annotation-only `Registration` import per house style:
  ```python
  # near the top of core/models.py, in the TYPE_CHECKING block
  if TYPE_CHECKING:
      from classes.models import Registration

  # method on UserProfile
  def cache_from_registration(self, registration: "Registration") -> None:
      """Seed empty profile fields from a class registration's overlapping answers.

      Only fills fields that are currently blank — a registration never
      overwrites a value the user has already set. Maps exactly the fields
      with a clean 1:1 semantic match (pronouns, phone); see the onboarding
      pre-fill plan for the full mapping rationale.
      """
      mapping = {"pronouns": registration.pronouns, "phone": registration.phone}
      changed: list[str] = []
      for field_name, incoming in mapping.items():
          if not getattr(self, field_name) and incoming:
              setattr(self, field_name, incoming)
              changed.append(field_name)
      if changed:
          self.save(update_fields=changed)
  ```
  > If `core/models.py` has no `TYPE_CHECKING` block yet, add `from typing import TYPE_CHECKING` and the guarded import. Lazy/string annotation `"Registration"` avoids the `core → classes` runtime import cycle.

- [ ] **Step 5: Confirm pass** — `pytest core/spec/models/user_profile_cache_spec.py -v` → all green.

- [ ] **Step 6: Lint + commit** — `ruff format . && ruff check --fix .`; commit `feat(core): UserProfile.cache_from_registration (no-clobber)`.

---

## Task 2: Wire the forward cache into the registration view

**Files:** `classes/views.py`, plus a view spec.

- [ ] **Step 1 (failing test):** `classes/spec/views/register_profile_cache_spec.py`:
  ```python
  """Logged-in registration caches overlapping answers onto the user's profile."""

  from __future__ import annotations

  import pytest
  from django.urls import reverse

  pytestmark = pytest.mark.django_db


  def describe_registration_profile_cache():
      def it_caches_pronouns_and_phone_for_a_logged_in_user(free_offering, client, member_user):
          from core.models import UserProfile

          client.force_login(member_user)
          data = _post_data(pronouns="xe/xem", phone="503-555-9999")
          resp = client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=data)
          assert resp.status_code == 302
          profile = UserProfile.objects.get(user=member_user)
          assert profile.pronouns == "xe/xem"
          assert profile.phone == "503-555-9999"

      def it_does_not_clobber_an_existing_profile_value(free_offering, client, member_user):
          from core.models import UserProfile

          UserProfile.objects.create(user=member_user, pronouns="she/her")
          client.force_login(member_user)
          data = _post_data(pronouns="xe/xem", phone="503-555-9999")
          client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=data)
          profile = UserProfile.objects.get(user=member_user)
          assert profile.pronouns == "she/her"   # untouched
          assert profile.phone == "503-555-9999"  # was empty → filled

      def it_does_nothing_for_an_anonymous_registrant(free_offering, client):
          from core.models import UserProfile

          data = _post_data(pronouns="xe/xem", phone="503-555-9999")
          resp = client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=data)
          assert resp.status_code == 302
          assert not UserProfile.objects.exists()
  ```
  - Reuse the `free_offering` fixture + `_post_data` helper from `register_spec.py` (free class confirms immediately, no Stripe mock). Either import `_post_data`/`free_offering` from the existing module or lift them into `classes/spec/views/conftest.py`. **Confirm `_post_data` accepts overrides** (`register_spec.py:340`-ish builds the dict); if not, pass a merged dict inline. `_post_data` must include `pronouns`/`phone` keys for these assertions.
  > `member_user` (conftest `:28`) has an auto-created `Member` but **no `UserProfile`** → the first test exercises the `get_or_create` path; the second pre-creates one.

- [ ] **Step 2: Confirm it fails** — pronouns/phone are NOT yet copied to any `UserProfile`.

- [ ] **Step 3: Implement** in `classes/views.py`. Add a tiny module-level helper and call it from both save sites:
  ```python
  def _cache_registration_to_profile(request: HttpRequest, registration: "Registration") -> None:
      """Seed the logged-in user's profile from their registration answers (no-op for guests)."""
      if not request.user.is_authenticated:
          return
      from core.models import UserProfile

      profile, _ = UserProfile.objects.get_or_create(user=request.user)
      profile.cache_from_registration(registration)
  ```
  Call it right after the waitlist save (`:469`, before `send_waitlist_joined_confirmation`) and after the main save (`:478`, before the free/paid branch). One call each; the helper guards the anonymous case.
  > Keep the view skinny: the helper only resolves the profile and delegates to the model method (all real logic stays in `UserProfile.cache_from_registration`). Use the lazy `from core.models import UserProfile` import inside the function, matching the view's existing lazy-import style.

- [ ] **Step 4: Confirm pass** — `pytest classes/spec/views/register_profile_cache_spec.py -v`.

- [ ] **Step 5: Regression** — `pytest classes/spec/views/register_spec.py` still green (the Member-based GET prefill is untouched).

- [ ] **Step 6: Lint + commit** — `ruff format . && ruff check --fix .`; commit `feat(classes): cache registration answers to UserProfile on signup`.

---

## Task 3: Pre-fill onboarding forms FROM the profile (`get_initial`)

**Files:** `classes/account/views.py`, plus an onboarding spec.

The symmetry win: `OnboardingStepView.form_valid` already maps **form field name → profile attr** 1:1 (`:247`). So `get_initial` is the mirror — for each profile field this step reads, set `initial[name] = getattr(profile, name)`. Declaring the fields per step (rather than introspecting the form) keeps it explicit and avoids pulling profile fields a step doesn't render.

- [ ] **Step 1 (failing test):** `classes/spec/account/__init__.py` (empty) + `classes/spec/account/onboarding_prefill_spec.py`:
  ```python
  """Returning users see onboarding fields pre-filled from their profile."""

  from __future__ import annotations

  import pytest
  from django.urls import reverse

  pytestmark = pytest.mark.django_db


  def describe_onboarding_prefill():
      def it_prefills_step2_from_the_profile(client, member_user):
          from core.models import UserProfile

          UserProfile.objects.create(
              user=member_user,
              preferred_name="Robin",
              pronouns="they/them",
              phone="503-555-0100",
              referral_source=UserProfile.Referral.INSTAGRAM,
          )
          client.force_login(member_user)
          resp = client.get(reverse("account:onboarding_step2"))
          assert resp.status_code == 200
          body = resp.content.decode()
          assert 'value="Robin"' in body
          assert 'value="they/them"' in body
          assert 'value="503-555-0100"' in body

      def it_prefills_step1_attendance(client, member_user):
          from core.models import UserProfile

          UserProfile.objects.create(
              user=member_user, first_attendance_status=UserProfile.FirstAttendance.RETURNING
          )
          client.force_login(member_user)
          resp = client.get(reverse("account:onboarding_step1"))
          body = resp.content.decode()
          # the "returning" radio renders checked
          assert "returning" in body

      def it_prefills_step3_accessibility_note(client, member_user):
          from core.models import UserProfile

          UserProfile.objects.create(user=member_user, accessibility_note="Need step-free access")
          client.force_login(member_user)
          resp = client.get(reverse("account:onboarding_step3"))
          assert "Need step-free access" in resp.content.decode()

      def it_renders_blank_when_no_profile_exists(client, member_user):
          client.force_login(member_user)
          resp = client.get(reverse("account:onboarding_step2"))
          assert resp.status_code == 200  # get_initial must not 500 when profile is absent
  ```
  > For the referral `<select>` assertion, prefer checking the selected `<option>` markup if `value="instagram"` isn't on a top-level input — adjust the assertion to match `templates/classes/account/onboarding/step2.html` after a quick read. Step 1 radios and Step 3 textarea reflect `initial` automatically once `get_initial` supplies it. The booking-surface auth views relay-redirect anonymous users — `member_user` is logged in, so the `_RelayAwareLoginMixin` passes through; no host override needed for an authenticated GET.

- [ ] **Step 2: Confirm it fails** — fields render empty today (no `get_initial`).

- [ ] **Step 3: Implement.** In `classes/account/views.py`:
  - On `OnboardingStepView` add:
    ```python
    profile_fields: tuple[str, ...] = ()

    def get_initial(self) -> dict[str, object]:
        initial = super().get_initial()
        from core.models import UserProfile

        profile = UserProfile.objects.filter(user=self.request.user).first()
        if profile is not None:
            for name in self.profile_fields:
                initial[name] = getattr(profile, name)
        return initial
    ```
  - Declare per step (field names already match both the form fields and the profile attrs):
    - `OnboardingStep1View.profile_fields = ("first_attendance_status",)`
    - `OnboardingStep2View.profile_fields = ("preferred_name", "pronouns", "phone", "referral_source")`
    - `OnboardingStep3View.profile_fields = ("interest_category_slugs", "accessibility_note")`
  > `interest_category_slugs` is a JSON list and `OnboardingStep3Form.interest_category_slugs` is a `MultipleChoiceField` — a list `initial` is exactly what it wants, so the previously-chosen chips re-check. `.filter(...).first()` returns `None` cleanly when no profile exists, satisfying the "blank when no profile" test without a `get_or_create` write on a GET.

- [ ] **Step 4: Confirm pass** — `pytest classes/spec/account/onboarding_prefill_spec.py -v`. If a select/checkbox assertion is brittle against the template, read the specific `step*.html` and tighten the asserted substring.

- [ ] **Step 5: Regression** — onboarding submit still works: `OnboardingStepView.form_valid` is unchanged, and `get_initial` only affects unbound GETs. Run any existing onboarding tests if present (none found today) plus the new spec.

- [ ] **Step 6: Lint + commit** — `ruff format . && ruff check --fix .`; commit `feat(account): pre-fill onboarding steps from UserProfile`.

---

## Task 4: Round-trip integration check (cache forward → pre-fill back)

**Files:** extend `classes/spec/account/onboarding_prefill_spec.py` (no new code).

- [ ] **Step 1 (test):** Prove the two halves connect end-to-end:
  ```python
      def it_round_trips_registration_pronouns_into_onboarding(free_offering, client, member_user):
          # 1) register while logged in → caches pronouns/phone
          client.force_login(member_user)
          client.post(
              reverse("classes:register", kwargs={"slug": free_offering.slug}),
              data=_post_data(pronouns="ze/zir", phone="503-555-7777"),
          )
          # 2) enter onboarding step 2 → those values are pre-filled
          resp = client.get(reverse("account:onboarding_step2"))
          body = resp.content.decode()
          assert 'value="ze/zir"' in body
          assert 'value="503-555-7777"' in body
  ```
  Pull `free_offering` + `_post_data` from the shared `classes/spec/views/conftest.py` (create it in Task 2 if they were lifted there) or import from `register_spec`.

- [ ] **Step 2: Confirm pass** with Tasks 1-3 in place. This is the literal acceptance scenario from the feature brief.

- [ ] **Step 3: Lint + commit** (folds into the Task 3 commit if done together).

---

## Task 5: Lint / format / type-check

- [ ] **Step 1:** `ruff format . && ruff check .` — clean across `core/models.py`, `core/factories.py`, `classes/views.py`, `classes/account/views.py`, and all new specs.
- [ ] **Step 2:** `mypy .` — the new method, helper, and `get_initial` are fully typed. Export `DATABASE_URL` first if running before push: `export $(grep '^DATABASE_URL=' .env | xargs)` (per project memory — pre-push mypy needs it).

---

## Task 6: Version bump + changelog

**Files:** `plfog/version.py`

- [ ] **Step 1:** Bump `VERSION` to the next patch after the merged release. **At time of writing the latest is `2.5.8` (PR #108, in flight). Verify the actually-merged version first** (`git fetch && git log origin/main -- plfog/version.py`, or read `VERSION` on `main`) and use the next patch (e.g. `2.5.9`). Do not assume.
- [ ] **Step 2:** Prepend a member-friendly `CHANGELOG` entry (plain language — this posts to Discord; no jargon, no PR numbers):
  ```python
  {
      "version": "2.5.9",  # verify against merged main
      "date": "2026-06-18",  # set to merge date
      "title": "We remember what you told us",
      "changes": [
          "If you're signed in when you register for a class, the pronouns and phone number you enter are now saved to your account, so you don't have to type them again later.",
          "The welcome/onboarding questions on the booking site now come pre-filled with anything we already have on file — your preferred name, pronouns, phone, how you found us, your interests, and any access notes — so returning visitors can review instead of re-enter. You can still change anything before saving.",
      ],
  }
  ```
- [ ] **Step 3:** Commit `chore: bump version + changelog for onboarding pre-fill`.

---

## Final verification

- [ ] `pytest` — full suite green, **100% coverage** including the new model method, view helper, `get_initial`, and the no-clobber/anonymous branches (the specs above exercise: empty→fill, existing→no-clobber, blank-incoming→no-op, anonymous→skip, no-profile→no-500, round-trip).
- [ ] `ruff format . && ruff check . && mypy .` — clean.
- [ ] Manual smoke (optional, project `run` skill): log in on the booking surface, register for a free class with new pronouns/phone, then open `/account/onboarding/2/` and confirm those values are pre-filled and editable; confirm a field you already had set is NOT overwritten by a later registration.

---

## Follow-up (out of scope for this plan)

- **Name & interest mapping.** `first_name`/`last_name` → `preferred_name` and `looking_for`/`prior_experience` → `interest_category_slugs` were intentionally NOT mapped (no clean 1:1 — see the mapping table). If product wants these, it needs a decision on name composition and a free-text-to-category parse (or an extra registration question that captures category interest directly), which is a feature, not wiring.
- **Onboarding gating.** This plan pre-fills the wizard but does not change *whether* it appears for already-onboarded users (`UserProfile.is_onboarded`, `core/models.py:438`). If returning users shouldn't be re-walked through onboarding at all, that's a separate routing change.
- **Profile → registration prefill from `UserProfile`.** Registration currently prefills only from `Member` (`classes/views.py:411`). Extending `_registration_initial_for_user` to also fall back to `UserProfile` (for non-member account holders) would close the loop the other way, but is a distinct change with its own precedence questions.
