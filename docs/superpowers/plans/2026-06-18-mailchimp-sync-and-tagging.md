# Mailchimp Sync & Automated Tagging — Close the Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the four remaining gaps in the already-shipped Mailchimp integration: (1) stop tagging known Past Lives people as "first-time" by consulting the member registry (the local mirror of Airtable) in addition to local class history, (2) actively *suppress* the first-time tag for verified members, (3) settle the tag name (`first-time-student` vs the spec's "First-Time Class Taker"), and (4) hide the marketing opt-in checkbox from anyone who already opted in.

**Architecture:** The Mailchimp machinery already exists and is correct — a bare `requests` v3 client with MD5-hashed upsert (`core/integrations/mailchimp.py`), a class-registration bridge (`classes/services/mailchimp_subscribe.py`), and an account-signup bridge (`core/services/mailchimp_account.py`). This plan does **not** rebuild any of that. It is a surgical change to the *tag-derivation logic* (one function, `derive_tags`) and the *registration form/template* (checkbox visibility). No new dependencies, no new client, no schema changes beyond — at most — one nullable timestamp write that already has a home (`UserProfile.subscribed_to_mailchimp_at`).

**Tech Stack:** Django 5, pytest + pytest-describe (BDD `*_spec.py`), factory-boy, `unittest.mock.patch` for the Mailchimp client (the existing specs mock `MailchimpClient.subscribe` directly — no `respx` needed at this layer; `respx` is reserved for the HTTP-client specs in `tests/core/integrations/mailchimp_spec.py`). ruff (line 120) + mypy. Full type hints.

---

## Background / context for the implementer

### What already exists (DO NOT rebuild)

- **HTTP client** — `core/integrations/mailchimp.py`. `MailchimpClient.subscribe(*, email, first_name, last_name, tags)` does a `PUT /lists/{id}/members/{md5(email)}` upsert (`:110–111`), idempotent and dedup-safe (existing email is merged/updated, never duplicated, and `status_if_new` avoids resurrecting unsubscribes). Returns `bool`, never raises. `from_site_config()` (`:52–69`) returns a disabled client when creds are blank.
- **Site config** — `core/models.py:156` `mailchimp_api_key`, `:162` `mailchimp_list_id` on the `SiteConfiguration` singleton.
- **Class-registration bridge** — `classes/services/mailchimp_subscribe.py`. `subscribe_registration()` (`:54–84`) is gated on `registration.wants_newsletter` (`:62`) and the idempotency flag `registration.subscribed_to_mailchimp` (`:64`), then calls `client.subscribe(..., tags=derive_tags(registration))`. Wired in from the free-class path (`classes/views.py:504–506`) and the Stripe webhook (`classes/webhook_handlers.py:104–106`).
- **`derive_tags(registration)`** — `classes/services/mailchimp_subscribe.py:20–51`. Today it emits `class-registrant`, `category-<slug>`, optional `guild-<slug>`, optional `instructor-<slug>`, and `first-time-student` — the last one gated **only** on local confirmed `Registration` rows (`:40–49`):
  ```python
  prior_confirmed = (
      Registration.objects.filter(
          email__iexact=registration.email,
          status=Registration.Status.CONFIRMED,
      )
      .exclude(pk=registration.pk)
      .exists()
  )
  if not prior_confirmed:
      tags.append("first-time-student")
  ```
- **Account-signup bridge** — `core/services/mailchimp_account.py`. `subscribe_user(user)` (`:35–71`) is gated on `profile.subscribed_to_mailchimp_at` (`:45`) and stamps it on success (`:70–71`). Tags via `derive_account_tags` (`:17–32`): `account-signup`, `persona-*`, `referral-*`, `interest-*`. **This path is out of scope** except as the precedent for the suppression flag (Task 4).
- **Form field** — `Registration.wants_newsletter` (`classes/models.py:976`), `Registration.subscribed_to_mailchimp` (`:980`). The form exposes it: `RegistrationForm.Meta.fields` includes `wants_newsletter` (`classes/forms.py:541`) with a friendly label (`:547–551`). The template renders it at `templates/classes/public/register.html:94–97`.
- **The suppression timestamp ALREADY EXISTS** — `core/models.py:417` `UserProfile.subscribed_to_mailchimp_at` ("Stamp set when the account-signup push to Mailchimp succeeded."). It is currently written **only** by the onboarding path (`mailchimp_account.py:70`), never by the class-registration path.

### What is mirrored from Airtable (the load-bearing constraint for Gap #1)

- The Airtable base is **"PLM Members & Studios 2026"** and only **Members, Spaces, Leases** are mirrored inbound — `airtable_sync/config.py:19–23` (`MEMBERS_TABLE_ID`, `SPACES_TABLE_ID`, `LEASES_TABLE_ID`, plus two *outbound-only* vote/session tables). The pull command (`airtable_sync/management/commands/airtable_pull.py:55–72`) imports Members (matching/creating by `_pre_signup_email`, `:92–93`), Spaces, and Leases. **There is no class, registration, or attendee-history table in Airtable, and none is pulled.**
- **Consequence — be honest about this:** The spec's literal instruction "check the email against … the AirTable mirror; if it exists in neither historical registry" cannot mean "look up prior *class attendance* in Airtable" — that history does not exist in Airtable at all. The only Airtable-derived fact available locally is **membership**: whether this email belongs to a known/verified Member. That is exactly what the spec's *other* clause wants ("verified Past Lives Member (AirTable check) → suppress the tag"). So Gaps #1 and #2 collapse into one real signal: **is this email a known Past Lives Member?** See Decision A.

### How to resolve "is this email a known member?" (the three email stores)

Per `membership/CLAUDE.md` and `docs/superpowers/specs/2026-04-07-user-email-aliases-design.md`, a member's email can live in three places:
- `Member._pre_signup_email` (DB column `email`) — truth for **unlinked** (Airtable-imported, not-yet-signed-up) members. **This is the Airtable mirror value.**
- `allauth.account.EmailAddress` — truth for **linked** members (verified login aliases).
- `MemberEmail.email` — pre-signup **staging** aliases for unlinked members (imported from Airtable, not yet promoted).

The existing precedent for "verified member by email" is `classes/views.py:397–408` `_member_for_email()`, which checks **only** verified `EmailAddress`. That is too narrow for our purpose: an Airtable-imported member who has never signed up has **no** `EmailAddress` row — they live in `_pre_signup_email` / `MemberEmail`. To honor "prior off-system attendees / known members aren't mis-tagged," the membership check must union all three stores. See Task 1.

### Existing tests to mirror

- `classes/spec/services/mailchimp_subscribe_spec.py` — the canonical style: a `site_with_mailchimp` fixture, `patch("core.integrations.mailchimp.MailchimpClient.subscribe")`, `describe_derive_tags()` with `it_tags_first_time_students` (`:127`) and `it_does_not_tag_first_time_when_prior_confirmed_exists` (`:132`). **Extend this file** for Tasks 1–3.
- `tests/core/services/mailchimp_account_spec.py` — account-path precedent (not modified here).
- Member factories: `tests/membership/factories.py` (`MemberFactory`, etc.). Classes factories: `classes/factories.py` (`RegistrationFactory`, `ClassOfferingFactory`, `CategoryFactory`, `InstructorFactory`).
- The classes app keeps specs under `classes/spec/`; membership specs live under `tests/membership/`. Follow each app's location.

---

## Decisions baked into this plan

- **Decision A — "Airtable history" means "is a known member," not "prior class attendance."** Airtable mirrors Members/Spaces/Leases only; there is no attendance table to consult (`airtable_sync/config.py:19–23`). The implementable, spec-faithful reading: a contact is "first-time" only when they are **neither** a returning local registrant **nor** a known Past Lives Member. We will add a single `_is_known_member(email)` helper that unions the three email stores (`_pre_signup_email`, `MemberEmail`, verified `EmailAddress`), which by construction includes everyone the Airtable pull created. This satisfies both spec clauses (the "check the mirror" clause and the "suppress for verified members" clause) with one check. *Recommended; alternative — mirroring an Airtable attendance table — is rejected as out of scope and unsupported by the current base schema.*
- **Decision B — keep the tag string `first-time-student`; do NOT rename to "First-Time Class Taker."** The spec asks for "First-Time Class Taker," but the shipped code and any live Mailchimp automations/segments key off `first-time-student` (`mailchimp_subscribe.py:49`). Renaming the tag silently would break existing automations and orphan already-tagged contacts. **Recommended:** keep `first-time-student` and treat "First-Time Class Taker" as the human-readable name of the *segment/automation in Mailchimp*, not the tag slug. If the product owner insists on the literal slug, that is a follow-up requiring a coordinated Mailchimp-side rename + a one-time re-tag migration — flagged in "Follow-up," not done here. **Confirm with the product owner before deviating.**
- **Decision C — suppression timestamp reuses the existing `UserProfile.subscribed_to_mailchimp_at`.** No new field. The checkbox is suppressed when (a) the user is authenticated and (b) their profile already has `subscribed_to_mailchimp_at` set. To keep that signal accurate across *both* opt-in paths, the class-registration bridge will also stamp the timestamp on success for logged-in users (today only the onboarding path does). Anonymous registrants always see the checkbox (we have no per-session opt-in store, and conflating it with `subscribed_to_mailchimp` on a per-registration row would be wrong — that's a dedup flag, not a per-person preference).
- **Suppression is UI-only and safe.** Even if a user who already opted in somehow submits with the box hidden/unchecked, the downstream `subscribe_registration` is idempotent and the Mailchimp upsert never duplicates — so suppression can never cause data loss, only avoids re-asking.

---

## File Structure

- Modify: `classes/services/mailchimp_subscribe.py` — add `_is_known_member()`; extend `derive_tags()` first-time logic to also suppress for known members.
- Modify: `classes/spec/services/mailchimp_subscribe_spec.py` — new contexts for member suppression and the Airtable-imported-member case.
- Modify: `classes/forms.py` — `RegistrationForm.__init__` drops `wants_newsletter` when the logged-in user already opted in (new `user` kwarg).
- Modify: `classes/views.py` — pass `user=request.user` into `RegistrationForm`; in `subscribe_registration` (or its caller), the timestamp stamp is handled in the service (next item).
- Modify: `classes/services/mailchimp_subscribe.py` — on successful subscribe, also stamp `UserProfile.subscribed_to_mailchimp_at` for a logged-in registrant (Task 4).
- Test: `classes/spec/forms/registration_form_spec.py` (extend or create — confirm exact filename first) — checkbox-suppression cases.
- Modify: `templates/classes/public/register.html` — guard the checkbox block with `{% if form.wants_newsletter %}` (field may be absent).
- Modify: `plfog/version.py` — version bump + member-friendly changelog entry.

---

## Task 1: Don't tag known members as first-time (Gaps #1 + #2)

**Files:**
- Modify: `classes/services/mailchimp_subscribe.py` (`derive_tags`, `:20–51`)
- Test: `classes/spec/services/mailchimp_subscribe_spec.py` (`describe_derive_tags`)

This is the heart of the change. A contact gets `first-time-student` **only** when they are *both* a local first-timer *and* not a known Past Lives Member.

- [ ] **Step 1: Write the failing tests.**

Append to `describe_derive_tags()` in `classes/spec/services/mailchimp_subscribe_spec.py`. Mirror the existing style (`RegistrationFactory`, no Mailchimp mock needed — `derive_tags` is pure DB logic).

```python
    def describe_member_suppression():
        def it_suppresses_first_time_for_a_verified_member(db):
            from allauth.account.models import EmailAddress
            from django.contrib.auth import get_user_model
            from tests.membership.factories import MemberFactory

            user = get_user_model().objects.create_user(
                username="known", email="known@example.com", password="x"
            )
            EmailAddress.objects.create(
                user=user, email="known@example.com", verified=True, primary=True
            )
            MemberFactory(user=user)
            reg = RegistrationFactory(email="known@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" not in tags

        def it_suppresses_first_time_for_an_airtable_imported_member(db):
            # Unlinked member imported from Airtable: email lives only in
            # Member._pre_signup_email, no User / EmailAddress yet.
            from tests.membership.factories import MemberFactory

            MemberFactory(user=None, _pre_signup_email="imported@example.com")
            reg = RegistrationFactory(email="imported@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" not in tags

        def it_suppresses_first_time_for_a_staged_member_email(db):
            from membership.models import MemberEmail
            from tests.membership.factories import MemberFactory

            member = MemberFactory(user=None, _pre_signup_email="primary@example.com")
            MemberEmail.objects.create(member=member, email="alias@example.com")
            reg = RegistrationFactory(email="alias@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" not in tags

        def it_still_tags_a_brand_new_non_member(db):
            reg = RegistrationFactory(email="stranger@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" in tags

        def it_matches_member_email_case_insensitively(db):
            from tests.membership.factories import MemberFactory

            MemberFactory(user=None, _pre_signup_email="Mixed@Example.com")
            reg = RegistrationFactory(email="mixed@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" not in tags
```

> Confirm `MemberFactory` accepts `user=None` and `_pre_signup_email=...` (check `tests/membership/factories.py`). If the kwarg is named differently, adjust — the field is `Member._pre_signup_email` (`membership/models.py:142`, DB column `email`). The existing `it_tags_first_time_students` / `it_does_not_tag_first_time_when_prior_confirmed_exists` (`:127`, `:132`) must keep passing unchanged.

- [ ] **Step 2: Run to verify it fails.**

Run: `pytest classes/spec/services/mailchimp_subscribe_spec.py -v -k member_suppression`
Expected: FAIL — known members currently still receive `first-time-student` (no member check exists).

- [ ] **Step 3: Implement `_is_known_member()` and use it in `derive_tags`.**

In `classes/services/mailchimp_subscribe.py`, add a helper and gate the tag on it. Union the three email stores so Airtable-imported members count:

```python
def _is_known_member(email: str) -> bool:
    """True when this email belongs to a known Past Lives Member.

    Unions the three email stores (see membership/CLAUDE.md): the member's
    stored pre-signup email (the Airtable-mirror value), any staged alias,
    and any verified allauth EmailAddress for a linked member. Anyone the
    Airtable pull created has a Member row, so this also satisfies the
    "check the email against the member registry" requirement without a
    separate Airtable lookup (Airtable mirrors no class history).
    """
    from membership.models import Member, MemberEmail

    if Member.objects.filter(_pre_signup_email__iexact=email).exists():
        return True
    if MemberEmail.objects.filter(email__iexact=email).exists():
        return True
    return Member.objects.filter(
        user__emailaddress__email__iexact=email,
        user__emailaddress__verified=True,
    ).exists()
```

Then change the first-time block in `derive_tags` (`:40–49`) so the tag is added only when there is **no** prior local confirmed registration **and** the email is not a known member:

```python
    prior_confirmed = (
        Registration.objects.filter(
            email__iexact=registration.email,
            status=Registration.Status.CONFIRMED,
        )
        .exclude(pk=registration.pk)
        .exists()
    )
    if not prior_confirmed and not _is_known_member(registration.email):
        tags.append("first-time-student")
```

Update the `derive_tags` docstring to note the member-registry suppression.

- [ ] **Step 4: Run to verify it passes.**

Run: `pytest classes/spec/services/mailchimp_subscribe_spec.py -v`
Expected: PASS (existing + 5 new).

- [ ] **Step 5: Lint + commit.**

```bash
ruff format classes/services/mailchimp_subscribe.py classes/spec/services/mailchimp_subscribe_spec.py
ruff check --fix classes/services/mailchimp_subscribe.py classes/spec/services/mailchimp_subscribe_spec.py
git add classes/services/mailchimp_subscribe.py classes/spec/services/mailchimp_subscribe_spec.py
git commit -m "Suppress first-time-student tag for known Past Lives members"
```

---

## Task 2: Confirm the tag string (Gap #3 — decision, no code unless approved)

**Files:** none (decision/verification) — code only if the product owner overrides Decision B.

The spec wants the tag "First-Time Class Taker"; the code uses `first-time-student` (`mailchimp_subscribe.py:49`). Per **Decision B**, we keep `first-time-student` to avoid breaking live automations and orphaning already-tagged contacts.

- [ ] **Step 1:** Verify the current literal is `first-time-student` (`mailchimp_subscribe.py:49`) and that no other code path emits a different first-time string:
  ```bash
  grep -rni "first.time" classes/ core/ --include="*.py" | grep -v spec
  ```
  Expected: the only first-time tag literal is `first-time-student`.
- [ ] **Step 2:** Record the decision in the PR description and surface it to the product owner. **Do not rename.** If — and only if — the owner explicitly approves the literal "First-Time Class Taker" slug, that becomes a separate follow-up (Mailchimp-side rename + bulk re-tag of existing contacts), not part of this branch. Note it under "Follow-up."

---

## Task 3: Stamp the suppression timestamp on the class-registration path (supports Gap #4)

**Files:**
- Modify: `classes/services/mailchimp_subscribe.py` (`subscribe_registration`, `:54–84`)
- Test: `classes/spec/services/mailchimp_subscribe_spec.py` (`describe_subscribe_registration`)

So the "already opted in" signal is accurate no matter which path subscribed the person, the class-registration success also stamps `UserProfile.subscribed_to_mailchimp_at` for a logged-in registrant (the onboarding path already does this — `mailchimp_account.py:70`). Anonymous registrants have no profile, so nothing to stamp.

- [ ] **Step 1: Write the failing tests.**

Append to `describe_subscribe_registration()`:

```python
    def it_stamps_profile_timestamp_for_a_logged_in_registrant(site_with_mailchimp):
        from django.contrib.auth import get_user_model
        from core.models import UserProfile

        user = get_user_model().objects.create_user(
            username="opt", email="opt@example.com", password="x"
        )
        profile = UserProfile.objects.create(user=user)
        reg = RegistrationFactory(wants_newsletter=True, member__user=user)
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ):
            subscribe_registration(reg)
        profile.refresh_from_db()
        assert profile.subscribed_to_mailchimp_at is not None

    def it_does_not_error_for_an_anonymous_registrant(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=True, member=None)
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ):
            subscribe_registration(reg)  # must not raise
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is True
```

> Confirm how a `Registration` links to a user. `Registration.member` is an FK (`classes/models.py:918`) and `Member.user` is the 1:1 (`membership/models.py:134`). If `RegistrationFactory` doesn't support `member__user=...`, build the member explicitly with `MemberFactory(user=user)` and pass `member=...`. The stamp should resolve the profile via `registration.member.user.profile` — adjust the helper to whatever the real linkage is, failing closed (no member/user/profile → no stamp).

- [ ] **Step 2: Run to verify it fails.**

Run: `pytest classes/spec/services/mailchimp_subscribe_spec.py -v -k "stamps_profile or anonymous_registrant"`
Expected: the stamp test FAILS (timestamp stays None); the anonymous test may already pass — keep it as a regression guard.

- [ ] **Step 3: Implement the stamp.**

In `subscribe_registration`, after the existing success block that sets `registration.subscribed_to_mailchimp = True` (`:82–83`), add a best-effort profile stamp:

```python
    registration.subscribed_to_mailchimp = True
    registration.save(update_fields=["subscribed_to_mailchimp"])

    _stamp_profile_subscribed(registration)


def _stamp_profile_subscribed(registration: Registration) -> None:
    """Mirror the opt-in onto the registrant's UserProfile when one exists.

    Keeps the 'already opted in' signal (used to hide the marketing checkbox)
    accurate across both the class-registration and account-signup paths.
    Fails closed: anonymous registrants have no profile, so nothing happens.
    """
    from django.utils import timezone

    member = registration.member
    user = getattr(member, "user", None) if member is not None else None
    profile = getattr(user, "profile", None) if user is not None else None
    if profile is None or profile.subscribed_to_mailchimp_at is not None:
        return
    profile.subscribed_to_mailchimp_at = timezone.now()
    profile.save(update_fields=["subscribed_to_mailchimp_at"])
```

- [ ] **Step 4: Run to verify it passes.**

Run: `pytest classes/spec/services/mailchimp_subscribe_spec.py -v`
Expected: PASS (all).

- [ ] **Step 5: Lint + commit.**

```bash
ruff format classes/services/mailchimp_subscribe.py classes/spec/services/mailchimp_subscribe_spec.py
ruff check --fix classes/services/mailchimp_subscribe.py classes/spec/services/mailchimp_subscribe_spec.py
git add classes/services/mailchimp_subscribe.py classes/spec/services/mailchimp_subscribe_spec.py
git commit -m "Stamp UserProfile.subscribed_to_mailchimp_at on class-registration opt-in"
```

---

## Task 4: Suppress the marketing checkbox for users who already opted in (Gap #4)

**Files:**
- Modify: `classes/forms.py` (`RegistrationForm.__init__`, `:552–587`)
- Modify: `classes/views.py` (`register`, the `RegistrationForm(...)` construction at `:456–464`)
- Modify: `templates/classes/public/register.html` (`:94–97`)
- Test: `classes/spec/forms/registration_form_spec.py` (confirm exact path first)

Validation/visibility belongs in the form, not the view (per coding standards). The form gains a `user` kwarg; when that user already has `subscribed_to_mailchimp_at`, it pops `wants_newsletter`. The template guards the block since the field may be absent.

- [ ] **Step 1: Write the failing form tests.**

Find the existing registration-form spec (the classes app keeps form specs in `classes/spec/forms/` — confirm the filename, likely `registration_form_spec.py`). Add a `describe_newsletter_checkbox_visibility()` block. Reuse the spec's existing pattern for building a valid `RegistrationForm` (it must pass `offering=`, `settings_obj=`; see `RegistrationForm.__init__` required kwargs at `classes/forms.py:553–562`).

```python
    def describe_newsletter_checkbox_visibility():
        def it_shows_checkbox_for_an_anonymous_user(db):
            form = _build_form(user=None)  # helper mirroring existing spec setup
            assert "wants_newsletter" in form.fields

        def it_shows_checkbox_for_a_user_who_has_not_opted_in(db):
            from django.contrib.auth import get_user_model
            from core.models import UserProfile

            user = get_user_model().objects.create_user(
                username="fresh", email="fresh@example.com", password="x"
            )
            UserProfile.objects.create(user=user, subscribed_to_mailchimp_at=None)
            form = _build_form(user=user)
            assert "wants_newsletter" in form.fields

        def it_hides_checkbox_for_a_user_who_already_opted_in(db):
            from django.contrib.auth import get_user_model
            from django.utils import timezone
            from core.models import UserProfile

            user = get_user_model().objects.create_user(
                username="opted", email="opted@example.com", password="x"
            )
            UserProfile.objects.create(user=user, subscribed_to_mailchimp_at=timezone.now())
            form = _build_form(user=user)
            assert "wants_newsletter" not in form.fields

        def it_shows_checkbox_for_a_user_with_no_profile(db):
            from django.contrib.auth import get_user_model

            user = get_user_model().objects.create_user(
                username="noprofile", email="np@example.com", password="x"
            )
            form = _build_form(user=user)
            assert "wants_newsletter" in form.fields
```

> Provide/extend a `_build_form(user=...)` helper in the spec that constructs `RegistrationForm` with the required `offering`/`settings_obj` kwargs (use `ClassOfferingFactory()` and `ClassSettings.load()`), matching whatever the file already does. If the file currently builds the form inline in each test, factor a small helper or inline the construction.

- [ ] **Step 2: Run to verify it fails.**

Run: `pytest classes/spec/forms/registration_form_spec.py -v -k newsletter_checkbox_visibility`
Expected: FAIL — `RegistrationForm.__init__` rejects the `user` kwarg / never pops the field.

- [ ] **Step 3: Add the `user` kwarg + suppression to the form.**

In `classes/forms.py`, add `user` to `RegistrationForm.__init__` (`:553–562`) and pop the field when the user already opted in. Do this near the existing field-pop logic (`:571–578`):

```python
    def __init__(
        self,
        *args,
        offering: ClassOffering,
        settings_obj: ClassSettings,
        member: "Member | None" = None,
        client_ip: str = "",
        is_waitlist: bool = False,
        user: "AbstractBaseUser | AnonymousUser | None" = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        ...
        if self._user_already_opted_in(user):
            # Don't re-ask a user who already opted in during a prior session.
            self.fields.pop("wants_newsletter", None)
        ...

    @staticmethod
    def _user_already_opted_in(user: "AbstractBaseUser | AnonymousUser | None") -> bool:
        if user is None or not user.is_authenticated:
            return False
        profile = getattr(user, "profile", None)
        return profile is not None and profile.subscribed_to_mailchimp_at is not None
```

> Match the existing import style for the auth types (the view already imports `AbstractBaseUser`/`AnonymousUser` — `classes/views.py`; the form may need `from __future__ import annotations` (already present) plus a `TYPE_CHECKING` import or a string annotation). Keep annotations consistent with the file.

- [ ] **Step 4: Pass the user from the view.**

In `classes/views.py` `register` (`:456–464`), add `user=request.user` to the `RegistrationForm(...)` call. (The view already has `request.user`; this is a one-line addition.)

- [ ] **Step 5: Guard the template.**

In `templates/classes/public/register.html` (`:94–97`), wrap the checkbox block so it renders only when the field is present:

```django
          {% if form.wants_newsletter %}
          <label class="reg-check" style="margin-top:4px">
            {{ form.wants_newsletter }}
            <span>{{ form.wants_newsletter.label }}</span>
          </label>
          {% endif %}
```

- [ ] **Step 6: Run to verify it passes.**

Run: `pytest classes/spec/forms/registration_form_spec.py -v`
Expected: PASS. Also re-run the view spec for `register` if one exists (`classes/spec/views/`) to confirm passing `user=` didn't break submission.

- [ ] **Step 7: Lint + commit.**

```bash
ruff format classes/forms.py classes/views.py classes/spec/forms/registration_form_spec.py
ruff check --fix classes/forms.py classes/views.py classes/spec/forms/registration_form_spec.py
git add classes/forms.py classes/views.py templates/classes/public/register.html classes/spec/forms/registration_form_spec.py
git commit -m "Hide marketing checkbox for registrants who already opted in"
```

---

## Task 5: Version bump + changelog

**Files:** `plfog/version.py`

Per project rule, every PR bumps `VERSION` and prepends a member-friendly `CHANGELOG` entry (this feeds the Discord release post — plain language, no jargon, no PR numbers).

- [ ] **Step 1: Determine the version.** The file currently reads `VERSION = "2.5.8"` (`plfog/version.py:5`), and **2.5.8 is in flight on PR #108**. **Verify the latest *merged* version on `main` before setting this** (`git fetch && git show origin/main:plfog/version.py | head`), then use the next patch after whatever has actually merged (likely `2.5.9` once #108 lands — do not assume). Set `date` to the **merge date**, not today.

- [ ] **Step 2: Bump + prepend the entry** (substitute the verified version/date):

```python
    {
        "version": "2.5.9",  # verify against merged main
        "date": "2026-06-18",  # set to merge date
        "title": "Smarter newsletter sign-up on class registration",
        "changes": [
            "When you register for a class and opt into email updates, we no longer label you a 'first-timer' if you're already a Past Lives member or have taken a class with us before.",
            "If you've already opted into our emails, the newsletter checkbox no longer shows up again when you register for another class.",
        ],
    },
```

- [ ] **Step 3: Commit.**

```bash
git add plfog/version.py
git commit -m "Bump version and add changelog entry for Mailchimp tagging improvements"
```

---

## Final verification

- [ ] **Full suite:** `pytest` — all pass, 100% coverage. The new helpers (`_is_known_member`, `_stamp_profile_subscribed`, `_user_already_opted_in`) are each exercised by Tasks 1, 3, 4. Watch for any uncovered branch (e.g. the `MemberEmail` arm of `_is_known_member`, the no-profile arm of the stamp) — the tests above hit each, but confirm with `--cov`.
- [ ] **Lint/format/type-check:** `ruff format . && ruff check . && mypy .` — clean. (`mypy` needs `DATABASE_URL` — `export $(grep '^DATABASE_URL=' .env | xargs)` first if running before push.)
- [ ] **Manual smoke (run skill), with Mailchimp creds set in `SiteConfiguration`:**
  - Anonymous registrant for a free class with the box checked → contact appears in Mailchimp; first-time tag present for a never-seen email, absent for an email matching a Member (`_pre_signup_email`).
  - Register the *same* email twice → no duplicate contact (existing upsert), and the second registration carries no `first-time-student` tag.
  - Log in as a user with `subscribed_to_mailchimp_at` set → the newsletter checkbox is gone on the registration form; a fresh logged-in user still sees it, and opting in stamps the profile so it's hidden next time.

---

## Follow-up (out of scope for this plan)

- **Literal "First-Time Class Taker" tag slug.** Only if the product owner overrides Decision B. Requires a coordinated Mailchimp-side rename of the existing `first-time-student` tag plus a one-time re-tag of already-tagged contacts, then a code change to the literal. Not a code-only change — do not attempt it piecemeal on this branch.
- **Mirroring real class-attendance history from an external system.** If the org ever wants true cross-system "have they *attended* before" (not just "are they a member"), that needs a new data source — Airtable's current base ("PLM Members & Studios 2026") has no attendance table (`airtable_sync/config.py:19–23`). Out of scope; flagged honestly because the original spec assumes such history exists in the mirror, and it does not.
- **Per-session opt-in suppression for anonymous users.** The checkbox suppression is keyed off `UserProfile`, which anonymous registrants don't have, so they always see it. A cookie/session-based suppression for repeat anonymous registrants is possible but low-value and was not requested.
