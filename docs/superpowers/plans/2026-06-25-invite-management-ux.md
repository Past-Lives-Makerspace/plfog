# Invite management UX in manage-members — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-25
**Surface:** FOG hub `pastlives.test:8000` — `/manage/members/` (the Manage Members page).
**Related:**
- Sibling spec: *Branded notification emails* — owns the appearance of the invite email itself. **This spec does not touch email styling**; the resend action just re-fires the existing `member.invited` event.
- Prior art for the page shape, role gating, and destructive actions: `docs/superpowers/plans/2026-06-21-registrations-admin-tab.md`.

---

## 1. Overview

Today, inviting a new FOG member is a one-shot, fire-and-forget action: a bare email box on the Manage Members page POSTs to `Invite.create_and_send` and that is the end of it (`templates/hub/admin/members.html:67-82`). There is **no way to see who has been invited, whether they ever signed up, or to nudge someone who hasn't** — an admin who wants to follow up has to go digging in the Django admin.

This feature makes onboarding genuinely manageable from the hub:

- **Send a single invite** — the existing inline form, cleaned up to use the standard field component and theme tokens, with a success/error **toast** instead of a full-page redirect.
- **See outstanding invites** — a card on the same page listing each invite with its **status** (pending / accepted / expired) and **when it was sent** ("3 days ago").
- **Resend** a pending or expired invite with one click (re-fires the invite email).
- **Revoke** an invite that was sent by mistake or to the wrong address (included — it is a small, self-contained model method + confirm modal).

Invites matter most when `SiteConfiguration.registration_mode == INVITE_ONLY` (the default — `core/models.py:109`), where an emailed invite is the *only* way a new person can register. The card stays useful in `OPEN` mode too (it still tracks who you've personally invited), so it is always shown.

### Locked decisions

| Decision | Choice |
|---|---|
| "Expired" — new field or derived? | **Derived from last-sent time**, not stored as a state. An un-accepted invite whose most-recent send is older than `INVITE_EXPIRY_DAYS` (14) reads as *expired*. This needs **one nullable `last_sent_at` field** (auto-reversible `AddField` migration) so that **Resend resets the clock** — see §3 for why a pure `created_at` derivation would leave a just-resent invite still reading "Expired." |
| Does Resend update the badge/age? | **Yes.** `send_invite_email()` stamps `last_sent_at`, and both "sent N ago" and `is_expired` derive from `last_sent_at or created_at`, so a resent invite immediately reads "Pending · sent just now." |
| Where the invites list lives | A new **"Invites" `.hub-card`** on the existing `/manage/members/` page, above the roster table. No new route/nav item — invites are part of "manage members," and keeping them together lets the admin invite → watch → follow up in one place. |
| What the list shows | **Un-accepted invites (pending + expired) always**, plus invites **accepted in the last 30 days** so the admin sees recent successes. Accepted invitees become real members in the roster below, so old accepted invites drop off rather than piling up. |
| Resend scope | Allowed for **pending and expired** (un-accepted) invites. Re-fires `Invite.send_invite_email()`, which already forces a fresh send every time (`core/models.py:350-381`). |
| Revoke | **Included** — destructive, behind `confirm_modal.html`; deletes the invite and its bare placeholder Member. |
| Send/resend feedback | **HTMX + toast + partial list refresh** (per FRONTEND rule 6). Revoke uses the standard `confirm_modal` full-page POST + Django message (page reload refreshes the list). |
| Validation home | **Reuse the existing `InviteMemberForm`** (`membership/forms.py:29`) — it already validates email format, not-already-a-member, and no-duplicate-pending-invite. View stays thin. |

---

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Create invite + placeholder member + send email + log activity | `Invite.create_and_send(email, invited_by)` (classmethod, guards dupes, requires a `MembershipPlan`) | `core/models.py:299-348` |
| (Re)send the invite email — forced, always fresh | `Invite.send_invite_email()` (emits `member.invited` with a unique idempotency `period`) | `core/models.py:350-381` |
| Mark accepted on signup | Adapter `pre_login` **bulk-updates** `accepted_at` for matching un-accepted invites when `signup=True` — it does **not** call `mark_accepted()` | `plfog/adapters.py:133-141` |
| "Is it accepted yet?" | `Invite.is_pending` property (`accepted_at is None`) | `core/models.py:272-275` |
| Invite form + all validation | `InviteMemberForm` (EmailField + `clean_email`: not-already-member, no-duplicate-pending) | `membership/forms.py:29-40` |
| Existing (bare) invite UI | inline `<form>` toggled by "+ Invite a member" | `templates/hub/admin/members.html:67-82` |
| Existing invite view (POST → create_and_send → redirect + message) | `admin_member_invite` | `hub/views.py:2213-2229` |
| Members list view + context | `admin_members` | `hub/views.py:2032-2078` |
| Admin gate (honors actual role, ignores view-as) | `@fog_admin_required` | `hub/view_as.py:205-220` |
| Invite URL | `hub_admin_member_invite` → `/manage/members/invite/` | `hub/urls.py:154` |
| Toast from a view | `trigger_toast(response, msg, type)` (sets `HX-Trigger`) | `hub/toast.py:10` |
| Status pill styling | `.hub-pill` + `.hub-pill--ok/--warn/--primary` | `static/css/hub.css:372-382` |
| Destructive button + confirm | `.hub-btn--sm .hub-btn--danger` + `components/confirm_modal.html` | `static/css/hub.css:844`; `templates/components/confirm_modal.html` |
| Field wrapper (theme-correct input, label, error) | `components/form_field.html` | per FRONTEND.md |
| "3 days ago" rendering | `{{ value|timesince }} ago` (already used in hub) | `templates/hub/_notification_feed.html:7`, `templates/hub/member_directory.html:45` |
| Activity log sink | `SiteActivity.log(Kind, actor=, payload=)`; `Kind` is a `TextChoices` (e.g. `MEMBER_INVITED`) | `core/models.py:557`, `:575` |

### What I found about the `Invite` model (read, not assumed)

`Invite` (`core/models.py:243-382`) has exactly these fields:

- `email` — `EmailField(unique=True)`
- `invited_by` — `FK(User, on_delete=SET_NULL, null=True, blank=True)`
- `member` — `OneToOneField(membership.Member, on_delete=SET_NULL, null=True, blank=True, related_name="invite")`
- `created_at` — `DateTimeField(auto_now_add=True)`
- `accepted_at` — `DateTimeField(null=True, blank=True)`

`Meta.ordering = ["-created_at"]`. There is **no custom manager** (plain `objects`) and **no expiry field/concept** — the only stored state is `accepted_at is None` (pending) vs. set (accepted).

**Acceptance path (corrected — read, not assumed):** the real signup flow does **not** call `mark_accepted()`. The allauth adapter's `pre_login` (`plfog/adapters.py:133-141`) sets acceptance directly with a bulk `Invite.objects.filter(email__iexact=user_email, accepted_at__isnull=True).update(accepted_at=timezone.now())` when `signup=True`. `mark_accepted()` (`core/models.py:277-297`) exists and would emit the `invite_accepted` event, but **nothing on the signup path calls it**, so `invite_accepted` does **not** fire on acceptance. For this feature that's immaterial: `accepted_at` is set either way, so `status` and `for_management_panel()` read correctly. (Implication for §10: do **not** write a test asserting `invite_accepted` fires through the signup flow.)

`create_and_send()` already guards "member exists" and "pending invite exists" and requires a `MembershipPlan`.

**Genuine gaps to close (kept minimal):**
1. No way to derive **expired**, and no way for **Resend to reset that derivation** — add one nullable `last_sent_at` field (auto-reversible migration), a cheap `is_expired` property, and a tunable threshold constant.
2. No manager method to fetch the list the panel needs — add an `InviteManager`. *(No migration.)*
3. No **resend** or **revoke** view/route.
4. The UI shows none of this and the existing form inline-styles its `<input>` (a theme rule-13 latent bug — see §6).

---

## 3. Data model

**One new nullable field (`last_sent_at`) + an auto-reversible `AddField` migration. No state field for "expired" — it stays derived.** Why a field at all: "expired" must derive from the **most-recent send**, not `created_at`. `created_at` is `auto_now_add` and `send_invite_email()` doesn't touch it, so deriving expiry from `created_at` alone would leave a *just-resent* invite still rendering "Expired · sent 20 days ago" — the badge and age would actively lie. Storing the last send time and deriving from `last_sent_at or created_at` makes **Resend reset the clock**, which is the whole point of a resend.

"Expired" is still **only advisory** — an invite's signup link does not stop working with age (the adapter only checks `registration_mode` + that the email was invited — `plfog/adapters.py:79-99`), so the badge is a nudge ("this has been sitting unanswered — resend it"), never a hard lock. We add the field for *display honesty*, not enforcement.

### New: `last_sent_at` field + migration

```python
# core/models.py — Invite
last_sent_at = models.DateTimeField(
    null=True, blank=True,
    help_text="When the invite email was most recently sent. Resending updates this; expiry is measured from it.",
)
```

- **Migration:** a single `AddField` (nullable). Django's reverse is `RemoveField` — **auto-reversible**, no `RunPython`, no data backfill (existing rows stay `NULL` and fall back to `created_at` via the coalesce below). Per CLAUDE.md the migration is reversible by construction. Remember `ruff format` + `git add` the new migration together (it's checked by CI `ruff format --check`).
- **Set it on every send:** `send_invite_email()` (`core/models.py:350-381`) gains, before `emit(...)`, `self.last_sent_at = timezone.now()` then `self.save(update_fields=["last_sent_at"])`. Because `create_and_send()` calls `send_invite_email()` right after creating the row, the first send populates it too; resends re-stamp it.

### New: tunable threshold (settings constant)

`plfog/settings.py` — add an explicit, env-overridable constant near the other site knobs:

```python
INVITE_EXPIRY_DAYS = int(os.environ.get("INVITE_EXPIRY_DAYS", "14"))
```

### New: status enum + derived properties on `Invite`

```python
from datetime import timedelta  # at module top

class Invite(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        EXPIRED = "expired", "Expired"

    # ...existing fields + last_sent_at...
    objects = InviteManager()  # see §5

    @property
    def sent_at(self):
        """The timestamp the UI means by 'sent' — most-recent send, else creation."""
        return self.last_sent_at or self.created_at

    @property
    def is_expired(self) -> bool:
        """Un-accepted and last sent longer ago than the expiry window (advisory only)."""
        if not self.is_pending:
            return False
        cutoff = timezone.now() - timedelta(days=settings.INVITE_EXPIRY_DAYS)
        return self.sent_at < cutoff

    @property
    def status(self) -> str:
        """Derived lifecycle state: accepted / expired / pending."""
        if not self.is_pending:
            return self.Status.ACCEPTED
        return self.Status.EXPIRED if self.is_expired else self.Status.PENDING

    @property
    def status_label(self) -> str:
        """Human label for the derived status (templates can't call Status(...) with an arg)."""
        return self.Status(self.status).label
```

`status` returns the machine value (used for the pill-class `{% if %}` switch); `status_label` gives the template the display word without it needing to call `Invite.Status(invite.status).label` (Django templates can't invoke a class with an argument). Both derivation and labeling stay on the model; the template only picks a CSS class. The "sent N ago" display uses `{{ invite.sent_at|timesince }} ago`.

### Optional (only if Revoke logs activity): one trivial choices migration

If `revoke()` logs to `SiteActivity` (recommended — fat models log their own side effects), add one `Kind`:

```python
# core/models.py — SiteActivity.Kind
MEMBER_INVITE_REVOKED = "member_invite_revoked", "Member invite revoked"
```

This is a `CharField.choices` change → a **single `AlterField` migration**, which Django makes **reversible automatically** (the reverse simply restores the prior choice set; no `RunPython`, no data change). If a reviewer prefers zero migrations, drop the one `SiteActivity.log(...)` line in `revoke()` and the migration disappears — the rest of revoke is unaffected. (Per CLAUDE.md: any added migration is reversible; this one is reversible by construction.)

---

## 4. Form

**Reuse `InviteMemberForm`** (`membership/forms.py:29-40`) unchanged — it already does everything the brief asks for:

| Validates | Message the user sees |
|---|---|
| Email format | (Django EmailField default) "Enter a valid email address." |
| Not already a member | "A member with this email already exists." |
| No duplicate pending invite | "A pending invite for this email already exists." |

`Invite.create_and_send()` independently re-guards the same two conditions (`core/models.py:315-319`) and raises `ValueError`, so the form is the user-facing validator and `create_and_send` is the integrity backstop — both stay. The view surfaces form errors and `ValueError` as **error toasts** (see §6). No new form is needed for the single-invite path.

(Bulk invite would need a new form — see §9, optional stretch.)

---

## 5. Manager methods (fat model — no date logic in the view)

New `InviteManager(models.Manager)` in `core/models.py`, assigned as `Invite.objects`. All windowing/date math lives here. Note the imports at the module top: `from datetime import datetime, timedelta` and `from django.db.models.functions import Coalesce` (so `pending()`/`expired()` partition on the same `last_sent_at or created_at` timestamp the `is_expired` property uses — otherwise the manager and the property could disagree about a resent invite).

```python
class InviteManager(models.Manager):
    def _expiry_cutoff(self) -> datetime:
        return timezone.now() - timedelta(days=settings.INVITE_EXPIRY_DAYS)

    def outstanding(self) -> QuerySet[Invite]:
        """Un-accepted invites (pending + expired), newest first, joins prefetched."""
        return self.filter(accepted_at__isnull=True).select_related("invited_by", "member")

    def pending(self) -> QuerySet[Invite]:
        sent = Coalesce("last_sent_at", "created_at")
        return self.outstanding().annotate(_sent=sent).filter(_sent__gte=self._expiry_cutoff())

    def expired(self) -> QuerySet[Invite]:
        sent = Coalesce("last_sent_at", "created_at")
        return self.outstanding().annotate(_sent=sent).filter(_sent__lt=self._expiry_cutoff())

    def for_management_panel(self) -> QuerySet[Invite]:
        """What the Invites card shows: all un-accepted + recently-accepted (last 30d)."""
        recent_accept = timezone.now() - timedelta(days=30)
        return self.filter(
            models.Q(accepted_at__isnull=True) | models.Q(accepted_at__gte=recent_accept)
        ).select_related("invited_by", "member")
```

(`Meta.ordering = ["-created_at"]` already gives newest-first; no per-query `order_by` needed.) `pending()`/`expired()` partition on the coalesced send time so they stay consistent with the `is_expired` property, and exist so tests and any future filtering never recompute the cutoff. The view calls **only** `Invite.objects.for_management_panel()` and reads each row's `status` property for the badge.

### New model method: `revoke()`

```python
def revoke(self) -> None:
    """Cancel an un-accepted invite: remove it and its bare placeholder member.

    Raises:
        ValueError: if the invite was already accepted (the person is now a real member).
    """
    if not self.is_pending:
        raise ValueError("Cannot revoke an invite that has already been accepted.")
    member = self.member
    self.delete()
    # Clean up ONLY a placeholder this flow created itself: a bare INVITED stub with no
    # linked user AND no Airtable origin. create_and_send REUSES a pre-existing INVITED
    # placeholder pulled from Airtable (core/models.py:328-336); Members are read-only
    # from Airtable by contract, so deleting one here would destroy imported data. The
    # `not member.airtable_record_id` guard (field: membership/models.py:178) keeps revoke
    # from ever touching an imported stub — it just detaches the (now-deleted) invite.
    if (
        member is not None
        and member.user_id is None
        and member.status == Member.Status.INVITED
        and not member.airtable_record_id
    ):
        member.delete()
    # Optional (drops the choices migration if omitted):
    SiteActivity.log(SiteActivity.Kind.MEMBER_INVITE_REVOKED, payload={"email": self.email})
```

Resend needs **no new model method** — it calls the existing `Invite.send_invite_email()` (`core/models.py:350-381`), which now also stamps `last_sent_at` (§3).

---

## 6. UI / UX completeness — apply the checklist concretely

Everything lives on **`/manage/members/`** → `templates/hub/admin/members.html`, plus one new partial. Both themes verified; mobile reflow specified; no inline form-control colors.

### 6.1 Page structure

The page gains a new **"Invites" `.hub-card`** placed **above** the existing Manage Members card. The "+ Invite a member" toggle button moves out of the members-card header into this new card's header. The members roster card is otherwise unchanged.

```
hub-card  (NEW)  — "Invites"
  ├─ header: "Invites"  +  [ + Invite a member ]  (Alpine toggle, x-data="{ showInvite:false }")
  ├─ send-invite form        (x-show="showInvite", closed by default — FRONTEND optional-form rule)
  └─ #invites-list           ← include "hub/admin/_invites_panel.html"  (the swappable region)

hub-card (existing) — "Manage Members"  (filters + roster table, unchanged)
```

### 6.2 Single invite — the send form

- **Container:** the toggle-revealed `<form>` in the Invites card. One field → it stays compact, revealed via the "+ Invite a member" button (`x-show`, `x-cloak`, closed by default).
- **Field:** rendered with **`components/form_field.html`** bound to `form.email` (`InviteMemberForm`). **This replaces the current bare `<input type="email">` that inline-styles `background`/`color` (`templates/hub/admin/members.html:48-62`) — a latent FRONTEND rule-13 white-box bug.** `form_field.html` wraps the input in the hub field scope, so it inherits `--hub-input-bg` / `--hub-input-border` / `--text` and is correct on both themes with zero inline color.
- **Submit:** a visible **"Send invite"** primary button (`.hub-btn .hub-btn--sm .hub-btn--primary`) plus a ghost **"Cancel"** that closes the form. The form posts via HTMX:
  - `hx-post="{% url 'hub_admin_member_invite' %}"`, `hx-target="#invites-list"`, `hx-swap="outerHTML"`.
  - **Success:** view returns the re-rendered `_invites_panel.html` (200) → the list swaps to include the new pending row; a **success toast** "Invite sent to jane@x.com" rides the `HX-Trigger`. On `htmx:afterRequest` (success), Alpine resets `showInvite=false` and clears the input.
  - **Error (invalid email / dupe / `ValueError`):** view returns **204 No Content** (HTMX makes no swap) + an **error toast** carrying the exact message (e.g. "A pending invite for this email already exists."). The form stays open with the typed value so the admin can fix it.
- **Loading:** `hx-disabled-elt="this"` on the submit button disables it while in flight (prevents double-send).

### 6.3 Outstanding-invites list — `templates/hub/admin/_invites_panel.html`

A **flex-based list, not a `<table>`** — chosen specifically so it reflows on mobile with no horizontal scroll (a table of email + date + status + two buttons is exactly the thing that blows out a phone viewport). Each invite is a `.pl-invite-row`:

```
.pl-invite-list                          (the #invites-list region wrapper)
  .pl-invite-row                         (flex, wrap, gap; one per invite)
    .pl-invite-row__email   jane@x.com
    .pl-invite-row__meta    Sent 3 days ago        ({{ invite.sent_at|timesince }} ago)
    .hub-pill .hub-pill--*  Pending / Accepted / Expired
    .pl-invite-row__actions [ Resend ]  [ Revoke ]      (un-accepted only)
```

**Columns / content per row:**
- **Email** — `invite.email`.
- **Sent** — `Sent {{ invite.sent_at|timesince }} ago` (`sent_at` = `last_sent_at or created_at`, so it reflects the latest resend — §3); for accepted ones also show `· Joined {{ invite.accepted_at|timesince }} ago` in muted text.
- **Status badge** — `.hub-pill` with a modifier chosen by a `{% if invite.status == 'expired' %}…` switch on `invite.status` (mapping in §6.4), label `{{ invite.status_label }}` (the model property added in §3 — `status` is a `@property`, so Django generates **no** `get_status_display`/`label`; the template can't call `Invite.Status(invite.status).label` with an argument, hence the property).
- **Actions** (only when `invite.is_pending`, i.e. pending **or** expired):
  - **Resend** — a real `<button class="hub-btn hub-btn--sm">` (non-destructive, no modal). `hx-post="{% url 'hub_admin_invite_resend' invite.pk %}"`, `hx-target="#invites-list"`, `hx-swap="outerHTML"`, `hx-disabled-elt="this"`. Success → list re-renders (the row now reads "Pending · sent just now" because the resend stamped `last_sent_at`) + toast **"Invite resent to jane@x.com"**. Disabled (and shows an inline spinner via the htmx-indicator) while sending.
  - **Revoke** — a real `<button class="hub-btn hub-btn--sm hub-btn--danger">` wired to **`components/confirm_modal.html`**: `@click="$dispatch('open-confirm', 'revoke-invite-{{ invite.pk }}')"`. Spacing from the Resend button beside it comes from `.pl-invite-row__actions { display:flex; gap:0.5rem; }` (8px grid) — **no inline `margin-left`**. One `confirm_modal` per row, message **"Revoke the invite for jane@x.com? Their signup link will stop working and you can re-invite them later."**, `confirm_action_url="{% url 'hub_admin_invite_revoke' invite.pk %}"`, `confirm_button_text="Revoke invite"`. It POSTs full-page → redirect to `/manage/members/` with a Django message; the reload refreshes the list.
  - Accepted invites show **no buttons** (the person is already a member; they appear in the roster below).

**States:**
- **Empty:** when `for_management_panel()` is empty, `.pl-invite-list` renders a single muted line: **"No outstanding invites. Use **+ Invite a member** to send one."** — not a blank region.
- **Loading:** the acting button is disabled (`hx-disabled-elt`) and shows the htmx-indicator spinner during the swap; the rest of the list stays interactive.
- **Error:** send/resend failures return a friendly **error toast**, never a 500; revoke of an already-accepted invite is impossible from the UI (no button rendered) and `revoke()` raises `ValueError` as a backstop, surfaced as an error message.
- **Success:** toast on every mutation (`trigger_toast`), and the list visibly updates (new/removed/refreshed row).

**Boolean controls:** none in this feature. (If bulk invite is built later, any boolean — e.g. "skip already-invited" — uses `components/toggle.html`, never a raw checkbox.)

### 6.4 Status badge tokens (dark + light)

Reuse the `.hub-pill` family (`static/css/hub.css:372-382`); add two in-family modifiers (flagged: **new `.hub-pill--*` modifiers**, same naming/shape as the existing three — not a new prefix or token):

| Status | Pill class | Dark | Light contrast fix |
|---|---|---|---|
| Accepted (success) | `.hub-pill--ok` *(exists, shared)* | `rgba(52,211,153,0.15)` / `#6ee7b7` *(unchanged)* | **scoped to invite list only** — see note |
| Pending (neutral) | `.hub-pill--neutral` *(new)* | `rgba(139,151,168,0.18)` text `var(--hub-text-muted)` | `[data-theme="light"]` muted gray on light surface |
| Expired (muted/danger) | `.hub-pill--danger` *(new)* | `rgba(224,85,85,0.16)` text `#f0a0a0` | `[data-theme="light"]` darker red text |

**Do not add a global `[data-theme="light"] .hub-pill--ok` override** — `.hub-pill--ok` is shared with `templates/hub/user_settings.html`, which currently ships no light override; a global change there is an out-of-scope side effect on another page. If the accepted pill needs a light-theme contrast bump, **scope it to the invite list**: `[data-theme="light"] .pl-invite-list .hub-pill--ok { color: <darker green>; }`. The two **new** modifiers (`--neutral` / `--danger`) are invite-introduced, so their `[data-theme="light"]` rules are safe to define globally in the family (nothing else uses them yet) — but to be conservative the build may also scope them under `.pl-invite-list`. All values are translucent over the card and use existing text tokens / brand-adjacent reds; both themes get explicit treatment so a light text-on-translucent pill doesn't go low-contrast on the Slate theme. New CSS goes in **`hub.css`** (hub surface). **No hardcoded colors on form controls; no inline `background`/`color` anywhere.** Verify both themes via the theme toggle.

### 6.5 Mobile

- The flex `.pl-invite-row` uses `flex-wrap: wrap` with `gap` on the 8px grid; below ~640px the email/meta/pill stack and the action buttons drop to a full-width row — **no horizontal scroll, no fixed widths**.
- Resend/Revoke are real buttons (`hub-btn--sm`), comfortably tappable; the confirm modal is `pl-modal--sm` (already mobile-friendly).
- The send form is a single field + buttons — trivially one-handed.

### 6.6 New CSS (all in `static/css/hub.css`, `pl-`/`hub-` prefixed)

`.pl-invite-list`, `.pl-invite-row`, `.pl-invite-row__email`, `.pl-invite-row__meta`, `.pl-invite-row__actions` (`display:flex; gap:0.5rem;` — this is what spaces Resend/Revoke), `.pl-invite-empty`, plus `.hub-pill--neutral` / `.hub-pill--danger` (+ their `[data-theme="light"]` overrides) and, if needed, a **scoped** `[data-theme="light"] .pl-invite-list .hub-pill--ok` contrast rule (never a global `.hub-pill--ok` override — it's shared with `user_settings.html`). The obsolete `.members-invite input[type="email"]` inline-styled block in `members.html` is **deleted** (replaced by `form_field.html`).

---

## 7. Views & URLs (thin — logic stays in model/manager/form)

### URLs (`hub/urls.py`, alongside line 153-155)

```python
path("manage/members/invites/<int:pk>/resend/", views.admin_invite_resend, name="hub_admin_invite_resend"),
path("manage/members/invites/<int:pk>/revoke/", views.admin_invite_revoke, name="hub_admin_invite_revoke"),
# existing: manage/members/invite/  → admin_member_invite  (hub_admin_member_invite)
```

### Views (`hub/views.py`)

- **`admin_members`** *(edit `hub/views.py:2032`)* — add to context: `invites = Invite.objects.for_management_panel()` and `invite_form = InviteMemberForm()`. (Single extra query; manager `select_related`s the joins.)
- **`admin_member_invite`** *(rewrite `hub/views.py:2213`, stays `@fog_admin_required @require_POST`)* — HTMX-ify:
  - Validate `InviteMemberForm`. On valid → `Invite.create_and_send(email, invited_by=request.user)` inside `try/except ValueError`.
  - **Success:** re-render `_invites_panel.html` with refreshed `invites`; `trigger_toast(resp, f"Invite sent to {email}.", "success")`; return it (200, swaps `#invites-list`).
  - **Invalid / `ValueError`:** `resp = HttpResponse(status=204)`; `trigger_toast(resp, <first form error or str(exc)>, "error")`; return it (no swap, form stays open).
- **`admin_invite_resend`** *(new, `@fog_admin_required @require_POST`)* — `invite = get_object_or_404(Invite, pk=pk)`; if `not invite.is_pending` → 204 + error toast "That invite was already accepted."; else `invite.send_invite_email()`, re-render `_invites_panel.html`, toast **"Invite resent to {email}."**, return 200.
- **`admin_invite_revoke`** *(new, `@fog_admin_required @require_POST`)* — `invite = get_object_or_404(Invite, pk=pk)`; `try: invite.revoke(); messages.success(request, f"Revoked the invite for {email}.")` / `except ValueError as exc: messages.error(request, str(exc))`; `redirect("hub_admin_members")`. (Full-page POST from `confirm_modal`; the reload re-renders the list.)

All four views are pure HTTP glue — no business logic, no date math, no validation in the view body.

---

## 8. Notifications / emails / activity

- **Invite + resend emails:** unchanged plumbing — both go through `Invite.send_invite_email()` → the `member.invited` event (`core/models.py:375-381`). The **email's appearance is out of scope here** and owned by the sibling *Branded notification emails* spec; resend simply re-fires the event (each send carries a unique idempotency `period`, so the EventDelivery ledger never dedupes a deliberate re-invite — `core/models.py:357-363`).
- **Activity:** `create_and_send` already logs `SiteActivity.Kind.MEMBER_INVITED` (`core/models.py:347`). `revoke()` optionally logs `MEMBER_INVITE_REVOKED` (§3 / §5).
- **Acceptance (corrected):** the signup flow sets `accepted_at` via a bulk `update()` in the adapter's `pre_login` (`plfog/adapters.py:133-141`), which **does not** call `mark_accepted()`, so the `invite_accepted` event does **not** fire on acceptance. This feature does not change that and does not depend on it — it only reads `accepted_at`/`status`. (Wiring acceptance to emit `invite_accepted` would be a separate change, out of scope here.)

---

## 9. Optional stretch — bulk invite (clearly out of core scope)

> **Not part of the core build.** Spec'd here only so the core design doesn't accidentally preclude it.

If/when bulk invite is wanted: add a second toggle-revealed form ("Invite several people") with a `<textarea>` for pasted emails (one per line or comma/space separated) **wrapped in `.hub-form-group`** (theme-correct; never a bare textarea — FRONTEND rule 13). A new `BulkInviteForm` parses + validates each address, dedupes against existing members/pending invites, and returns clean/invalid buckets. A `Invite.objects.create_and_send_many(emails, invited_by)` manager method loops `create_and_send`, collecting per-email outcomes. The view returns the refreshed `_invites_panel.html` + a summary toast ("Sent 6 invites, skipped 2 already-invited."). CSV upload is a further extension (a file field + the same parser). Keep it behind its own toggle so the single-invite path stays one click.

---

## 10. Tests (BDD `*_spec.py`, `describe_*`/`it_*`, ≥98% gate, run in `plfog-web` Docker; `context_*` is NOT collected — use `describe_*` for nested blocks)

**Model + manager — `tests/core/models_spec.py`** (existing `describe_Invite`; builds invites with `Invite.objects.create(...)` as the file already does):
- `sent_at`: returns `last_sent_at` when set, else falls back to `created_at`.
- `is_expired`: false when accepted; false when un-accepted but last-sent newer than the window; true when un-accepted and last-sent older than `INVITE_EXPIRY_DAYS`. **Resend resets it:** an invite whose `created_at` is old but whose `last_sent_at` is recent reads **not expired** (the core display-honesty case — set `created_at` back, then set `last_sent_at=now`). Note both `created_at` and `last_sent_at` are written via `update()`/explicit assignment since `created_at` is `auto_now_add`.
- `send_invite_email` stamps `last_sent_at` (a re-send moves it forward), and `status` flips `EXPIRED → PENDING` after a resend on an aged invite.
- `status`: `ACCEPTED` when `accepted_at` set; `EXPIRED` past the cutoff; `PENDING` otherwise. `status_label` returns the matching `Status` label.
- `InviteManager.outstanding/pending/expired`: partition correctly around the cutoff **using the coalesced send time** (a resent-but-old invite lands in `pending()`, not `expired()`); all exclude accepted.
- `for_management_panel`: includes all un-accepted, includes accepted-within-30-days, excludes accepted-older-than-30-days.
- `revoke`: deletes the invite; deletes a bare INVITED placeholder with no user **and no `airtable_record_id`**; **does NOT delete a reused Airtable-imported stub** (`airtable_record_id` set — the invite goes, the imported Member stays); **does not** delete a member that has a linked user; raises `ValueError` on an already-accepted invite; logs `MEMBER_INVITE_REVOKED` (if that line is kept).

**Form — `tests/membership/forms_spec.py`** (already covers `InviteMemberForm`): confirm the three validation paths still pass (no change expected; included for the coverage gate).

**Views — `tests/hub/admin_views_spec.py`** (existing `describe_admin_members`; superuser via `_create_superuser`, plain member via `_create_member_user`):
- `admin_members` renders the Invites card and lists outstanding invites; shows the empty state with none.
- `admin_member_invite`: login required; forbids plain members; valid POST creates an invite + returns the partial + success toast header; duplicate/invalid POST returns 204 + error toast and creates nothing.
- `admin_invite_resend`: admin-only; pending → re-fires email (assert `send_invite_email` called / a fresh `EventDelivery`) and **`last_sent_at` moves forward**, returns partial + toast; already-accepted → 204 + error.
- `admin_invite_revoke`: admin-only; pending → invite gone + redirect + success message; accepted → error message, invite intact.

Gotchas:
- `create_and_send` needs a `MembershipPlan` to exist (`core/models.py:321-323`) — seed one in fixtures (mirror the existing invite tests).
- Expiry tests must control "now"/`created_at`/`last_sent_at` deterministically since `created_at` is `auto_now_add` (set them via `update()` or explicit assignment after create).
- **Do NOT** write a test asserting `invite_accepted` fires through the signup flow — it doesn't (acceptance is a bulk `update()` in the adapter, not `mark_accepted()`; §2/§8). Tests should assert on `accepted_at`/`status`, which are set either way.

---

## 11. Build order (each phase ships green: full suite + `ruff` + `mypy`)

1. **Model + manager + settings constant** — add the nullable `last_sent_at` field (auto-reversible `AddField` migration; `ruff format` + `git add` it together) and stamp it in `send_invite_email()`; `InviteManager` (Coalesce-based `pending`/`expired`), `sent_at`/`is_expired`/`status`/`status_label`, `Invite.Status`, `INVITE_EXPIRY_DAYS`; `revoke()` with the Airtable guard (+ the `MEMBER_INVITE_REVOKED` choices migration if logging). Tests for all of it.
2. **Views + URLs** — HTMX-ify `admin_member_invite`; add `admin_invite_resend` / `admin_invite_revoke`; extend `admin_members` context. View tests.
3. **Templates + CSS** — new Invites card + `_invites_panel.html`; swap the bare input for `form_field.html`; `.pl-invite-*` and `.hub-pill--neutral/--danger` (+ light overrides) in `hub.css`; delete the obsolete inline-styled input block. Manually verify on `pastlives.test:8000` in **both themes** and at mobile width.
4. **Housekeeping** — bump `plfog/version.py` `VERSION` + a **member-friendly `CHANGELOG`** entry (e.g. *"Admins can now see who's been invited, whether they've joined, and resend or cancel an invite — all from Manage Members."*). **Do this at build time, one bump per PR — not now.**

> Spec only — do not build until approved.

---

## 12. Out of scope

- **Email appearance / templating** — owned by the sibling *Branded notification emails* spec. This feature only re-fires the existing event.
- **Bulk / CSV invite** — optional stretch (§9), not core.
- **Hard-enforcing expiry** (blocking signup on an aged link) — "expired" stays advisory; the adapter is untouched.
- **A dedicated Invites page / nav item** — the card lives on `/manage/members/`.
- **Editing an invite** (changing the email) — revoke + re-invite covers it; no in-place edit.
- Any new color, prefix, or token beyond the two in-family `.hub-pill--*` modifiers (flagged above).

---

## Done checklist

- [ ] Single-invite form uses `form_field.html` (no inline control colors); visible **Send invite** button wired via HTMX; success + error toasts; closed-by-default toggle.
- [ ] Outstanding-invites card lists email + "sent N ago" (from `sent_at` = `last_sent_at or created_at`) + status pill (pending/accepted/expired) via `for_management_panel()`.
- [ ] `last_sent_at` field added (auto-reversible migration), stamped by `send_invite_email()`, so **Resend resets the badge/age** (no stale "Expired" after a resend); manager `pending`/`expired` partition on the coalesced send time.
- [ ] Per-row **Resend** (real `hub-btn--sm`, non-destructive, disabled-while-sending, toast on success) for un-accepted invites.
- [ ] Per-row **Revoke** (`hub-btn--sm hub-btn--danger` + `confirm_modal.html`; spacing via `.pl-invite-row__actions` flex `gap`, not inline margin) for un-accepted invites; deletes invite + a placeholder it created, but **never an Airtable-imported stub** (`airtable_record_id` guard).
- [ ] Empty / loading / error / success states all implemented.
- [ ] Status pills use theme tokens with explicit dark **and** light treatment; the accepted-pill light fix is **scoped to `.pl-invite-list`** so the shared `.hub-pill--ok` on `user_settings.html` is untouched; verified in both themes.
- [ ] Mobile: flex rows reflow, no horizontal scroll, real tap targets.
- [ ] All date/window logic in the manager; all validation in the form; views are thin.
- [ ] BDD specs (model, manager, form, views) green at ≥98% coverage; full type hints; run in `plfog-web`.
- [ ] `ruff format .` + `ruff check .` clean.
- [ ] Version bump + member-friendly changelog entry (at build time, one per PR).
