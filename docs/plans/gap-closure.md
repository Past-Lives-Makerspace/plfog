# Implementation Plan — Closing the Six Review Gaps on `v2.0-public-booking-subdomain`

**Status:** Ready — open questions resolved 2026-05-23 (see end of doc).
**Scope:** Six features identified as gaps in the PR #94 review.
**Delivery:** **One PR on the existing `v2.0-public-booking-subdomain` branch.** All six gaps land together. Single version bump (`2.0.6`), single changelog entry combining all six themes.
**Author:** Plan generated 2026-05-22. Decisions captured 2026-05-23.

---

## Background

PR #94 (`v2.0-public-booking-subdomain`) shipped the book.pastlives.space subdomain, lite account dashboard, guest lookup, and onboarding wizard. A subsequent feature-verification review found six additional asks were partially or completely missing:

| # | Feature | Review status |
|---|---|---|
| 1 | Image upload revamp (gallery, preview, auto-resize, hero cropper) | Partial — gallery/preview shipped, auto-resize and hero cropper missing |
| 2 | Mailchimp tagging by criteria | Partial — infra accepts tags, only `class-registrant` ever applied |
| 3 | Mailchimp push from onboarding/signup | Partial — registration & newsletter wired, account signup is not |
| 4 | Simplybook tour-status integration | Absent |
| 5 | Instructor sees signups + manual email | Partial — read-only roster exists, composer missing |
| 6 | Customizable registration questions | Absent |

This plan closes all six.

---

## Implementation Order

Sequenced to minimize rework within a single PR.

1. **Gap 1 — Image auto-resize + cropper.** Touches `ClassOffering.image`, validators, form widgets. Lands the `normalize_image` helper that later gaps inherit.
2. **Gap 2 — Mailchimp tag derivation.** Adds `Category.guild` FK + `derive_tags(registration)` helper. Reused by Gap 3.
3. **Gap 3 — Mailchimp push from onboarding.** Reuses tag-derivation patterns and `MailchimpClient.from_site_config()` infra.
4. **Gap 4 — Simplybook integration.** Independent new module. Adds env vars and `UserProfile` fields.
5. **Gap 6 — Custom registration questions.** Models + form + display. Do before the email composer so the composer table can show answers.
6. **Gap 5 — Instructor manual email composer.** Last — depends on the Gap 6 answers table.

### Single-PR mechanics
- One commit per gap (or one tight group per gap) to keep the history readable on the same branch.
- Migrations numbered in implementation order; verify `makemigrations --check` is clean before each commit.
- `plfog/version.py` bumped **once** at the end to `2.0.6` with a combined changelog entry: e.g. *"Class photos now auto-resize with a hero crop tool. Newsletter tagging picks up your guild, category, and instructor. Account signups sync to the newsletter. Tour status pulls in from Simplybook. Registration questions you can customize. Instructors can email their students from the registrations page."*
- `.github/workflows/discord-notify.yml` will post one combined Discord message on merge — acceptable since this is a known multi-feature release.

---

## Gap 1 — Image auto-resize + hero cropper

### Module changes
- **New** `core/images.py` — pure functions, no Django deps: `normalize_image(file, *, max_long_edge: int, format: str = "JPEG", quality: int = 85) -> ContentFile`. Strips EXIF, converts mode to RGB (handles HEIC via `pillow-heif` registered at module import), resizes with `Image.thumbnail`, returns a fresh `ContentFile`.
- **Edit** `core/validators.py` — keep `validate_image_size` as the hard upper bound (raises before normalize runs).

**Cropper approach:** store crop box + apply via CSS `object-position` / `object-fit: cover`. No server-side cropped variant.

### Model changes (`classes/models.py`)
- `ClassOffering.image` — extend existing `save()` to call `normalize_image(self.image, max_long_edge=2400)` when the field is dirty.
- `ClassImage.image` — same hook, `max_long_edge=1600`.
- `Category.hero_image` and `Instructor.photo` — same, `max_long_edge=2400` / `1200`.
- New cropper output fields on `ClassOffering`: `hero_crop_x`, `hero_crop_y`, `hero_crop_w`, `hero_crop_h` (PositiveIntegerField, null=True, blank=True, `help_text="Crop box in source-image pixels — set by the hero cropper."`).
- Extend `display_images` to optionally include the crop rectangle so templates apply CSS `object-position` / `object-fit: cover`.

### Form / template / static
- **Edit** `classes/forms.py` — `ClassForm` gets a hidden `hero_crop` JSON field bound to the four crop ints.
- **Edit** `templates/classes/instructor/class_form.html` (and admin equivalent) — load Cropper.js vendored at `static/vendor/cropper/`, wire it to the hero image input, emit the crop box into the hidden field on change.
- **Add** `static/js/hero_cropper.js` — thin glue layer.

### Migration
- `classes/migrations/0017_hero_crop_fields.py` — additive, schema-only, naturally reversible.

### Tests (BDD `*_spec.py`)
- `tests/core/images_spec.py` — `describe_normalize_image` with `it_resizes_when_over_max`, `it_strips_exif`, `it_passes_through_when_small`, `it_handles_rgba_png`.
- `classes/spec/models/classoffering_image_spec.py` — `it_resizes_hero_on_save`, `it_stores_crop_box`, `it_does_not_resize_when_unchanged`.
- Use a real in-memory Pillow image via `BytesIO`; never mock Pillow.

### Settings / env
- New env vars `IMAGE_MAX_LONG_EDGE_HERO` (default 2400), `IMAGE_MAX_LONG_EDGE_GALLERY` (default 1600) in `plfog/settings.py`.

### Migration safety
- Schema-only; safe everywhere. Existing images are untouched (no backfill) — they'll get resized on next save. Optional follow-up: a `management/commands/renormalize_class_images.py` one-shot backfill, not blocking this PR.

---

## Gap 2 — Mailchimp tag derivation

### Prerequisite model change
- **Edit** `classes/models.py` — add `Category.guild = models.ForeignKey("membership.Guild", null=True, blank=True, on_delete=models.SET_NULL, related_name="categories", help_text="Optional link to the makerspace Guild that owns this category. Used for Mailchimp tagging.")`.
- Migration `classes/migrations/00XX_category_guild.py` — additive, nullable; existing rows keep guild=NULL.

### Module changes
- **Edit** `classes/services/mailchimp_subscribe.py`:
  - Add `def derive_tags(registration: Registration) -> list[str]:` returning `["class-registrant", f"category-{registration.class_offering.category.slug}", f"instructor-{registration.class_offering.instructor.slug}"]`, plus `f"guild-{category.guild.slug}"` when `category.guild_id` is set (use `slugify(category.guild.name)` since `Guild` has no slug field; cache the slugified value on the model later if hot), plus `"first-time-student"` when `Registration.objects.filter(email__iexact=registration.email, status=Registration.Status.CONFIRMED).exclude(pk=registration.pk).count() == 0`.
  - Replace the hardcoded `tags=["class-registrant"]` with `tags=derive_tags(registration)`.

### Where applied
- `classes/views.py` (free flow) — already calls `subscribe_registration`, picks up new tags transparently.
- `classes/webhook_handlers.py` (paid flow) — same.

### Tests
- **Edit** `classes/spec/services/mailchimp_subscribe_spec.py`:
  - `it_tags_first_time_students`
  - `it_does_not_tag_first_time_when_prior_confirmed_exists`
  - `it_includes_category_slug_tag`
  - `it_includes_instructor_slug_tag`
  - `it_includes_guild_tag_when_category_has_guild`
  - `it_omits_guild_tag_when_category_has_no_guild`
- Use `RegistrationFactory` and assert the `tags=...` kwarg passed to the mocked `MailchimpClient.subscribe`.

### Migration safety
- One additive nullable FK on `Category` + code changes. Reversible.

---

## Gap 3 — Mailchimp push from onboarding

### Where the hook lives
**Pick onboarding step-3 completion**, not the allauth adapter. Justification: (a) the adapter runs before the user has filled in any persona info, so we'd push an under-tagged contact and then need a second sync; (b) onboarding step 3 is the natural "I'm in" moment; (c) it gives us `UserProfile.referral_source`, `interest_category_slugs`, and `first_attendance_status` to tag with.

### Module changes
- **New** `core/services/mailchimp_account.py` — `subscribe_user(user) -> None` builds tags `["account-signup"]` plus `f"persona-{profile.first_attendance_status}"`, plus `f"interest-{slug}"` for each `profile.interest_category_slugs`, plus `f"referral-{profile.referral_source}"`. Idempotent; skips when no `UserProfile` or no Mailchimp config; sets `UserProfile.subscribed_to_mailchimp_at` on success.
- **Edit** `classes/account/views.py` — in `OnboardingStepView.form_valid`, after `profile.onboarding_completed_at = timezone.now()` and save, call `subscribe_user(self.request.user)`. Must not raise.

### Model changes
- **Edit** `core/models.py` — add `UserProfile.subscribed_to_mailchimp_at = models.DateTimeField(null=True, blank=True, help_text="Stamp set when account-signup push to Mailchimp succeeded.")`
- Migration `core/migrations/00XX_userprofile_subscribed_to_mailchimp_at.py` — additive.

### Tests
- `tests/core/services/mailchimp_account_spec.py` — `describe_subscribe_user` with `it_pushes_with_account_signup_tag`, `it_includes_referral_tag_when_set`, `it_includes_interest_tags_per_slug`, `it_is_idempotent_on_resubscribe`.

### Settings / env
- Reuses existing `SiteConfiguration.mailchimp_*`.

### Backfill
- **No backfill.** Existing onboarded users won't get the `account-signup` tag — only new completions from the date this ships.

### Migration safety
- Additive only; reversible by `RemoveField`.

---

## Gap 4 — Simplybook integration for tour status

### Push vs pull — recommended approach
**Pull on demand + cache on UserProfile.** Justification: signups are infrequent and we only need tour status for a handful of views (account overview, instructor roster). Pushing every user write to Simplybook adds failure modes for no benefit. Pull-with-cache means one HTTP call per stale user per day.

### Module changes
- **New** `core/integrations/simplybook.py` modeled on `mailchimp.py`:
  - `SimplybookConfig` dataclass (api_key, company_login) read from env.
  - `SimplybookClient` with `.enabled`, `.has_completed_tour(email: str) -> bool`, and `.upsert_client(email, first_name, last_name) -> bool`. Uses `requests`, 5s timeout, returns False on any error. Auth via documented JSON-RPC token flow — implemented inline to avoid an unmaintained SDK.

### Model changes
- **Add** to `core/models.py`:
  - `UserProfile.completed_tour_at = models.DateTimeField(null=True, blank=True, help_text="Cached from Simplybook; refreshed by the tour-status sync.")`
  - `UserProfile.tour_status_checked_at = models.DateTimeField(null=True, blank=True, help_text="Last time Simplybook was polled for this user.")`

### Service + management command
- **New** `core/services/tour_status.py` — `refresh_if_stale(user, *, max_age_hours: int = 24) -> None`. Called lazily from `classes/account/views.py` `OverviewView.get_context_data` and from the onboarding completion hook.
- **New** `core/management/commands/sync_tour_status.py` — nightly cron-friendly bulk refresh of users with `tour_status_checked_at < now - 24h`.

### Tests
- `tests/core/integrations/simplybook_spec.py` — mock with `respx`, assert request shape, header auth, 5s timeout, returns False on 401/network error.
- `tests/core/services/tour_status_spec.py` — `it_refreshes_when_never_checked`, `it_skips_when_recently_checked`, `it_writes_completed_tour_at_when_found`.

### Settings / env
- New: `SIMPLYBOOK_API_KEY`, `SIMPLYBOOK_COMPANY_LOGIN`. Documented in `plfog/settings.py` under the integrations section.

### Migration safety
- Additive `UserProfile` fields; standard reversible migration.

---

## Gap 6 — Custom registration questions

**Scope:** Global questions only — every active question is asked on every registration. Admin maintains the list. No per-class or per-category attachment.

### Model changes (`classes/models.py`)
- **New** `RegistrationQuestion`:
  - `class QuestionType(TextChoices): SHORT_TEXT, LONG_TEXT, YES_NO, SINGLE_CHOICE`.
  - `prompt = CharField(max_length=500, help_text="The question shown to the registrant.")`
  - `question_type = CharField(choices=QuestionType.choices, default=QuestionType.SHORT_TEXT)`
  - `choices_json = JSONField(default=list, blank=True, help_text="Options for SINGLE_CHOICE; ignored otherwise.")`
  - `is_required = BooleanField(default=False)`
  - `is_active = BooleanField(default=True, help_text="Uncheck to retire a question without deleting historical answers.")`
  - `sort_order = PositiveIntegerField(default=0)`
  - `__str__` returns the prompt truncated.
  - `Meta.ordering = ["sort_order", "id"]`.
- **New** `RegistrationAnswer`:
  - `registration` FK (CASCADE), `question` FK (PROTECT), `answer_text` TextField.
  - `UniqueConstraint(fields=["registration", "question"])`.
  - Index on `registration`.

### Form changes (`classes/forms.py`)
- `RegistrationForm.__init__` — query `RegistrationQuestion.objects.filter(is_active=True)`, dynamically inject one Django form field per question (`forms.CharField`, `Textarea`, `BooleanField`, `ChoiceField`) named `custom_q_<pk>`.
- `clean()` validates required answers.
- `save()` writes `RegistrationAnswer` rows in a single `bulk_create` inside a transaction.

### View / template
- **Edit** `templates/classes/public/register.html` — render the dynamic fields in their own fieldset.
- **Edit** `templates/classes/instructor/registrations.html` — expose answers per row (collapsible).
- Admin: register `RegistrationQuestion` as a standalone `ModelAdmin` in `classes/admin.py` with list_display showing prompt/type/required/active/sort_order.

### Migration
- `classes/migrations/0018_registration_questions.py` — schema-only.

### Tests
- `classes/spec/models/registration_question_spec.py` — `it_orders_by_sort_order`, `it_protects_question_with_existing_answers`.
- `classes/spec/forms/registration_form_custom_questions_spec.py` — `it_injects_active_questions`, `it_skips_inactive_questions`, `it_validates_required_fields`, `it_persists_answers_on_save`.

### Migration safety
- Pure additive; reversible.

---

## Gap 5 — Instructor manual email composer

### Model changes (`classes/models.py`)
- **New** `InstructorMessage`:
  - `instructor` FK, `class_offering` FK, `subject` (max 255), `body` TextField, `recipient_count` PositiveIntegerField, `sent_at` auto_now_add, `bcc_self` BooleanField default True.
- **New** `InstructorMessageRecipient`:
  - `message` FK, `registration` FK (PROTECT), `email` (snapshot at send time for audit).

### Form / view / URL
- **New** `classes/forms.py:InstructorEmailForm` — `subject`, `body`, `registration_ids = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple)`. `clean_registration_ids` filters against `Registration.objects.filter(class_offering__instructor=request.instructor)` — the same queryset-scoping pattern the codebase uses elsewhere. (Skipping django-guardian; not currently in `requirements.txt`.)
- **New URL** in `classes/urls.py`: `path("instructor/registrations/email/", views.instructor_registrations_email, name="instructor_registrations_email")`.
- **New view** in `classes/views.py` — POST-only, builds an `EmailMessage` with `bcc=recipient_emails`, `to=[instructor.user.email]`, uses `settings.DEFAULT_FROM_EMAIL`. On success creates the `InstructorMessage` + `InstructorMessageRecipient` rows in one transaction.

### Template
- **Edit** `templates/classes/instructor/registrations.html` — add checkboxes per row, an inline compose-panel below the table, send button. Display custom-question answers (Gap 6) under each row so the instructor knows who they're writing to.

### Tests
- `classes/spec/views/instructor_email_spec.py` — `it_rejects_when_user_is_not_the_classs_instructor`, `it_rejects_recipients_outside_my_classes`, `it_sends_with_bcc_and_logs_audit_row`, `it_handles_empty_recipient_list_with_form_error`.
- Use Django's `mail.outbox`, never mock the mail backend.

### Settings / env
- Reuses existing `EMAIL_BACKEND` and `DEFAULT_FROM_EMAIL`. No new vars.

### Migration safety
- Additive; reversible. `RegistrationAnswer` from Gap 6 must land first if you want the table to show answers — sequencing matches the suggested order.

---

## Cross-Cutting Concerns

### New dependencies
- **Pillow** — already in `requirements.txt`. No new pip dep.
- **pillow-heif** — **added now** to `requirements.txt`. Registered at `core/images.py` module import so Pillow opens HEIC transparently. Needs `libheif` system package on Hetzner/Render — document in deploy notes.
- **Cropper.js** — vendor at `static/vendor/cropper/cropper.min.{js,css}` (MIT). No npm/build step added.
- **Simplybook** — no SDK; bare `requests` like Mailchimp. Session token cached in-process per worker (module-level dict keyed by api_key with expiry).
- **django-guardian** — **not** added. Existing queryset-scoping pattern is consistent across the app; introducing guardian for one feature breaks the principle of one auth pattern.

### Env vars to add (document in `plfog/settings.py`)
- `IMAGE_MAX_LONG_EDGE_HERO` (default 2400)
- `IMAGE_MAX_LONG_EDGE_GALLERY` (default 1600)
- `SIMPLYBOOK_API_KEY`
- `SIMPLYBOOK_COMPANY_LOGIN`

### Version bump strategy
**One PR, one version bump.** `plfog/version.py` goes from current → `2.0.6` at the end of implementation, with a single combined `CHANGELOG` entry covering all six themes in member-friendly language. Discord webhook posts one announcement on merge.

### Testing posture
- Each new module gets a `*_spec.py` peer in `tests/<app>/` or `classes/spec/<area>/`.
- Externals mocked with `respx` (Mailchimp, Simplybook). Never mock the DB; use `factory-boy`.
- Target 100% line + branch coverage on new modules.

---

## Decisions Log (resolved 2026-05-23)

1. **Guilds vs Categories** — `Guild` already exists in `membership/models.py` but has no FK to classes. **Decision:** add nullable `Category.guild` FK; emit `guild-<slug>` tag when present. (See Gap 2.)
2. **Custom questions scope** — **Decision:** global only. No per-class or per-category attachment. Every active question is asked on every registration. (See Gap 6.)
3. **Hero crop rendering** — **Decision:** store crop box, apply via CSS `object-position`. No server-side cropped variant. (See Gap 1.)
4. **HEIC support** — **Decision:** add `pillow-heif` now. Document `libheif` system dep for Hetzner/Render. (See Cross-Cutting.)
5. **Simplybook auth** — **Decision:** cache session token in-process per worker. No Django cache dependency. API key remains an env var. (See Gap 4.)
6. **Onboarding push backfill** — **Decision:** no backfill. Only new onboarding completions push to Mailchimp. (See Gap 3.)
7. **Instructor email composer rate limit** — **Decision:** no rate limit. Add later if abuse appears. (See Gap 5.)

---

## Critical Files (for implementation reference)
- `classes/models.py`
- `classes/services/mailchimp_subscribe.py`
- `classes/views.py`
- `classes/account/views.py`
- `classes/forms.py`
- `core/integrations/mailchimp.py`
- `core/models.py`
- `core/validators.py`
- `plfog/settings.py`
- `plfog/version.py`
