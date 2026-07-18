# Guild Settings page + "Your Mailing List" section — Spec & Implementation Plan

**Status:** Spec only — approved to build (v23).
**Date:** 2026-07-17
**Surface:** FOG hub — the guild-edit page (`hub_guild_edit`, `templates/hub/guild_edit.html`), Announcements/Emails tab.
**Related:** `2026-07-17-join-guild-command.md` (same v23 PR; also touches `guild_edit.html` + `hub/forms.py` — land this AFTER it), v22 guild-announcement-recipient-list (`Guild.announcement_recipients()`).

---

## 1. Summary

Two changes to the guild-edit experience:

**(A)** Make it obvious the guild-edit page **is the "Guild Settings" page** — rename the page title, heading, and the entry button on the guild page from "Edit" to "Guild Settings". (Pure labeling; the URL and behavior don't change.)

**(B)** Add a **"Your Mailing List"** section at the top of the **Announcements/Emails** tab so a guild lead can see exactly who their announcements reach and extend it. It shows the guild's **members** (automatically on the list, with a live count) and lets the lead **add custom, non-member email addresses** (a booster, a partner, a lead's personal address) that also receive the guild's announcement emails. A tooltip explains that a guild announcement goes out to **email and the guild's Discord channel at the same time**.

### Locked decisions (from user + scout)

| Decision | Choice |
|---|---|
| Page rename | Label only — "Guild Settings" everywhere the page is titled/linked. URL name `hub_guild_edit` unchanged. |
| Members on the list | **Auto-included, read-only** — the existing `Guild.announcement_recipients()` roster (active members with a usable email). Shown with a count. |
| Custom emails | A **new `GuildMailingListEmail`** row model (guild FK + email + optional label), edited with the standard `extra=0` inline-formset editor (+ Add / per-row Delete / Save). |
| De-dup | Custom emails are lower-cased and any that equal a member email are dropped — in **both** the displayed list and delivery. |
| Send integration | Custom emails must receive the announcement **without suppressing member emails** — the `emit(email_to=…)` explicit path currently suppresses the member fan-out (`emit.py:161`), so add an **additive** `extra_emails` path. Both send entry points (`GuildAnnouncement.notify_members` and `AnnouncementDraft.send`) must include them. |
| Tooltip | Reuse the `.pl-help` component; copy explains "members + custom addresses" and "sends to email AND your Discord channel simultaneously." |

## 2. What already exists (reuse, don't reinvent)

Confirmed on `origin/main`.

| Need | Existing thing | Location |
|---|---|---|
| The page to rename (title/h1/back button) | `guild_edit.html` | `templates/hub/guild_edit.html:2,7,8` |
| The entry button to rename ("Edit") | guild detail page | `templates/hub/guild_detail.html:64,66` |
| The Announcements/Emails tab body (insert point at top) | `section === 'announcements'` | `guild_edit.html:559-631` (insert before `:569`) |
| Member recipient count + email list (already rendered) | `announcement_recipient_count` / `announcement_recipient_emails` + "Show recipients" collapsible | `guild_edit.html:572-590`; `hub/views.py:699-700` |
| Resolve member recipients (single source of truth) | `Guild.announcement_recipients()` → `guild_members` resolver | `membership/models.py:1660`; `core/events/resolvers.py:161` |
| Confirm the email **+ Discord** dual send (for the tooltip) | `GuildAnnouncement.notify_members` → `emit()` → per-recipient email + `_guild_broadcast` Discord post | `membership/models.py:2168`; `core/events/emit.py:162,301` |
| Row model to clone (FK + sort_order + inline editor) | `GuildLink` | `membership/models.py:1835-1841` |
| Formset factory to clone (`extra=0, can_delete=True`) | `GuildLinkFormSet` / `GuildLinkForm` | `hub/forms.py:929-938` |
| List-editor template (**+ Add**, per-row **Delete** button, `empty_form` clone) | the **Links** editor `<form>` | `guild_edit.html:498-556` |
| Save view to clone (own endpoint, `_require_can_edit_guild`, redirect `?tab=`) | `guild_links_save` | `hub/views.py:2244-2260` |
| Page context builder (add the formset + a context key here) | `_guild_edit_context` | `hub/views.py:664-727` (formsets built `:702-705`) |
| Tooltip component (global CSS, no new styles) | `.pl-help` / `.pl-help__icon` / `.pl-help__bubble` | `guild_detail.html:244-247`; `static/css/hub.css:1661-1710` |
| Explicit-address send path (to extend into an additive one) | `emit(email_to=…)` → `_explicit_email_fan_out`; suppression at `emit.py:161` | `core/events/emit.py:86-90,161,351` |
| Second send entry point (must also include custom emails) | `AnnouncementDraft.send` | `membership/models.py:2748` |

### Genuine gaps to close

1. **`GuildMailingListEmail` model** (+ migration) — the only new storage.
2. **Additive custom-email delivery** — the one real logic gap: send to custom emails **in addition to** members, deduped, from both entry points, without tripping the `email_to` suppression.
3. Editor formset + save view + URL + the template section + the rename.

## 3. Where the code lives

```
membership/models.py             # + GuildMailingListEmail model; a Guild.mailing_list_emails() -> deduped custom email list;
                                 #   wire custom emails into notify_members + AnnouncementDraft.send
membership/migrations/00XX_*.py  # create GuildMailingListEmail
core/events/emit.py              # + additive `extra_emails` param (does NOT suppress the member fan-out)
hub/forms.py                     # + GuildMailingListEmailForm + GuildMailingListFormSet (clone GuildLink*)
hub/views.py                     # + guild_mailing_list_save (inline-error re-render) + guild_mailing_list_import (CSV parse) views;
                                 #   + mailing_list_formset & custom_recipient_emails in _guild_edit_context
hub/urls.py                      # + hub_guild_mailing_list_save, hub_guild_mailing_list_import
templates/hub/guild_edit.html    # rename (title/h1) + new "Your Mailing List" <form> at top of announcements tab
templates/hub/guild_detail.html  # rename the "Edit" entry button -> "Guild Settings"
tests/membership/... , tests/hub/...  # model, resolver merge, form, view, template specs
```

## 4. Data model

**`GuildMailingListEmail`** (`membership/models.py`, mirroring `GuildLink`):

| Field | Type | Notes |
|---|---|---|
| `guild` | `ForeignKey(Guild, on_delete=CASCADE, related_name="mailing_list_emails")` | The guild whose announcements this address receives. |
| `email` | `EmailField(max_length=254)` | `help_text="A non-member email address that also receives this guild's announcement emails."` |
| `label` | `CharField(max_length=100, blank=True, default="")` | `help_text="Optional — who this is (e.g. 'Front desk', 'Partner org')."` |
| `sort_order` | `PositiveIntegerField(default=0)` | `help_text="Ascending; lower shows first."` |

`Meta.ordering = ["sort_order", "id"]`; `constraints = [UniqueConstraint(fields=["guild", "email"], name="uq_guildmailinglistemail_guild_email")]`. `__str__` → `f"{self.email} ({self.guild.name})"`. Migration: create table; reverse = drop (auto-reversible `CreateModel`).

## 5. Business logic

**The collision key is `user.email`, lower-cased — one definition used everywhere.** The EMAIL channel delivers to `user.email` (`core/events/channels.py:152`) and the UI already displays `sorted(user.email for user, _reason in recipients)` (`hub/views.py:701`). So member-vs-custom de-dup MUST key on `{u.email.strip().lower() for u, _ in recipients}` — **not** `member.primary_email` (which can be a verified alias differing from `user.email`) and not raw mixed-case. This same lower-cased set is used in the display context, in the model helper, and in the in-emit dedup. A custom `Foo@Bar.com` equal to a member's `foo@bar.com` collapses to one send.

**`Guild.mailing_list_emails_deduped(member_emails: set[str]) -> list[str]`** (fat model — single name, used by both display and delivery): returns the guild's custom addresses, `.strip().lower()`-normalized, sorted, with any member in `member_emails` (already lower-cased) removed.

**Delivery — the additive path (the one careful change), through a single choke point:**
- Add an optional **`extra_emails: list[str] | None = None`** parameter to `emit()` (`core/events/emit.py`). Default `None` → **no behavior change for any existing caller**. When given, it fans those addresses through the explicit-email path **additively** — it must NOT set `suppress_user_email` (the member per-recipient loop still runs; only the pre-existing `email_to`/`suppress_email` keep the old suppression semantics). Dedup `extra_emails` against the resolved member recipients inside emit as a belt-and-suspenders guard (the ledger won't catch it — the per-recipient loop claims `user:{pk}` while the explicit loop claims `email:{addr}`, different slots, `emit.py:391,428`).
- **Gate on the email toggle (BLOCKER fix):** pass `extra_emails` **only when the announcement actually sends email** (`self.send_email` is true). When a lead turns "Also send email" off (`suppress_email=True`), custom addresses get nothing either — otherwise the toggle would suppress members while still emailing boosters, the opposite of what it says.
- **Single choke point = `GuildAnnouncement.notify_members`** (`membership/models.py:~2168`): compute the lower-cased `member_emails` set from the resolved recipients, then (when `self.send_email`) pass `extra_emails=self.guild.mailing_list_emails_deduped(member_emails)` into its `emit(...)`. **Do NOT touch `AnnouncementDraft.send`** — its guild branch materializes a `GuildAnnouncement` and calls `notify_members` (so it's covered automatically), and its **site** branch has `guild is None` (no mailing list; adding `self.guild.…` there would `AttributeError`).
- The Discord broadcast is unchanged (custom emails are email-only; Discord still posts once to the guild channel).

**Invariant:** the count/list shown in the UI must equal what's delivered. Both are built from `announcement_recipients()` (members) + `mailing_list_emails_deduped(member_emails)` (custom), with the same lower-cased `user.email` key — so the "Your Mailing List" section and the actual send can't drift.

## 6. UI / UX  ← completeness checklist applied concretely

### (A) Rename to "Guild Settings"
- `guild_edit.html:2` title → `{% block title %}{{ guild.name }} Settings{% endblock %}`.
- `guild_edit.html:7` h1 → `{{ guild.name }} Settings` (keep the existing "← Back to Guild Page" button at `:8`).
- `guild_detail.html:64` `title="Edit Guild Page"` → `title="Guild Settings"`; `:66` visible label `Edit` → `Guild Settings` (keep the pencil icon). No URL/behavior change.

### (B) "Your Mailing List" section — top of the Announcements/Emails tab
- **Screen / partial:** `templates/hub/guild_edit.html`, inside `section === 'announcements'`, a **new `<form method="post" action="{% url 'hub_guild_mailing_list_save' guild.pk %}">`** inserted at the very top of the tab (before the pending-proposals banner at `:559`). It is its **own top-level form with its own endpoint** — NOT nested in the emails form (`:608`) or the page main form (nested-form trap; the studio-hours/FAQ/Links editors all do this).
- **Heading + tooltip:** `<h2>Your Mailing List</h2>` with a `.pl-help` span (copy from `guild_detail.html:244-247`), bubble text (softened — the dual send is toggle-controlled, not absolute): *"Everyone here gets your guild's announcement emails: members are added automatically, plus any custom addresses below. By default an announcement goes out to email **and** your guild's Discord channel — you can turn either off when you compose."*
- **Members block (read-only) — this section is the ONE place the roster is shown.** Render "**{{ announcement_recipient_count }} members** are on your list automatically" + a "Show members" `x-show` list of `announcement_recipient_emails`, and keep the caveat that members without an email aren't included. **Move this display out of the "Post an Announcement" card** — the old reach block at `guild_edit.html:572-590` is **removed** from that card so the roster isn't shown twice on the tab (see the reach-line fix below).
- **Custom addresses editor (list editor — all three controls named):** clone the **Links** editor (`:498-556`):
  - Rows: per `mailing_list_formset` form — `{{ f.id }}`, `{{ f.sort_order }}` hidden; `{% include "components/form_field.html" with field=f.email %}` and `f.label`; a **per-row Delete button** — real `pl-btn pl-btn--danger pl-btn--sm` with `margin-top:0.75rem` — that flips `{{ f.DELETE }}` and calls `this.form.requestSubmit()` (saved rows) or removes the DOM node (unsaved clones).
  - **"+ Add address" button** that clones a hidden `<template id="mailing-list-empty-template">` of `mailing_list_formset.empty_form`, `replaceAll('__prefix__', idx)`, appends, and bumps `id_mailing_list-TOTAL_FORMS`.
  - **Save button** — `pl-btn pl-btn--primary`, "Save mailing list", submits this form → `guild_mailing_list_save`.
  - **Empty state:** with no custom rows, show *"No custom addresses yet. Add one below or import a list."* above the Add button.
- **CSV / list import (its own small `<form enctype="multipart/form-data">`, sibling to the editor):**
  - A **file input** (`accept=".csv,.txt"`, label "Import addresses") + an **"Import" submit button** → `guild_mailing_list_import` (its own endpoint). Hint: *"Upload a CSV or text file — one email per line, with an optional second column for a label."*
  - The view parses leniently: split on newlines/commas, run each token through Django's `EmailValidator`, lower-case, and create `GuildMailingListEmail` rows — **skipping** invalid tokens, addresses already on the custom list, and any that match a member email. If a row has a second CSV column, use it as the `label`.
  - **Feedback (Django message summarizing the outcome):** *"Imported 12 addresses. Skipped 3 already on your list, 2 that are members, and 1 invalid."* Empty/no-file/all-invalid → a friendly error message, no rows created.
- **Validation / error state:** `email` is an `EmailField` → an invalid manual entry **re-renders the tab with inline field errors** by passing the bound `mailing_list_formset` back into `_guild_edit_context` and calling `render(...)` (NOT redirect) — mirror **`guild_emails_save`** (`hub/views.py:~2168`, which re-renders with `emails_form=form`), **not** `guild_links_save` (which flashes + redirects and loses the typed input). The re-render must **re-open the Announcements tab** (the page `x-data` `section` seeds from a context flag / `?tab=announcements`, since a bare POST has no `?tab` and would default to `basic`, hiding the error). Duplicate `(guild,email)` → the unique-constraint error surfaces on the row.
- **Success feedback:** full-page POST → Django messages + redirect `?tab=announcements` on success, consistent with the other editors.
- **Dark + light:** all fields via `form_field.html` (`.pl-form-group` scope) — no inline `background`/`color`. `.pl-help` CSS is already global. Verify both themes.
- **Mobile:** rows are label+email stacked; the Add/Delete/Save are real buttons on the 8px grid; no table, no horizontal scroll.

## 7. Notifications / emails / activity

No new triggers. Guild-announcement emails now **also** reach the guild's custom addresses (via the additive `extra_emails`) whenever the announcement sends email; the Discord channel post and the member emails are unchanged. Custom recipients are non-members (no account, no in-app notification, no activation gate) — email only. **One choke point:** wire `extra_emails` into `GuildAnnouncement.notify_members` — every real send (guild-edit create, member-proposal approve, and `AnnouncementDraft.send`'s guild branch, which all funnel through `notify_members`) picks it up automatically. `AnnouncementDraft.send`'s **site** branch is intentionally excluded (`guild is None` — no mailing list).

## 8. Build order (each phase green)

1. **Model + migration** — `GuildMailingListEmail` + `Guild.mailing_list_emails_deduped()`. Green.
2. **Additive send** — `emit(extra_emails=…)` (member fan-out preserved, default `None` = no-op for all other callers) wired into `notify_members` only, gated on `self.send_email`; tests prove members STILL receive AND custom addresses receive, deduped on lower-cased `user.email`, and that email-off suppresses both. Green. *(Highest-risk phase — do it before UI.)*
3. **Editor + import** — form + formset + `guild_mailing_list_save` (inline-error re-render) + `guild_mailing_list_import` (CSV/list parse) views + URLs + `_guild_edit_context` wiring + the template section (roster + editor + import). Green.
4. **Rename** — the title/h1/entry-button label changes. Green.
5. **Version + changelog** — one member-facing v23 entry (the rename is cosmetic, no entry): *"Guild leads: your Guild Settings page now has a Your Mailing List section — see who your announcements reach, add custom email addresses beyond your members, and import a list."* (Handled centrally with the other v23 entries.)

## 9. Testing

BDD `*_spec.py`, factory-boy (`GuildMailingListEmailFactory`), respx where a send touches Discord, ≥98% coverage, Docker `plfog-web`.

- **Model:** `mailing_list_emails_deduped` lower-cases, sorts, and drops collisions with member emails; unique `(guild,email)` enforced.
- **Send (critical):** with members + custom emails, `notify_members` and `AnnouncementDraft.send` deliver to **every member** AND **every custom address**; a custom address equal to a member email is sent **once**; `suppress_email` still suppresses everything; the Discord post still fires once. Assert on the outbox + the emit fan-out (mock the Discord webhook).
- **Emit unit:** `extra_emails` is additive — passing it does NOT suppress the per-recipient member loop (guards the `emit.py:161` trap); dedup against member recipients.
- **Form/formset:** add, edit, delete a custom email through `guild_mailing_list_save`; invalid email → bound formset **re-rendered inline** (not redirected) with the announcements tab open and the typed input preserved; duplicate `(guild,email)` surfaces the unique-constraint error; permission gate (`_require_can_edit_guild`).
- **CSV import (`guild_mailing_list_import`):** a file of mixed lines/commas creates rows for the valid new emails and **skips** invalid tokens, existing custom addresses, and member-collisions (case-insensitive); the summary message reports the counts; a second CSV column becomes the `label`; empty/no-file/all-invalid → error message, no rows created; permission gate.
- **Template:** the "Your Mailing List" section renders once at the top of the announcements tab with the tooltip, the members count/list (and the old reach block is **gone** from the Post-an-Announcement card — asserted so the roster isn't duplicated), the Add/Delete/Save controls, the import form, and the empty state; the page title/h1 say "Settings".

## 10. Open / deferred

- **Choosing a subset of recipients per announcement ("send to some, not all")** — its own spec, `2026-07-17-announcement-recipient-selection.md`, which builds ON this roster (selects a subset of members + custom at compose time). Not in this spec.
- **Per-custom-address opt-out / bounce handling** — out of scope; these are fire-and-forget announcement recipients.

> Spec only — build under v23, **after** `/join-guild` lands (shared files: `guild_edit.html`, `hub/forms.py`, `hub/views.py`, `membership/models.py`).
