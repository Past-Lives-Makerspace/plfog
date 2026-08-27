# Guild Orientation & Announcement Polish — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-27
**Surface:** FOG hub (`pastlives.test:8000`) — guild detail page (`/guilds/<slug>/`, both member hub and public guilds surface), guild edit page (`/guilds/<pk>/edit/`), the member "Suggest an announcement" page.
**Related:** Polishes surfaces shipped by `2026-08-26-orienter-availability.md` (the Orientations tab hours editors and "All Orientation Hours" overview) and `2026-08-26-paid-orientations.md` (the booking partial's paid states). Neither is re-opened; this round only moves, renames, and re-plumbs their UI.

---

## 1. Summary

Four small quality-of-life fixes for guild pages and guild settings. Members get a dedicated **Orientations** tab on every guild page instead of hunting for the booking section under the Guild Calendar. Guild leads get an **Announcements** tab (renamed from "Announcements/Emails") with a new switch to turn off member announcement suggestions for their guild. The now-defunct **Welcome email** (its trigger, "Join This Guild", was removed last round) is deleted outright, and the **Thank-you email** editor moves to the Orientations tab where the orientation-lifecycle email belongs, with its own card and Save. And the lead's **Edit Hours** flow stops reloading the whole page with a `?orienter=` querystring: it opens a modal instead, under a card now titled **Orientation Schedule**.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Orientations tab position on guild detail | Between Guild Calendar and Buyables, rendered under the same `{% if show_orientation %}` gate as the booking partial. |
| Legacy deep links | `?tab=orientations` maps to the new tab; a `#guild-orientation` hash also lands on the new tab (that anchor previously implied the schedule tab). `?tab=schedule` keeps showing the calendar. |
| Where the suggestion toggle lives | New boolean on **`Guild`** (it is an announcements concern, not an orientations one), `allow_member_announcement_suggestions`, default `True`. |
| Toggle enforcement | Belt and braces: the guild page hides the button AND the propose form's guild picker excludes disabled guilds, so a hand-crafted POST fails validation server side. |
| Email scope (revised) | **Delete the Welcome email entirely; move the Thank-you email to the Orientations tab.** The Welcome email fired from `member_joined_guild` on guild join — "Join This Guild" was removed last round, so the feature is dead code. The Thank-you email fires from `complete_orientation` (`membership/orientations.py:770`) and IS the orientation-lifecycle email, so it belongs on the Orientations tab. The old combined "Guild follow-up emails" form (both cards, Announcements tab) disappears — Welcome deleted, Thank-you moved. |
| Thank-you card mechanism | The Thank-you card gets its own form + Save on the Orientations tab, posting to the existing `hub_guild_emails_save` with a single hidden `form_id="thankyou_email"` discriminator (house multi-form-per-endpoint pattern, `hub/views.py:5276`). No form split is needed anymore — there is only one email form now. |
| Edit Hours modal submit | The modal form posts via HTMX. Invalid → the bound form partial re-renders **inside the modal**. Valid → the view saves exactly as today and answers with an `HX-Redirect` to `?tab=orientations`, so the page reloads and the existing flash message shows. |
| Modal formset prefix | `modal_rules` (never `rules`) — the page always renders the viewer's own `rules`-prefixed My Orientation Hours formset, so a second `rules` formset in the modal would duplicate every DOM id. A posted `formset_prefix` field, whitelisted server side, tells the save view which prefix to bind. |
| Modal dismissal | `components/modal.html` gains an opt-in `modal_static` parameter that drops the `@click.outside` and `@keydown.escape.window` handlers; the Edit Hours modal uses it. Without it, the **teleported** delete-confirm (`confirm_modal.html:44` is `<template x-teleport="body">`) sits outside the hours modal's DOM subtree, so Alpine's teleport-blind `click.outside` treats every confirm click — Confirm, Cancel, backdrop — as outside and closes the editor; Escape would also close both layers at once. |
| `?orienter=` page-state flow | Removed entirely (dead code cleanup): the `_guild_edit_context` orienter-param branch, `hours_editing_other`, and the "Editing X's Hours" template branch all go. |
| "My Orientation Hours" card | Unchanged — self-editing stays inline. |
| Versioning | No VERSION or changelog work in this spec (handled by the round that ships it). |

## 2. What already exists (reuse, don't reinvent)

All confirmed in the codebase 2026-08-27.

| Need | Existing thing | Location |
|---|---|---|
| Guild detail tab machinery | Pure Alpine `x-data="{ section: 'overview' }"` + `x-init` legacy `?tab=` mapping; every panel stays in the DOM (`x-show`) | `templates/hub/guild_detail.html:116-126` |
| Orientation booking UI (all member states) | `hub/partials/guild_orientation.html` — oriented / hold / booked / paused / slot list / custom request | included at `guild_detail.html:447` inside the schedule panel |
| `show_orientation` context flag | `orientation is not None and orientation.is_enabled` | `hub/views.py:544,609` |
| Overview "Join an Orientation" button | sets `section = 'schedule'` + scrolls to `#guild-orientation` | `guild_detail.html:305-310` |
| Member "+ Suggest an announcement" button | shown to any authenticated non-editor | `guild_detail.html:151-153` |
| Propose view + form | `propose_guild_announcement` (`hub/views.py:3653`), `GuildAnnouncementProposalForm` — guild picker queryset `Guild.objects.filter(is_active=True)` (`hub/forms.py:2269`), `fixed_guild` lookup `hub/views.py:3673` | as noted |
| Guild edit tab bar | Alpine section from `URLSearchParams` or `active_tab` context | `templates/hub/guild_edit.html:5,14-25` |
| Follow-up emails form + save | `GuildEmailsForm` (`hub/forms.py:1584`, six fields, enable-requires-subject+body for welcome, `*_updated_at` stamping) posting to `guild_emails_save` (`hub/views.py:3034`) | template `guild_edit.html:897-917` |
| Welcome email (being DELETED) | `member_joined_guild` sends it (`membership/orientations.py:944-980`, one `emit_with_email_shell` that also fires the lead-only "New follower" in-app notice + `GUILD_JOINED` activity); fields `join_email_*` on `GuildOrientationSettings` (`membership/models.py:8181-8192`); `join_email_ready` property (`:8218`); templates `membership/emails/guild_welcome.{html,txt}` | removal specced in §5/§6.3 |
| Thank-you email (being MOVED) | `complete_orientation` sends it (`membership/orientations.py:770-799`); fields `thankyou_email_*` + `resolved_thankyou_subject/body` (`membership/models.py:8165-8215`), standard-copy fallback so enabling needs no subject/body; templates `orientation_thankyou.{html,txt}` — all UNCHANGED, only the editor UI moves | `membership/orientations.py`, `membership/models.py` |
| Thank-you email send | `complete_orientation` — fires when an orientation is marked complete; on by default with standard-copy fallback | `membership/orientations.py:770-799` |
| Email fields | `GuildOrientationSettings.thankyou_email_* / join_email_*` | `membership/models.py:8165-8192` |
| Hours formset + editor | `OrientationAvailabilityFormSet` (`extra=0`, `can_delete`, prefix `rules`) + the canonical clone-template editor | `hub/forms.py:1686`; `guild_edit.html:387-463` |
| Hours save (scope-aware) | `guild_orientation_hours_save` — `orienter_scope` picks prefix `rules` (personal) vs `guild_rules` (legacy), gate `can_edit_orienter_hours`, redirect `?tab=orientations` on success | `hub/views.py:938-993` |
| "All Orientation Hours" overview + Edit Hours links | per-staffer rows + `?tab=orientations&orienter=<pk>` anchors (active `:475`, former `:496`) | `guild_edit.html:466-507` |
| `?orienter=` page-state plumbing (to be removed) | orienter-param branch, `hours_editing_other`, `hours_scope_member` | `hub/views.py:701-723,783-785`; template `:392-394` |
| Generic modal | `components/modal.html` — `open-modal`/`close-modal` events, `#<id>-body` designed for HTMX, sizes sm/md/lg, Escape + click-outside + close button, `role="dialog" aria-modal` | `templates/components/modal.html` |
| Form-in-modal + HTMX precedent | `hub/partials/_propose_modal.html` (hx-post, closes on successful `htmx:after-request`); product modal (`guild_detail.html:696-716`) | as noted |
| `form_id` multi-form endpoint pattern | `user_settings` (`hub/views.py:1951,1983,2097`), member edit (`:5286`) | `hub/views.py` |
| Toggle / form field / confirm components | `components/toggle.html`, `form_field.html`, `confirm_modal.html` (incl. `confirm_js` used by the hours delete flow at `guild_edit.html:418-420`) | `templates/components/` |
| Help bubbles + articles | `data-help-key` registry (`core/help_registry.py`), member help articles + ShotSpecs (`membership/help_content.py`) | as noted |
| Modal CSS | `.pl-modal` max-height 85vh with internal scroll; `.pl-modal--lg` 720px | `static/css/components.css:22-34` |
| Tab bar overflow | `.pl-tabs` scrolls horizontally on narrow screens (no wrap, hidden scrollbar) | `static/css/hub.css:21-31` |

**Genuine gaps (all small):**

1. No per-guild boolean to disable member suggestions — the one new model field (§4).
2. No endpoint that returns an hours-edit form partial — one new GET view + partial (§6.4).
3. No announcements-side settings form — one tiny new ModelForm + endpoint (§6.2).

## 3. Where the code lives

```
membership/
  models.py                 Guild.allow_member_announcement_suggestions (new field);
                            DELETE GuildOrientationSettings.join_email_* fields + join_email_ready property
  migrations/0144_*.py      the new boolean (auto-reversible AddField)
  migrations/0145_*.py      RemoveField x4 dropping the join_email_* columns (see §4 data-loss note)
  orientations.py           strip the welcome-email send from member_joined_guild (keep the notice + activity)
  emails/guild_welcome.html, guild_welcome.txt   DELETE both templates
  emails/_button.html       reword the ":2" comment that references the deleted guild_welcome.html (nit #6)
  help_content.py           article copy + ShotSpec updates + welcome-email copy removal (§6.6)
  example_guild.py          seeded example-announcement copy rename + drop join_email_* dict keys (§6.6)
core/
  triggers.py               add no_email flag to Trigger; set no_email=True on the guild_joined trigger (§5 #4)
  events/registry.py        _channels_from_trigger: skip the EMAIL spec when trigger.no_email (§5 #4)
  management/commands/demo_data.py   DELETE the `if not obj.join_email_subject:` seed block (:711-715) — runs in CI
hub/
  views.py                  guild_detail x-init ctx unchanged; _guild_edit_context cleanup;
                            guild_emails_save (thankyou-only form_id dispatch); new guild_announcement_settings_save;
                            new guild_orientation_hours_form; guild_orientation_hours_save (prefix whitelist + HTMX answers)
  forms.py                  GuildEmailsForm → GuildThankyouEmailForm (thankyou fields only; drop all join_email_*);
                            new GuildAnnouncementSettingsForm; GuildAnnouncementProposalForm queryset filter
  urls.py                   two new paths (announcement-settings save, hours form partial)
templates/hub/
  guild_detail.html         new Orientations tab + panel; button/empty-state gating
  guild_edit.html           tab rename; DELETE the whole Announcements "Guild follow-up emails" section;
                            Thank-you card added to Orientations tab; Orientation Schedule card + modal trigger;
                            remove hours_editing_other branch
  partials/_orienter_hours_modal_form.html   (new) the modal's formset partial
templates/components/
  modal.html                new optional modal_static parameter (§6.4 — omitted = today's behavior)
  help_registry.py          no key changes (verified — see §6.6)
  spec/rich_email_templates_spec.py   DELETE describe_guild_welcome_email (§9)
tests/e2e/email_gallery/
  registry.py               DELETE the guild_welcome GalleryEmail entry (:471-488) (§9)
  context.py                drop join_email_* kwargs in build_sample_data (:82-84); DELETE guild_welcome_context (:523-534) (§9)
tests/                      email-gallery parity + welcome-removal sweep per §9
```

No new apps; everything stays inside the current coverage and mypy scope.

## 4. Data model

**Add** one field on `Guild` (`membership/models.py:1589`):

| Field | Type | Notes |
|---|---|---|
| `allow_member_announcement_suggestions` | `BooleanField(default=True)` | `help_text="Let members suggest announcements for this guild from its guild page."` |

**Remove** four fields from `GuildOrientationSettings` (`membership/models.py:8181-8192`) — the now-defunct Welcome email:

| Field dropped | Type |
|---|---|
| `join_email_enabled` | `BooleanField` |
| `join_email_subject` | `CharField` |
| `join_email_body` | `TextField` |
| `join_email_updated_at` | `DateTimeField` |

Also delete the `join_email_ready` property (`membership/models.py:8218-8220`). The `thankyou_email_*` fields and the `resolved_thankyou_subject` / `resolved_thankyou_body` / `is_paid` / `is_accepting` properties stay untouched (none of them reference `join_email_*`).

**Migrations (two, one logical change each — house rule):**

- `0144_guild_allow_member_announcement_suggestions` — a plain `AddField`, automatically reversible (reverse = `RemoveField`); no data migration needed since `default=True` preserves today's behavior for every guild.
- `0145_remove_guild_welcome_email` — four `RemoveField` operations dropping the `join_email_*` columns. Django auto-generates the `AddField` reverse, so the migration *runs* backward cleanly, **but the column drop is destructive: any welcome-email subject/body a guild had stored is lost and a reverse migration restores empty columns, not the old copy.** This is acceptable — the feature is dead (no trigger fires it since "Join This Guild" was removed), and the fields are lead-authored config, not member records. Call it out in the PR description so nobody expects a rollback to recover text.
- No index on the new boolean (never filtered in bulk hot paths; the propose-form queryset filter is a simple boolean AND on an already-small table).

## 5. Business logic (fat models — thin here by design)

This round adds almost no business logic; it re-homes UI. The rules that do exist:

- **Suggestion gating** lives in the form, per house standards: `GuildAnnouncementProposalForm.__init__` narrows the guild queryset to `Guild.objects.filter(is_active=True, allow_member_announcement_suggestions=True)` and overrides the picker's `invalid_choice` error message to *"This guild isn't taking member suggestions right now."* A POST naming a disabled guild is a normal validation error, not a crash. The view's `fixed_guild` lookup (`hub/views.py:3673`) adds the same filter so `?guild=<disabled pk>` degrades to the unfixed picker instead of pre-selecting a guild the form would reject.
- **Editing an existing proposal keeps working even if its guild has since disabled suggestions:** when the form binds to a saved instance (`instance.pk`), the queryset widens to `filter(is_active=True).filter(Q(allow_member_announcement_suggestions=True) | Q(pk=instance.guild_id))` — the proposal's own guild stays selectable, so a CHANGES_REQUESTED proposal can be revised and resubmitted without repointing at a different guild. The narrow filter applies only to *new* proposals (and to switching an existing one to a different guild).
- **Already-pending proposals for a guild that later disables suggestions** stay in the lead's review queue and can still be approved or declined (moderation is the lead's task, not the member's), and per the previous point the proposer can still resubmit after a changes request.
- **All guilds disabled:** the propose page is reachable even with every suggest button hidden — `hub_compose` redirects non-composers there (`hub/views.py:2740`). When the picker queryset is empty and the member is not editing an existing proposal, the view skips the form and the template renders a friendly card instead: *"No guilds are taking member suggestions right now."* with the existing back link — no empty `<select>`, no dead end. The "My proposals" list below still renders (editable ones remain editable via the instance-widened queryset).
- **Hours save** keeps its exact semantics (`retire_rule`, slot regeneration, flash message math). The only view-layer additions: a `formset_prefix` whitelist and HTMX-aware responses (§6.4). Binding an unlisted prefix raises `Http404` — fail loudly, mirroring the existing `orienter_scope` digit check.
- **Welcome-email removal (service side):** `member_joined_guild` (`membership/orientations.py:944-980`) fires a single `emit_with_email_shell` that does three things — the `GUILD_JOINED` activity row, the lead-only "New follower" in-app notice, and (only when `join_email_ready`) the welcome email to the member. Strip just the email: drop the `subject`, `text_template`, `html_template`, `template_context`, and `email_to` arguments (and the `welcome_settings` / `welcome_ready` / `settings_obj` lookups that fed them), keeping the activity + in-app notice. Since that leaves no email at all, the builder switches the call to the plain `emit()` path (the notice + activity route the spine already supports without an email shell). The function keeps its two callers (`membership/models.py:736`, `hub/discord_commands.py:197`) and its "New follower" behavior unchanged — only the email stops.
- **Welcome-email removal (spine channel — the substantive consequence, #4):** the email gallery classifies every spine event as *carded* (has an email whose copy is reviewable) or *listed under "No email is sent"*. `guild_joined` is a legacy-trigger-derived event (`core/triggers.py:112`, built in the trigger loop at `core/events/registry.py:380-390`), and `_channels_from_trigger` (`registry.py:244-264`) **unconditionally appends an EMAIL spec** — `_EMAIL_OFF` for `guild_joined` (no `force_email`, `email_default=False`). Today that vestigial EMAIL channel is *hidden* only because the `guild_welcome` gallery entry claims `guild_joined`'s email as a structural template (`_STRUCTURAL_EVENT_KEYS`). Once that entry is deleted (#3), the deriver in `gallery_emails()` would silently re-card `guild_joined` as an **opt-in email** card ("the generic notification copy they get") — a lie, because `member_joined_guild` now sends no email. The honest fix is to make `guild_joined` declare **no email channel at all**, so it is *listed* as no-email, not carded:
  - Add a `no_email: bool = False` field to the `Trigger` dataclass (`core/triggers.py:23-31`), alongside the existing `force_email` / `email_default` / `push_default` flags (mutually exclusive with `force_email` — a trigger cannot both force and suppress email; note it in the field's comment).
  - Set `no_email=True` on the `guild_joined` trigger (`core/triggers.py:112-116`).
  - In `_channels_from_trigger`, guard the EMAIL append: `if trigger.no_email: pass` (append no EMAIL spec) else the existing `force_email`/`email_default` branch. `guild_joined`'s channels become in-app (ON) + push (OFF) + Discord DM (OFF) — **no EMAIL**.
  - **Downstream, all now correct and consistent:** `gallery_emails()` skips it (`event.channel(Channel.EMAIL)` is None, `registry.py:747-748`); `no_email_events()` lists it (`:768-769`) with reason "in app + Discord DM only — no email channel"; `it_covers_every_email_section` sees `guild_joined` in `listed_keys`, not `carded_keys`, so it passes **honestly** (this is the test the reviewer flagged). `email_catalogue._sends_email` (`core/events/email_catalogue.py:122-123`) returns False, correctly dropping it from the email catalogue. The live notifications matrix is channel-driven — an absent channel renders as an empty cell (`core/events/settings_matrix.py:113,255`), so `guild_joined`'s Email toggle (which currently does nothing) cleanly disappears from member settings too. The `emit()`/dispatch path honors the event's channels, so no email is attempted regardless (belt and braces with the service-side strip above). The "New follower" in-app notice, push default, and Discord DM opt-in are untouched.
  - **Why this over the "in-app-only card" alternative:** the gallery's card family is for events that *send* email; `guild_joined` sends none, so carding it (even without a template) would reintroduce the same dishonesty in a different shape. Listing it under "No email is sent" is what the machinery already does for in-app/Discord-only events, and `guild_joined` becomes the first such event — exactly the case `no_email_events()` was built to hold. This is the house-consistent path.
- **Thank-you email save** keeps its exact validation and `*_updated_at` stamping, now in a single-purpose `GuildThankyouEmailForm` (§6.3). The thank-you keeps its no-requirement rule (enabling needs no subject/body — standard-copy fallback exists), so its `clean()` is trivial. No welcome validation survives (the field, form, and enable-requires-subject+body rule are all gone).

## 6. UI / UX

### 6.1 Guild detail — new Orientations tab *(item i)*

**Screen:** `templates/hub/guild_detail.html` (member hub AND the public guilds surface — the tab bar is shared).

- **Tab button** (`:120-121`), between Guild Calendar and Buyables, same gate as the partial:
  ```html
  {% if show_orientation %}<button type="button" class="vote-tab"
      :class="section === 'orientations' ? 'vote-tab--active' : ''"
      @click="section = 'orientations'">Orientations</button>{% endif %}
  ```
- **Panel**, placed after the schedule panel (`:448`), the partial moved verbatim out of it:
  ```html
  {% if show_orientation %}
  <div x-show="section === 'orientations'" x-cloak>
      {% include "hub/partials/guild_orientation.html" %}
  </div>
  {% endif %}
  ```
  The include at `:447` (inside the schedule panel) is deleted. The Guild Calendar keeps showing orientation slots as calendar entries and its Orientation filter chip (`guild_calendar_app.html:85-87` is untouched); only the booking UI moves. So the calendar tab does not become a dead end for someone who spots an orientation entry there, add a one-line pointer under the calendar in the schedule panel:
  ```html
  {% if show_orientation and not is_oriented %}
  <p class="hub-text-muted" style="margin-top:0.75rem; font-size:0.9rem;">
    Want to book one of these orientation times?
    <a href="#" @click.prevent="section = 'orientations'">Open the Orientations tab.</a>
  </p>
  {% endif %}
  ```
  The `not is_oriented` gate mirrors the Overview's Join button (`guild_detail.html:305`) — an already-oriented member is not invited to book again.
  ```html
  ```
- **Overview "Join an Orientation"** (`:306-307`) becomes:
  ```html
  @click="section = 'orientations'; $nextTick(() => document.getElementById('guild-orientation')?.scrollIntoView({ behavior: 'smooth' }))"
  ```
- **Legacy mapping** — extend the `x-init` (`:117`), guarded so a garbage or gated-off `?tab` can never select a missing panel (the existing pattern):
  ```html
  x-init="const t = new URLSearchParams(window.location.search).get('tab');
          if (t === 'notes' || t === 'meetings') section = 'meetings';
          {% if show_orientation %}if (t === 'orientations' || window.location.hash === '#guild-orientation') { section = 'orientations'; if (window.location.hash === '#guild-orientation') $nextTick(() => document.getElementById('guild-orientation')?.scrollIntoView()); }{% endif %}"
  ```
  The hash case covers any old link built against the anchor-inside-schedule era — and it *scrolls*, not just selects, since selecting the tab alone would leave the anchor unhonored (browsers can't scroll to an element that was `display:none` at load). `?tab=schedule` intentionally keeps meaning the calendar. A side benefit worth noting (no changes to them in this spec): the home dashboard's orientation nudge (`hub/home.py:101`) and the bell/notification rows built in `membership/orientations.py:202,977` can later append `?tab=orientations` to their guild-page URLs and land members directly on the booking tab.
- **Help keys keep resolving:** `orientation.book-slot` (Overview button `:306` + slot list `partial:65`), `orientation.request-custom-time` (`partial:122`), and `orientation.cancel-booking` (`partial:42`) all travel with the partial; no `data-help-key` value changes and no `core/help_registry.py` edits. The registry keys' `short_text` mention no tab name (verified `core/help_registry.py:39-124`), so no copy drift there. The paid-orientations round's booking-related keys live inside the partial and move with it — each still appears exactly where it did, once per element.
- **States:** unchanged — the partial already renders oriented / pending-payment hold / booked / paused / empty-slots / logged-out states; they simply render inside the new panel. Alpine tabs are CSS-show/hide, so every panel (including this one) stays in the server-rendered DOM — which is also why `tests/hub/orientation_paid_surfaces_spec.py:59` (splits page HTML on `id="guild-orientation"`) keeps passing untouched.
- **Empty/gated state:** `show_orientation` false → no tab, no panel, and the x-init guard means `?tab=orientations` falls back to Overview instead of a blank page.
- **Dark + light:** no new styles; the tab button reuses `vote-tab`. Verify both themes.
- **Mobile:** `.pl-tabs` horizontally scrolls (`hub.css:21-31`); one more tab extends the scroll strip, nothing wraps or overflows the viewport.

### 6.2 Guild edit — Announcements tab rename + suggestion toggle *(item ii)*

**Screen:** `templates/hub/guild_edit.html` Announcements panel (`:761-918`) + tab bar (`:23`).

- **Rename:** button label at `:23` becomes `Announcements`. The Alpine section key `announcements` and `data-help-key="guild.announcements"` stay (deep links and the help bubble keep working; the registry entry already says "Announcements tab", `core/help_registry.py:609`).
- **New card "Member Suggestions"** — placed between the pending-proposals banner (`:872-879`) and the "Post an Announcement" card (`:880`), so the setting sits right next to the feature it governs. Its own form and endpoint (the tab's established one-form-per-card rhythm; there is no general announcements settings form to extend):
  ```html
  <form method="post" action="{% url 'hub_guild_announcement_settings_save' guild.pk %}" class="hub-form">
    {% csrf_token %}
    <div class="hub-card" style="padding:1.5rem; margin-bottom:1.5rem;">
      <h2 class="hub-detail-label" style="margin-top:0; margin-bottom:0.25rem;">Member Suggestions</h2>
      <p class="hub-text-muted" style="font-size:0.8125rem; margin-bottom:1rem;">Members can propose announcements from your guild page. Nothing posts until you approve it. Turn this off to hide the suggestion button.</p>
      {% include "components/form_field.html" with field=announcement_settings_form.allow_member_announcement_suggestions %}
      <button type="submit" class="pl-btn pl-btn--primary" style="margin-top:1rem;">Save</button>
    </div>
  </form>
  ```
  - **Form:** new `GuildAnnouncementSettingsForm(forms.ModelForm)` in `hub/forms.py` — `model = Guild`, `fields = ["allow_member_announcement_suggestions"]`, label `"Let members suggest announcements"`. Rendered through `form_field.html`, which auto-renders the boolean as a toggle.
  - **Endpoint:** new `guild_announcement_settings_save` in `hub/views.py`, URL `guilds/<int:pk>/announcement-settings/save/` name `hub_guild_announcement_settings_save`. `@login_required @require_POST`, gate `_require_can_edit_guild`, save, `messages.success(request, "Announcement settings saved.")`, redirect `?tab=announcements`. A single-boolean ModelForm cannot fail validation, so no error branch beyond the gate.
  - **Context:** `_guild_edit_context` gains `"announcement_settings_form": GuildAnnouncementSettingsForm(instance=guild)`.
- **Member side, belt and braces:**
  - Guild page button (`guild_detail.html:151-153`): condition becomes `{% elif user.is_authenticated and guild.allow_member_announcement_suggestions %}`.
  - Empty-state copy (`:168`): "No announcements yet — be the first to suggest one." only renders when suggestions are allowed; a disabled guild shows plain "No announcements yet." (no dead-end invitation to a hidden button).
  - Server side: the propose form's queryset filter + `fixed_guild` filter per §5. Someone landing on `/guilds/announcements/propose/?guild=<disabled>` still gets a working page — the picker just does not offer that guild.
- **States:** toggle on (default, today's behavior) / toggle off (button and empty-state invite hidden; picker excludes the guild for new proposals; direct POST → field error *"This guild isn't taking member suggestions right now."*; existing proposals stay editable per §5) / every guild off (the propose page shows the §5 empty-state card instead of a bare select). Success → Django message on the tab. Editors never see the suggest button in either state (they have Send Announcement).
- **Dark + light / mobile:** all reused components; card follows the tab's existing stack; nothing new to style. Verify both themes.

### 6.3 Delete the Welcome email; move the Thank-you email to Orientations *(item iii, revised)*

**Two changes, one simplification.** The Welcome email is dead (its "Join This Guild" trigger was removed last round), so it is deleted outright — no card, no form, no fields. The Thank-you email is the orientation-lifecycle email, so its editor moves from the Announcements tab to the Orientations tab. That leaves exactly one email form in the whole feature, so the earlier "split one form into two" plan is gone.

**The two emails, verified:**

- **Welcome email — DELETED.** Was sent by `member_joined_guild` (`membership/orientations.py:944`) on guild follow/join. Removed at every layer: model fields + property (§4), the send call (§5), the `guild_welcome.{html,txt}` templates, the form fields, the UI card, and all help/example/test copy (§6.6, §9).
- **Thank-you email — MOVED, otherwise unchanged.** Sent by `complete_orientation` (`membership/orientations.py:770`) when an orientation is marked complete. Same trigger, template, `period` dedupe, and standard-copy fallback; only the editor UI relocates. Card copy: "Sent to the member once their orientation is marked complete."

**Form** (`hub/forms.py`): `GuildEmailsForm` (`:1584`) is renamed/reduced to **`GuildThankyouEmailForm`** — a `ModelForm` on `GuildOrientationSettings` with fields `thankyou_email_enabled/subject/body` only. All `join_email_*` handling is deleted: the `_JOIN_EMAIL_FIELDS` tuple, `clean_join_email_body`, the `_require_subject_and_body(cleaned, "join_email", "welcome")` call in `clean()` (leaving a trivial `clean()`, or none), the welcome label/widget entries, and the welcome branch of `save()`'s stamping. What stays: `RichTextEditorWidget` on `thankyou_email_body`, `sanitize_rich_html` cleaning, no enable-requires-subject+body rule (standard-copy fallback), and `save()` stamping `thankyou_email_updated_at` when a thank-you field changed. No welcome form is created; the previously-planned `GuildWelcomeEmailForm` and `_GuildEmailFormBase` mixin are dropped.

**Endpoint** — `guild_emails_save` (`hub/views.py:3034`) keeps its URL and name. A single `form_id` discriminator is enough (there is only one email form now, but the hidden field keeps the house pattern and guards against a stray POST):

- `form_id == "thankyou_email"` → bind `GuildThankyouEmailForm`; success message "Thank-you email saved."; redirect `?tab=orientations` (the tab the card now lives on). **Invalid → re-render `guild_edit.html` with the bound form and `ctx["active_tab"] = "orientations"`** — this fixes a latent bug where today's invalid emails POST re-renders without `active_tab`, dumping the lead on Basic Information with the errors hidden on another tab. (The thank-you form has no required-subject/body rule, so a validation error is rare — a body that fails sanitization is the realistic path — but the tab-preserving re-render is still specced.)
- Missing/unknown `form_id` **on a POST** → `Http404` (fail loudly). A GET keeps today's behavior — redirect to the tab (now `?tab=orientations`, `hub/views.py:3051-3052`) — so bookmarked/link-followed GETs never 404.

**Templates:**

- **Thank-you card** → Orientations tab (`guild_edit.html`), inserted after the orientation-settings `</form>` (`:383`) and before the My Orientation Hours form (`:388`) — the locked "after Booking settings, before My Orientation Hours" slot (the same one the Welcome card would have taken). It cannot live inside the settings form (no nested forms), so it is its own form: hidden `<input type="hidden" name="form_id" value="thankyou_email">`, the three fields via `form_field.html`, and a `Save` button (Rule 21: last thing in the form, label just "Save"). Card heading **Thank-you Email** (Title Case, Rule 22); description "Sent to the member once their orientation is marked complete." The existing `margin-bottom:2rem` on the settings Save (`:382`) gives Rule 18 breathing room above it.
- **Announcements tab:** the entire "Guild follow-up emails" `<form>` (`guild_edit.html:897-917`) is **deleted** — it held only the Thank-you and Welcome cards (verified: those two cards and one `Save emails` button, nothing else), so with Welcome gone and Thank-you moved, the whole block leaves the Announcements tab. Nothing else in that tab referenced it.
- `_guild_edit_context:777` swaps `emails_form` for `thankyou_email_form` (bound-form override preserved for the invalid-POST path).

**States:** enabled-with-content / disabled (fields keep their values; nothing sends) / validation error (bound form re-renders on the Orientations tab with field errors) / success (Django message + redirect to the Orientations tab). The thank-you email's content, trigger, and dedupe are untouched — §7.

**Help copy that must move or be removed:** §6.6.

### 6.4 Orientation Schedule + the Edit Hours modal *(item iv)*

**Screen:** `templates/hub/guild_edit.html` Orientations tab, the lead/admin overview card (`:466-507`), plus a new partial and endpoint.

**Rename:** heading `:468` "All Orientation Hours" → **"Orientation Schedule"**. The subtext ("Everyone on staff who can give orientations, and when.") does not say "hours" — verified, it stays. The Former Staff subheading and its explainer stay.

**Trigger buttons:** both Edit Hours anchors (`:475` active staff, `:496` former staff) become buttons that open one shared modal and fetch that person's form. **The viewer's own row does not get one** — their editor is the inline My Orientation Hours card, and an Edit Hours modal on their own row would open a second (`modal_rules`) editor over the very rows the inline (`rules`) card is editing, with the two saves silently clobbering each other. `_guild_edit_context` exposes `viewer_member_pk` (the viewer already exists there as `viewer`) and the active-staff row gates on `{% if staffer.pk != viewer_member_pk %}`. The former-staff list stays **deliberately ungated** — and a builder must not "tidy" that up: the viewer *can* appear there (an admin or officer with leftover personal rules who is not on this guild's leadership lands in `former_staff_overview`), and such a viewer gets no inline My Orientation Hours card (`show_my_hours_card` requires leadership membership), so the modal is their only route to cleaning up their own rows. Gating the former-staff row on `viewer_member_pk` would lock that admin out of their own cleanup. The two-editors collision the active-staff gate prevents cannot occur here, precisely because no inline card renders for a former-staff viewer. The click also resets the modal body to a loading line *before* the fetch, so back-to-back edits of different staffers never flash the previous person's form:

```html
{% if staffer.pk != viewer_member_pk %}
<button type="button" class="hub-btn hub-btn--sm"
        @click="document.getElementById('edit-hours-modal-body').innerHTML = '<p class=\'hub-text-muted\'>Loading…</p>'; $dispatch('open-modal', 'edit-hours-modal')"
        hx-get="{% url 'hub_guild_orientation_hours_form' guild.pk %}?orienter={{ staffer.pk }}"
        hx-target="#edit-hours-modal-body" hx-swap="innerHTML"
        hx-on::response-error="document.getElementById('edit-hours-modal-body').innerHTML = '<p class=\'hub-text-muted\'>Could not load this editor. Close this window and try again.</p>'"
        hx-on::send-error="document.getElementById('edit-hours-modal-body').innerHTML = '<p class=\'hub-text-muted\'>Could not load this editor. Close this window and try again.</p>'">
  Edit Hours
</button>
{% endif %}
```

(`hx-on::response-error` only fires on HTTP error statuses; `hx-on::send-error` covers a pure network failure — both land on the same message, so the loading line can never strand.)

One modal include after the card (`{% if can_edit_others_hours %}` scope):

```html
{% include "components/modal.html" with modal_id="edit-hours-modal" modal_title="Edit Hours" modal_size="lg" modal_static=True %}
```

**`modal_static` is new and is the blocker fix.** `components/modal.html` gains an optional parameter: when truthy, the component omits its `@click.outside="open = false"` and `@keydown.escape.window="open = false"` bindings; the modal then closes only via the X button, an in-body Cancel, or a `close-modal` dispatch. Why it's needed here: the delete-confirm (`confirm_modal.html:44`) renders through `<template x-teleport="body">`, so its entire DOM — Confirm button, Cancel button, backdrop — lives *outside* the hours modal's subtree, and the vendored Alpine 3.14.9 `click.outside` handler checks only `el.contains(target)` with no teleport awareness. Without the opt-out, every click inside the confirm closes the hours modal underneath it: Cancel discards the lead's edits, and Confirm `requestSubmit()`s into a now-hidden modal where a validation error re-renders invisibly. Dropping the window-level Escape handler also fixes Escape tearing down both layers at once (the confirm keeps its own Escape and click-outside — dismissing *it* is safe and leaves the editor open). Omit the parameter and every existing modal renders byte-for-byte as before. The generic title is fine because the partial's first line names the person (below). The `.pl-modal` shell scrolls internally past 85vh, so long formsets stay usable.

**New GET endpoint:** `guild_orientation_hours_form` — URL `guilds/<int:pk>/orientation/hours/form/`, name `hub_guild_orientation_hours_form`, `@login_required`. Reads `?orienter=<pk>` (non-digit or missing → `Http404`), resolves the Member (`get_object_or_404`), gates with `can_edit_orienter_hours(request, guild, target)` (403 otherwise — same gate the save uses, so former-staff targets work for leads/admins exactly as the old querystring flow did). Returns `render(request, "hub/partials/_orienter_hours_modal_form.html", {...})` with an unbound `OrientationAvailabilityFormSet(instance=guild, prefix="modal_rules", queryset=guild.orientation_rules.for_orienter(target))`.

**The partial** `templates/hub/partials/_orienter_hours_modal_form.html` — the riskiest piece, spelled out:

- **Structure:** a heading line ("Editing **{{ target.display_name }}**'s Hours" + the same muted "Weekly windows…" explainer), then the formset form:
  ```html
  <form id="edit-hours-modal-form" class="hub-form"
        hx-post="{% url 'hub_guild_orientation_hours_save' guild.pk %}"
        hx-target="#edit-hours-modal-body" hx-swap="innerHTML">
    {% csrf_token %}
    <input type="hidden" name="orienter_scope" value="{{ target.pk }}">
    <input type="hidden" name="formset_prefix" value="modal_rules">
    {{ formset.management_form }}
    <div id="modal-rule-rows" class="pl-hours-rows">…rows…</div>
    <template id="modal-rule-empty-template">…empty form…</template>
    …+ Add hours / Save…
  </form>
  ```
- **Prefix and id uniqueness (the whole point of `modal_rules`):** the page permanently renders the viewer's own `rules`-prefixed formset in the My Orientation Hours card, including `id_rules-TOTAL_FORMS` and `#rule-empty-template`. The modal formset's prefix `modal_rules` gives every management input, field, and label a distinct id (`id_modal_rules-TOTAL_FORMS`, `id_modal_rules-0-weekday`, …), and the template/container ids are `modal-rule-empty-template` / `modal-rule-rows`. Nothing in the modal ever collides with, or mutates, the page's own editor.
- **Rows:** copied from the canonical editor (`guild_edit.html:401-425`) — weekday/start/end/seats via `form_field.html` in the flex row, `is_active` toggle, then for saved rows the hidden `{{ rule.DELETE }}` plus a real `pl-btn pl-btn--danger pl-btn--sm` Delete button with `margin-top:0.75rem`.
- **Delete confirm inside the modal:** same `confirm_modal.html` + `confirm_js` flow, with modal-unique ids `confirm_id="modal-rule-del-<pk>"` and `confirm_js` flipping the `modal_rules` DELETE checkbox then `document.getElementById('edit-hours-modal-form').requestSubmit()` — `requestSubmit()` fires the form's submit event, which htmx intercepts, so the delete round-trips through `hx-post` like any save. Alpine 3's MutationObserver initializes the swapped-in `confirm_modal` components automatically, and each confirm teleports itself to `<body>` (`confirm_modal.html:44`), so it stacks above the hours modal regardless of DOM order. That same teleport is exactly why the hours modal must be `modal_static` (see the trigger-button block above): with the default click-outside binding, dismissing or confirming the teleported dialog would close the editor underneath it. With `modal_static`, cancelling the confirm returns the lead to the still-open editor, and confirming submits into a visible modal where any error re-renders in view.
- **"+ Add hours":** identical inline-`onclick` clone pattern as the page editor (`:447-456`) — no `<script>` tags, so it needs no script re-execution after the HTMX swap — targeting `modal-rule-empty-template`, `id_modal_rules-TOTAL_FORMS`, `modal-rule-rows`. Cloned unsaved rows get the plain Remove button (`this.closest('.hub-card').remove();`).
- **Save button:** last element in the form, labeled `Save` (Rule 21), with `hx-disabled-elt="this"` so it disables while the request is in flight — the valid-save path round-trips through `HX-Redirect`, and an impatient second click during that window must not double-submit the formset (house precedent: `confirm_modal.html:93`). A Cancel button beside it dispatches `close-modal` (`$dispatch('close-modal', 'edit-hours-modal')`) so there is no dead end.
- **Empty state:** zero rows renders "No hours yet. Add a window and members can start booking {{ target.display_name }}."

**Save view changes** (`guild_orientation_hours_save`, `hub/views.py:938`):

1. Prefix selection: for a personal scope, `prefix = request.POST.get("formset_prefix", "rules")`, whitelisted to `{"rules", "modal_rules"}` (anything else → `Http404`); the guild-level scope keeps hard-wired `guild_rules`. Additionally, `modal_rules` is only valid **with** the `HX-Request` header — a crafted plain POST with `formset_prefix=modal_rules` would otherwise fall through to the full-page invalid re-render, where the page's `rules`-prefixed context formset meets a `modal_rules` management form (mismatched, broken). Non-HTMX + `modal_rules` → `Http404`. `orienter_scope` remains the authority for target + queryset, exactly as today.
2. **Valid POST:** unchanged pipeline (`_apply_hours_formset`, `generate_slots`, `messages.success`). If the request is HTMX (`request.headers.get("HX-Request")`), respond `HttpResponse(status=204)` with `response["HX-Redirect"] = f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"` instead of the plain redirect — htmx performs a full navigation, the modal disappears with the old page, and the queued flash message renders on the reloaded Orientations tab (deletes, adds, and slot-regeneration counts all surface exactly as they do today). Non-HTMX requests (the page's own My Hours / Guild Hours forms) keep the plain `redirect`.
3. **Invalid POST:** if HTMX + `modal_rules`, re-render `_orienter_hours_modal_form.html` with the **bound** formset (status 200) — htmx swaps it into `#edit-hours-modal-body`, so field and non-form errors appear **inside the open modal** with everything the lead typed preserved. Non-modal invalid POSTs keep the current full-page re-render with `ctx["active_tab"] = "orientations"` (minus the removed scope echo below).

**Dead code removal:** in `_guild_edit_context` — the `orienter_param` branch (`:707-713`), the `hours_scope_member` parameter, `hours_editing_other` (`:714`, and its term in `show_my_hours_card:723`, which becomes `viewer is not None and viewer.pk in leadership_ids`), and the context keys `hours_scope_member`/`hours_editing_other` (one key is *added*: `viewer_member_pk`, for the own-row Edit Hours gate above). In the template — the `:392-394` "Editing …'s Hours" heading + back link (My Orientation Hours heading becomes unconditional) and the `orienter_scope` hidden value simplifies to the viewer's pk. In `guild_orientation_hours_save` — the `hours_scope_member=target` echo on the invalid path. `hub/urls.py` is untouched apart from the two additions (the old flow was querystring-only).

**States walk (this screen):**

| State | What the lead sees |
|---|---|
| Click Edit Hours | Modal opens immediately over a "Loading…" line (the click resets the body before fetching — production runs on Render, not localhost, so the in-flight state is real); the swapped form replaces it. Back-to-back edits of two staffers never show the first person's stale form. |
| Fetch fails (network/5xx) | `hx-on::response-error` replaces the body: "Could not load this editor. Close this window and try again." The X button still closes (it is not gated by `modal_static`). |
| Viewer's own row | No Edit Hours button — the inline My Orientation Hours card is their editor (prevents two live editors over the same rows). |
| Person with rows | Heading names the person; their rows render editable; Delete buttons confirm first (retiring future open slots is destructive — same message as the page editor). |
| Person with no rows | Empty-state line + "+ Add hours". |
| Former staff | Same modal; leads/admins pass the gate; deleting their leftover rows is the advertised cleanup path and keeps working. |
| Validation error | Bound formset re-renders inside the modal; typed values and errors intact; modal stays open. |
| Success | Full page reload onto the Orientations tab; flash message reports rules deleted / slots removed / booked slots kept. |
| No permission / bogus pk | Endpoint returns 403 / 404; the button never renders for non-`can_edit_others_hours` viewers anyway. |

**A11y:** the modal component provides `role="dialog"`, `aria-modal`, `aria-labelledby`, and a labeled close button — the Edit Hours modal inherits all of it by using `components/modal.html`. Escape and click-outside are deliberately disabled on this one modal (`modal_static`, the blocker fix): a formset editor with unsaved work should not vanish on a stray click, and keyboard users always have the X button, the in-body Cancel, and Tab reachability to both. The nested delete-confirm keeps its own Escape/click-outside, so the innermost layer stays easy to dismiss. The component has no focus trap today; this spec matches the existing component rather than inventing one (a component-level upgrade is §10 material). Delete/Remove are real buttons, keyboard-reachable.

**Dark + light:** all reused components and tokens (`hub-card`, `pl-btn`, `form_field`); the row cards' `rgba(255,255,255,0.02)` inset matches the page editor in both themes. Verify both.

**Mobile:** `.pl-modal` is `width:100%` with a max-width, scrolling internally; the row's flex fields already wrap (`flex-wrap:wrap`, min-widths). Buttons are full-size tap targets.

### 6.5 UX-completeness pass (per changed screen)

- **Guild detail:** primary action per tab is obvious (Request buttons in the new tab); no dead ends (gated deep links fall back to Overview; disabled suggestions removes the invitation copy too); all partial states preserved.
- **Guild edit / Announcements:** every card keeps its own visible Save; the new toggle card names its consequence; the tab's forms remain non-nested and independently saveable.
- **Guild edit / Orientations:** the tab now reads top-to-bottom as: booking settings → thank-you email → my hours → schedule (with modal editing) → legacy guild hours → upcoming slots. Each form ends in its own `Save`; no control sits below a save button; nothing is create-only or edit-only.
- **Guild edit / Announcements:** with the follow-up-emails block gone, the tab ends cleanly at Recent Announcements — one fewer form to save, no dangling email cards.
- **List editors:** both formsets involved (modal hours, existing page hours) have the full trio — "+ Add" (clone + TOTAL_FORMS bump, `extra=0`), real per-row Delete (confirmed, saves the page/modal), and a wired Save.

### 6.6 Help content + registry impact (checked, not hand-waved)

`core/help_registry.py`: **no changes.** All touched keys (`orientation.book-slot`, `orientation.request-custom-time`, `orientation.cancel-booking`, `guild.announcements`, `guild.run-orientations`, `guild.join-leave`) have tab-name-free `short_text` and travel with their elements.

`membership/help_content.py` (copy + ShotSpecs — member-facing, plain language, no dashes as punctuation in new copy):

| Article | Change |
|---|---|
| `getting-oriented` (`:319-365`) | Step 1: "…jumps you to the booking section on the **Orientations** tab." Step 2 caption/body likewise. ShotSpec 2: rename file to `02-orientations-tab.png`, caption "The Orientations tab holds the guild's booking section." (selector `nav[role="tablist"]` still valid). Screenshot 1's selector (`.hub-card:has(button[data-help-key="orientation.book-slot"])`) still resolves — the Get Involved button keeps its key. Regenerate both shots. |
| `guilds-and-guild-pages` (`:276`, `:291`, `:298`) | Tab list line: Guild Calendar keeps "meetings and classes"; add an "**Orientations** — book a time to get oriented" line. The Good-to-Know claim "Anyone can propose an announcement for a guild" (`:291`) gains the qualifier "when the guild has member suggestions on". Regenerate `01-a-guild-page.png` (`:298`) — the tab nav it captures now includes the Orientations tab. |
| `announcements` (`:645-676`) | Add one sentence after the suggest steps: "Some guilds turn member suggestions off. If you don't see the button, that guild isn't taking suggestions right now." The "…for any guild" claim (`:650`) softens to "for any guild that has member suggestions on". `02-propose-announcement.png` unchanged (shot against a default-on guild); `01-guild-announcements.png` unchanged for the same reason. |
| `your-guild-page` (`:1299`, `:1333-1335`) | Tab roster line: "Announcements/Emails" → "Announcements". Rewrite the "Two Automatic Emails" section (`:1333-1335`) into **one** email: drop the Welcome email entirely and say the guild's single automatic email — the **Thank-you email**, sent once an orientation is marked complete — is written on the **Orientations** tab with its own Save. Retitle the heading (no longer "Two"). |
| `running-orientations` (`:1469`, `:1472-1476`) | "…your thank-you email (if you've set one up on the **Orientations** tab)"; drop "and posts a welcome notice to the guild" only if that clause referred to the deleted email (it refers to the completion in-app notice from `complete_orientation:800`, which STAYS — leave it). Regenerate ShotSpec `01-orientations-tab.png` (`:1472-1476`): the panel now shows the Thank-you Email card and the renamed **Orientation Schedule** heading (no Welcome card); update the caption accordingly. |
| `guilds-and-guild-pages` following section (`:288`) | Remove the "Some guilds send a welcome email with next steps" clause; keep "and the guild's leads are notified so they can say hi" (that in-app notice stays). |
| `running-a-guild` (`:1254`) | The "Two automatic emails — a welcome email … and a thank-you …" bullet becomes a single "**One automatic email** — a thank-you after their orientation" bullet; the link target stays `your-guild-page` (which now documents it on the Orientations tab). |
| `guild-announcements` (`:1514-1584`) | Sweep every "Announcements/Emails" → "Announcements" (five mentions incl. one ShotSpec caption at `:1555`); add a short "Turning member suggestions off" paragraph pointing at the Member Suggestions card. Regenerate `02-announcements-tab.png` (the tab now also shows the toggle card and loses the welcome card). |
| `membership/example_guild.py` (`:163-165`, `:209-219`) | Two changes: (1) the seeded example announcement says leads publish "from the Announcements/Emails tab" — rename to "the Announcements tab"; (2) in the `GUILD_ORIENTATION_SETTINGS` dict drop the three `join_email_*` keys (`:215-219`) — they name dropped model fields and would crash the seed — keeping the `thankyou_email_*` keys (`:209-211`). The copy lives in seeded DB rows, so after deploy re-run `seed_example_guild` (Render one-off job) so the live example updates; a code-only change leaves the old wording on production. |

The help drift guard (`tests/membership/help_content_spec.py` + `tests/e2e/help_screenshots_spec.py`) will fail on caption/body/file mismatches — update copy and ShotSpecs together in one commit.

## 7. Notifications / emails / activity

**One email removed; one unchanged; one channel retired.** The **Welcome email is deleted** — its `member_joined_guild` send is stripped (§5), but the lead-only "New follower" in-app notice and the `GUILD_JOINED` activity that shared that emit call **stay** (they are not the welcome email). As a direct consequence, the `guild_joined` spine event loses its (now-dead) EMAIL channel via the `no_email` trigger flag (§5, #4): it becomes an in-app + Discord-DM event, honestly *listed* as "No email is sent" in the copy gallery and shorn of its inert Email toggle in the notifications matrix. The **Thank-you email is unchanged** — same trigger (`complete_orientation:770`), template, audience, and `period` dedupe key; only its editor UI moves. The completion in-app "welcome notice to the guild" (`complete_orientation:800-815`, a copy-mode `emit`) is a different notification and is untouched. The suggestion toggle suppresses proposal *creation* only; the proposal-review notification chain is untouched (it simply receives none from disabled guilds).

## 8. Build order (phased; each phase ships green)

1. **Model + gating (item ii backend):** `Guild.allow_member_announcement_suggestions` + migration 0144; `GuildAnnouncementProposalForm` queryset filter + `invalid_choice` message; `fixed_guild` filter; specs. Run `manage.py check` after the migration (CI runs system checks local pytest skips).
2. **Announcements tab (item ii UI + rename):** tab label, Member Suggestions card, `GuildAnnouncementSettingsForm`, `guild_announcement_settings_save` + URL, guild-detail button/empty-state gating; specs.
3. **Email rework (item iii):** (a) **delete the Welcome email as ONE atomic change so CI never goes red mid-phase** — drop the `join_email_*` model fields + `join_email_ready` (migration 0145), strip the send from `member_joined_guild`, retire the `guild_joined` EMAIL channel (`no_email` trigger flag + `_channels_from_trigger` guard, #4), delete `guild_welcome.{html,txt}` + the `_button.html` comment, delete the `demo_data.py` join-email seed block, remove the `guild_welcome` gallery entry + `guild_welcome_context` + the `EXPECTED_PAIRS`/count/test-rename in the completeness spec, and run the full test/help/example sweep (§9) — all in the same commit, because the model-field drop, the gallery parity layer, and the `demo_data` CI seed are mutually load-bearing; (b) reduce `GuildEmailsForm` → `GuildThankyouEmailForm`, wire the `thankyou_email` `form_id` dispatch in `guild_emails_save` (with the `active_tab` fix), delete the Announcements "Guild follow-up emails" block, add the Thank-you card to the Orientations tab; specs. Run `manage.py check` after migration 0145.
4. **Orientations tab on guild detail (item i):** tab + panel + Overview button + x-init mapping; specs.
5. **Edit Hours modal (item iv):** `modal_static` parameter on `components/modal.html` first (independently green — omitted param changes nothing); then `guild_orientation_hours_form` view + URL + partial; `formset_prefix` whitelist (incl. the HX-Request requirement) + HTMX branches in the save view; template card rename + own-row-gated modal trigger; `?orienter=` dead-code removal in context/template/tests; specs.
6. **Help content sweep (§6.6):** article copy + ShotSpecs + screenshot regeneration + `example_guild.py` rename. Post-deploy: re-run `seed_example_guild` so the live Cartographers Guild announcement picks up the new tab name.

> Spec only — do not build until approved. No VERSION/changelog work in this spec; the shipping round handles it.

## 9. Testing

BDD `*_spec.py` (pytest-describe, `describe_*`/`it_*` only — never `context_*`), factory-boy, existing spec homes:

- **`tests/hub/guild_detail_*` / new `guild_orientation_tab_spec.py`:** tab button renders only with `show_orientation`; panel contains the partial; schedule panel no longer does; x-init string carries the orientations mapping only when gated on. (`orientation_paid_surfaces_spec.py` untouched — asserted above.)
- **Proposal gating (`tests/hub/guild_announcements_*`):** button hidden when disabled (and empty-state invite softened); picker queryset excludes disabled guilds for NEW proposals but includes the instance's own guild when editing (a CHANGES_REQUESTED proposal for a since-disabled guild resubmits without repointing); POST naming a disabled guild → field error with the exact message; `?guild=<disabled>` degrades to unfixed; all-guilds-disabled renders the empty-state card in place of the form (and the `hub_compose` non-composer redirect path lands there sanely); pending proposals for a since-disabled guild still decidable in the queue; default `True` keeps every existing test green.
- **Announcement settings save:** permission gate (non-editor 403 path via `_require_can_edit_guild`), toggle persists both ways, redirect + message.
- **Welcome-email removal sweep (must land first so the suite stays green):**
  - **Delete** `describe_join_email_ready` (`tests/membership/orientation_models_spec.py:54-63`) and `core/spec/rich_email_templates_spec.py:53-64` (`describe_guild_welcome_email`); remove `"guild_welcome"` from the email-gallery list (`tests/core/email_gallery_completeness_spec.py:41`).
  - **`describe_member_joined_guild`** (`tests/membership/orientations_service_spec.py:514-541`): drop the welcome-email assertions and the `join_email_*` factory kwargs (`:521-523`); keep and tighten the "New follower" notice assertions (`:533-534`) and the `GUILD_JOINED` activity — the point of the test becomes "sends the notice, sends NO welcome email".
  - **Strip `join_email_*` factory kwargs** wherever a test passes them to `GuildOrientationSettingsFactory` (they will raise `TypeError` on the dropped fields once the columns go): `tests/membership/guild_updates_model_spec.py:170-172`, `tests/hub/my_guilds_spec.py:206-208`, `tests/hub/guild_social_spec.py:40-56`, plus any in `guild_emails_spec.py` below. Also drop the `settings.join_email_enabled is False` assertion in `tests/membership/example_guild_spec.py:43`.
  - **`demo_data` seed (runs in NORMAL CI — blocker for green):** `core/management/commands/demo_data.py:711-715` reads `obj.join_email_subject` and writes the four `join_email_*` fields; `tests/membership/demo_data_spec.py:45` runs `call_command("demo_data")` in the ordinary suite, so after the column drop this is an `AttributeError` and `describe_demo_data_seed` fails. **Delete the entire `if not obj.join_email_subject:` block** (`:711-715`); the thank-you seed block just above it (`:703-710`) stays. No test change needed in `demo_data_spec.py` itself — it just needs the command to stop touching dropped fields.
  - **Email-gallery parity layer (three linked edits — the whole layer must move together or CI reddens):**
    - `tests/e2e/email_gallery/registry.py:471-488` — **delete the `guild_welcome` `GalleryEmail` entry** (its `html_template`/`text_template` point at the deleted files and its `context_builder="guild_welcome_context"` reads dropped fields). This is what un-cards `guild_joined`, which is why the #4 `no_email` spine change is mandatory in the same commit — without it `guild_joined` would be neither carded nor honestly listed.
    - `tests/core/email_gallery_completeness_spec.py` — remove `"guild_welcome"` from `EXPECTED_PAIRS` (`:41`), which drops the count from 26 to 25, so also flip `assert len(EXPECTED_PAIRS) == 26` and **rename the test** `it_discovers_the_expected_26_pairs` → `it_discovers_the_expected_25_pairs` (`:66-70`). `it_covers_every_email_section` needs no edit — the #4 change makes `guild_joined` land in `listed_keys` on its own.
    - `tests/e2e/email_gallery/context.py` — drop the three `join_email_*` kwargs in `build_sample_data` (`:82-84`) and **delete `guild_welcome_context`** (`:523-534`), which reads `join_email_subject`/`body`. Dormant under `BUILD_EMAIL_GALLERY=1` (won't fail pytest), but it breaks the gallery build and dangles on dropped fields — remove it with the registry entry that referenced it.
  - **Channel-change coverage (#4):** a spec asserting `guild_joined` has NO EMAIL channel and IS returned by `no_email_events()` (with the "no email channel" reason); confirm `_channels_from_trigger` still appends EMAIL for a normal trigger (regression guard on the new branch — 100% branch coverage requires both the `no_email` and non-`no_email` paths exercised). Verify the `settings_matrix` specs stay green with `guild_joined`'s Email cell now absent (an empty cell, not a toggle). The "New follower" notice test (`orientations_service_spec.py:532`, `Notification … trigger="guild_joined"`) still passes — the notice is preserved.
- **Thank-you email tests (`tests/hub/guild_emails_spec.py` rework):** this file currently tests the combined `GuildEmailsForm` and its docstring (`:3`) still describes the old move. Rewrite to the thank-you-only world: `form_id="thankyou_email"` saves the thank-you fields and stamps `thankyou_email_updated_at`; enabling with no subject/body is allowed (standard-copy fallback, keep the `:124` test); a sanitization-failing body re-renders with `active_tab == "orientations"`; unknown/missing `form_id` on POST → 404, GET → redirect to `?tab=orientations`; the template renders the Thank-you card in the Orientations panel and **no** email card in the Announcements panel. Delete every welcome/`join_email_*` case (`:77,84-94,103,107,120,140-156`). The existing thank-you model-property tests (`orientation_models_spec.py:25-51`, `orientations_service_spec.py:317`) stay green unchanged.
- **Hours modal (`tests/hub/orienter_hours_editor_spec.py` rework):** the `?orienter=` querystring tests (`:277-319`) are replaced by form-endpoint tests — lead gets another's form (prefix `modal_rules`, scope hidden field, heading names the target), non-privileged staffer 403s for others, bogus/missing orienter 404s, former-staff target renders. Save: `formset_prefix=modal_rules` + HTMX header → 204 + `HX-Redirect` on valid, partial re-render with errors on invalid; unlisted prefix → 404; `modal_rules` **without** the HX-Request header → 404; non-HTMX `rules` path byte-for-byte behavior preserved (retire/regenerate/flash). Template: "Orientation Schedule" heading; no `orienter=` hrefs remain; `hours_editing_other` gone from context; no Edit Hours button on the viewer's own row (and present on every other active-staff row).
- **Beyond the `:277-319` block, three assertions on the old heading need updating by name:** `tests/hub/guild_edit_spec.py:741` and `tests/hub/orienter_hours_editor_spec.py:218,410` assert `b"All Orientation Hours"` positively (→ `b"Orientation Schedule"`); `orienter_hours_editor_spec.py:422` asserts its *absence* and stays as-is (see §10 for the changelog implication).
- **Modal survives the confirm round-trip (blocker regression test):** a rendered `edit-hours-modal` carries no `@click.outside`/`@keydown.escape.window` bindings (assert `modal_static` output at the component level — with the param the handlers are absent from the HTML, without it they render exactly as today), plus an e2e/DOM-level spec in the existing screenshot/e2e harness asserting that opening a row's delete confirm, clicking its Cancel, leaves the hours modal open with the formset intact.
- **Docstring sweep:** stale "Announcements/Emails" wording in `tests/hub/orientation_settings_spec.py:167`, `tests/hub/guild_mailing_list_views_spec.py:3`, and `tests/e2e/guild_announcement_recipients_spec.py:49` — rename alongside the template change (cosmetic, but the sweep is one grep).
- **Help drift:** `help_content_spec` + screenshot specs pass with the copy/ShotSpec updates; `tests/template_comment_lint_spec.py` for the template edits.
- Coverage gate 100% (branch), mutation suite as usual. No tz gotchas — nothing date-windowed changes.

## 10. Open / deferred

- **Modal focus trap:** `components/modal.html` has no focus trap; adding one is a component-level change benefiting every modal, out of scope here.
- **Changelog wording constraint for the shipping round:** the eventual member-facing CHANGELOG entry must not quote the old heading "All Orientation Hours" verbatim — changelog text renders into every hub page's context, and `tests/hub/orienter_hours_editor_spec.py:422` asserts that string is absent from the page. Describe the change in other words ("the schedule card", "the hours overview").
- **Out of scope:** any change to orientation booking logic, slot generation, the **thank-you** email's content/trigger/dedupe (only its editor moves), the compose wizard, the review queue, the legacy Guild Hours (Any Orienter) card, VERSION/changelog, and the public guilds surface beyond what the shared templates carry automatically. (The welcome email's *removal* is in scope — §4/§5/§6.3 — including the one spine consequence it forces: retiring the `guild_joined` EMAIL channel via the new `no_email` trigger flag, #4. No *other* email send, trigger, or channel changes; every other spine event keeps its current channels.)
