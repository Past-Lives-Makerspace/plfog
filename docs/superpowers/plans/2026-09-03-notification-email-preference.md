# Notification email preference

**Date:** 2026-09-03
**Surface:** FOG hub — Settings → Account tab (Manage Email Addresses card)
**Size:** Small (one model field, one resolver, one small form + endpoint, adapter wiring)

## Overview

A member with multiple verified email addresses on their account can choose which address their
**email notifications** are sent to. Today every event-driven notification email goes to
`user.email` — the allauth primary mirror — so a member who keeps a guild address (e.g.
`woodshop@pastlives.space`) as a secondary can't route notifications there without flipping their
login primary. This feature adds a per-member notification target, defaulting to the primary.

## Locked decisions

| Decision | Call | Why |
|---|---|---|
| Scope of "email notifications" | The `core.events` pipeline only: `EmailAdapter`, `ScheduledEmailAdapter` (both in `core/events/channels.py`). | That is the system members control from Settings → Notifications. Login codes, address-verification mail, and direct transactional senders (billing receipts, orientation/class emails) keep their current targeting — a login code must go to the address being verified. |
| Storage | New `Member.notification_email` — `EmailField(blank=True, default="")`, `help_text` per standards. Blank = follow the primary. | One field, no backfill; migration is a plain schema add. |
| Resolution | New module-level helper `notification_email_for(user) -> str` in `core/events/channels.py` (or `core/email.py` if cleaner for imports), used by both adapters. Logic: if the user's member has `notification_email` set AND that address still exists as a **verified** `EmailAddress` row on this user → return it; else return `user.email`. | Fail-soft at read time: deleting or unverifying the chosen address silently falls back to primary, never strands notifications. No signal/cleanup plumbing needed. |
| Validation | New `NotificationEmailForm` (hub/forms.py): a `ChoiceField` whose choices are built in `__init__` from the user's **verified** `EmailAddress` rows, plus a blank "Primary email (default)" choice. Form validation is the only write path — the model field itself stays a plain EmailField. | Forms own validation (house rule). Unverified addresses are never offered. |
| UI placement | Inside the existing **Manage Email Addresses** card on the Settings → Account tab, below the address list, as its own small form. | The member is already thinking about their addresses there; the card's copy line about notifications is updated to match. |
| Suppression/dedup guard | In `core/events/emit.py`, the `extra_emails` additive path builds `member_emails_lower` from `user.email` to drop an extra address that already belongs to a recipient. **Extend** that set (keep the `user.email` entries, add each recipient's resolved notification target). The `email_to`/explicit path needs no change — `suppress_user_email` already suppresses the member loop there. | Otherwise an `extra_emails` address equal to a recipient's chosen target gets the mail twice. |
| Admin | No admin surface. The auto-registered Member admin exposes the raw field, which is enough. | YAGNI. |

## What already exists (reuse map)

| Piece | Where | Reused as |
|---|---|---|
| Email choke-point | `core/email.py::send` | Untouched — adapters keep calling it. |
| Both notification adapters | `core/events/channels.py` `EmailAdapter.deliver`, `ScheduledEmailAdapter.deliver` | Swap `user.email` for `notification_email_for(user)` (both the empty-check and the `to=`). |
| Verified-address source of truth | `allauth.account.models.EmailAddress` (user FK, `verified`, `primary`) | Choices + resolver check. |
| Manage Email Addresses card | `templates/hub/user_settings.html`, Account tab | Host for the new form. |
| Settings POST pattern | Existing hub settings endpoints (plain POST → redirect back to the tab) | New `hub_notification_email_set` view follows it. |
| Member email docs | `Member.primary_email` three-store note | The new field gets a docstring cross-reference; `primary_email` itself is untouched. |

## Data model

```python
# membership/models.py — on Member
notification_email = models.EmailField(
    blank=True,
    default="",
    help_text=(
        "Verified address email notifications are sent to. Blank means the primary email. "
        "If this address is later removed or unverified, notifications fall back to the primary."
    ),
)
```

One migration: `AddField`, no data migration.

## Resolver

```python
def notification_email_for(user: User) -> str:
    """The address event-driven notification email should go to for this user.

    The member's chosen ``notification_email`` wins only while it is still a
    verified address on this user; otherwise the allauth primary mirror
    (``user.email``) is used. Never raises; returns "" when the user has no email.
    """
```

Implementation notes:
- Look up the member via `getattr(user, "member", None)`-safe access (some Users have no Member);
  no member or blank field → `user.email or ""`.
- Verify with one query: `EmailAddress.objects.filter(user=user, email__iexact=chosen, verified=True).exists()`.
- One extra query per recipient is acceptable — notification fan-outs are small. Do **not** add
  caching or prefetch plumbing.

Both adapters change their guard and target:

```python
address = notification_email_for(user)
if not address.strip():
    return
send_email(to=address, ...)
```

## Form and view

- `NotificationEmailForm(forms.Form)` with a single `notification_email = forms.ChoiceField(required=False)`.
  `__init__(user, ...)` builds choices: `[("", "Primary email (default)")] + [(ea.email, ea.email) for ea in verified]`.
  `save()` writes `user.member.notification_email` (`update_fields=["notification_email"]`).
- View `hub_notification_email_set` (hub/views.py): `@login_required`, POST-only. **Memberless users**
  (`getattr(request.user, "member", None) is None` — a real state, see the profile POST guard) get the
  existing "not linked to a membership" error-message pattern and a redirect; no save attempted. Otherwise
  bind the form; on valid, save + `messages.success(request, "Notification email updated.")`, redirect to
  the Settings Account tab. On invalid (tampered POST value) redirect back with
  `messages.error(request, "Choose one of your verified addresses.")` — no partial state written.
- URL: `hub/urls.py` alongside the other settings endpoints.
- The settings view passes an **always-unbound** form into the template context (the endpoint redirects in
  every branch, so field-level errors are unreachable; message banners are the only feedback surface).
  Initial value: match the stored `member.notification_email` against the verified rows with the **same
  `iexact` semantics as the resolver**, and map it to the canonical `ea.email` casing; no match → "" so
  the select never renders a dangling value and never shows "Primary" while mail actually routes elsewhere.

## UI / UX (completeness pass)

Screen: **Settings → Account tab → Manage Email Addresses card** (`templates/hub/user_settings.html`).

- **Copy fix first:** the card's intro line currently says "The primary email receives account
  notifications." Change to: "Each verified email can be used to log in. Notifications go to your
  primary email unless you pick a different one below." (Plain language, no dashes.)
- **Picker visibility:** render the picker only when the user has a Member row AND 2+ verified
  addresses. Memberless users see nothing new in the card.
- **The picker (2+ verified addresses):** a new block placed **after the address-list form's closing
  `</form>` and before the "Add an Email Address" heading** — the card contains two allauth forms
  posting to `account_email` (the list form and the Add form) and the new form must nest inside
  neither (documented nested-form bug in this template):
  - Label: **Send notifications to** (rendered via `components/form_field.html` with the ChoiceField —
    the component gives the label, select styling, and error slot for free, in both themes).
  - A visible **Save notification email** submit button (`hub-btn hub-btn--sm hub-btn--primary`),
    inside this form only — it must NOT sit inside the allauth `account_email` form above (nested-form
    bug called out in this template's comments). Give the block `margin-top: 1rem` so it clears the
    allauth action buttons.
  - Success state: standard Django message banner after redirect ("Notification email updated.").
  - Error state: message banner ("Choose one of your verified addresses.") — reachable only by a
    tampered POST, but specified so the view never 500s.
- **Fewer than 2 verified addresses (but at least one address on file):** hide the select + button;
  render a single muted line instead: "Notifications go to your primary email. Add and verify another
  address to choose a different one." No dead dropdown with one option.
- **Zero addresses on file:** the card already renders "No email addresses on file yet." — show
  nothing extra (the muted line above would reference a primary that doesn't exist).
- **Chosen address later deleted/unverified:** resolver falls back to primary at send time (no user
  action required), and the select renders back at "Primary email (default)" because the initial-value
  coercion above drops the dangling value. No error surfaces — the system self-heals.
- **Unverified addresses:** never listed in the select; the pill UI in the card already shows their
  Unverified state, which is the user's cue to verify first.
- **Dark/light themes:** only `form_field.html` + existing `hub-` classes; no inline colors.
- **Mobile:** the select and button stack naturally in the card (block-level, full width at narrow
  sizes per existing `hub-form` behavior); no new layout needed.
- **No list editing here** — the address add/remove flows stay on the existing allauth forms; this
  feature adds exactly one select + one button.

## Tests (BDD, `*_spec.py`)

- `tests/core/notification_email_spec.py` (or fold into an existing events spec dir):
  - `describe_notification_email_for`: chosen verified address wins; blank field → primary; chosen but
    unverified → primary; chosen but deleted → primary; user with no member → `user.email`; user with
    nothing → "".
  - `describe_EmailAdapter` / scheduled adapter: delivers to the chosen address; skips when resolver
    returns "".
  - Suppression: an `extra_emails` address equal to a recipient's resolved notification target is
    dropped from the additive list (and the existing `user.email` dedup keeps working).
- `tests/hub/notification_email_settings_spec.py`:
  - Form offers only verified addresses plus the default choice.
  - POST with a verified secondary saves it; POST with an unlisted/unverified address saves nothing and
    redirects with the error message.
  - Settings page shows the picker with 2+ verified addresses; the muted fallback line with exactly 1;
    nothing extra with 0 addresses; nothing for a memberless user.
  - Memberless POST gets the error-message redirect, no crash.
  - Dangling stored value renders as default; a case-differing stored value renders as the canonical
    verified address (not as default).
- Changelog collision sweep after writing the entry (house rule).

## Version & changelog (build time)

Minor bump; one member-facing entry, e.g. title "Choose where your notifications go" — plain language,
no dashes, swept against negative assertions.

## Out of scope

- Routing login codes, verification mail, or direct transactional senders (billing, orientation,
  class emails) through the preference.
- Per-category notification addresses.
- Any admin UI beyond the auto-registered model admin.
- Changing `Member.primary_email` semantics anywhere.

## Done means

A member with `woodshop@pastlives.space` verified as a secondary picks it in Settings → Account, and
the next event-driven notification email lands there; deleting that address later silently reverts
notifications to their primary with no error and no stale UI.
