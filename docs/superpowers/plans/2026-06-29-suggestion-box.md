# Virtual Suggestion Box — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-29
**Surface:** FOG hub (`pastlives.test`) — guild detail pages (`/guilds/<slug>/`) and the existing feedback page (`/feedback/`).
**Related:** reuses the beta-feedback form (`hub.beta_feedback`) and the guild-lead email fan-out used by class reviews (`classes/emails.py`).

---

## 1. Summary

Members can drop a suggestion to a guild's leadership, or to the makerspace as a whole, without hunting for an
email address. On any guild page there's a **"Suggestion Box"** button that opens a short modal; submitting it
emails everyone who runs that guild (lead + all staff). The org-wide box is the existing `/feedback/` page,
relabeled. By default the submitter's name and email ride along so leads can follow up — but a single
**"Submit anonymously"** toggle strips the contact info before it's sent.

It is **fire-and-forget**: a suggestion is an email, nothing more. No new model, no inbox, no in-app bell, no
Discord. This keeps the build to "wire up an email send" and reuses plumbing that already exists.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| How leadership receives suggestions | **Email only**, fire-and-forget (like `/feedback/`). No model, no inbox, no in-app, no Discord. |
| Org-level box — where & who | **Reuse `/feedback/`**: relabel it "Suggestion Box," add the anonymous toggle, keep its existing admin recipients. |
| Who can submit | **Logged-in members only** — the hub is `@login_required`; guild pages aren't public. |
| Anonymity model | A logged-in member opts to **withhold** their identity. Toggle **defaults OFF** → contact info **is** included (per the ask). |
| Guild recipients | `guild.leadership_members()` (lead + all staff), their `primary_email`. |
| No-lead fallback | If a guild has no lead/staff with an email, route to the org feedback recipients so it's never dropped. |
| Spam protection | A honeypot field on each form. (Rate-limiting deferred — see §10.) |

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Send a transactional email (audited) | `core.email.send(to=, subject=, trigger_kind=, text_body=, html_body=, best_effort=True)` | `core/email.py` |
| Resolve "who runs this guild" | `Guild.leadership_members()` → `list[Member]` (lead + staff, deduped) | `membership/models.py:1112` |
| Each lead's email | `member.primary_email` (alias-aware) | `membership/models.py` (Email Model section) |
| Exact "email every guild lead" loop | `for member in guild.leadership_members(): email = member.primary_email` | `classes/emails.py:52` |
| Wrap a flat body in the branded HTML shell | `_flat_text_email_html(text)` | `classes/emails.py:65` |
| Org-wide feedback form + email send | `BetaFeedbackForm` + `BetaFeedbackForm.send(user=)` → `core.email.send()` to `settings.BETA_FEEDBACK_EMAILS` | `hub/forms.py:350`, view `hub/views.py:1984` |
| Org recipients (admin inbox) | `settings.BETA_FEEDBACK_EMAILS` (env, default `josh@plaza.codes`) | `plfog/settings.py:418` |
| Modal + toast pattern | `components/modal.html`, `trigger_toast()` | FRONTEND.md, `hub/toast.py` |
| Guild detail "actions" card (where the button slots in) | overview aside, the stack of `pl-btn pl-btn--secondary` buttons | `templates/hub/guild_detail.html:236-261` |

**Gaps to close (small):** a `GuildSuggestionForm` + a thin POST view + URL for the guild box; a branded
email builder for the guild send; an `is_anonymous` toggle (+ honeypot) added to `BetaFeedbackForm`; the
relabel of the feedback page. No model, no migration.

## 3. Where the code lives

```
hub/forms.py        + GuildSuggestionForm (subject, message, is_anonymous, honeypot) + .send(guild, user)
                    ~ BetaFeedbackForm: add is_anonymous + honeypot; honor anonymity in .send()
hub/views.py        + guild_suggestion_form(request, slug)    (login_required, GET — renders the modal partial)
                    + guild_suggestion_create(request, slug)  (login_required, POST — validates + sends)
                    ~ beta_feedback: pass is_anonymous through (form already owns the logic)
hub/urls.py         + hub_guild_suggestion_form    -> /guilds/<slug>/suggest/        (GET)
                    + hub_guild_suggestion_create  -> /guilds/<slug>/suggest/send/   (POST)
templates/hub/
  guild_detail.html ~ add "Suggestion Box" button (open-modal + hx-get) + the empty modal shell in "Get Involved"
  partials/guild_suggestion_form.html   + the modal's form body — rendered on GET, re-rendered with errors on
                                          invalid POST. Receives { form, guild, member } in context.
  beta_feedback.html ~ relabel to "Suggestion Box"; render fields explicitly (no blind loop — see §6 Screen B)
templates/membership/emails/
  guild_suggestion.txt / .html   + branded email to the guild's leads (or reuse _flat_text_email_html)
```

No new app, no model, no migration — stays inside the existing `hub` + `membership` coverage scope. The
**GET form view + hx-get on the trigger** is load-bearing: `components/modal.html` exposes its body only as a
`{{ modal_body }}` variable, so the form must be HTMX-loaded into `#guild-suggestion-body`, not inlined. This
mirrors the shipped announcements editor exactly (`_guild_announcement_row.html:20-22` button +
`guild_announcement_edit` GET branch, `hub/views.py:1785-1790`).

## 4. Data model

**None.** This feature stores nothing. (If a persisted inbox is ever wanted, that's a separate spec — see §10.)

## 5. Business logic (forms own it; views stay thin)

### `GuildSuggestionForm(forms.Form)` — `hub/forms.py`

| Field | Type | Note |
|---|---|---|
| `subject` | `CharField(max_length=200, required=False)` | "What's this about? (optional)". Blank → the email subject falls back to `"Suggestion"`. |
| `message` | `CharField(widget=Textarea(rows=6))` | The suggestion body. **Required — this is the one validation error a user can hit.** |
| `is_anonymous` | `BooleanField(required=False, initial=False)` | Renders as a toggle. OFF = contact info included (default). |
| `website` (honeypot) | `CharField(required=False)` | Bot trap; rendered off-screen, `aria-hidden`, not in the tab order; if non-empty, treat as spam. |

- `clean()` — if the honeypot `website` is filled, set a private `self._is_spam = True` (no visible error — the
  view returns the success toast without sending, so bots get no signal). `message` required → its default
  "This field is required." is fine, or override to "Add a few words so the leads know what you're suggesting."
  `subject` is optional, so it can never error — the error model has exactly one path (empty message).
- `send(self, *, guild, user) -> None` — **the fat method.** Resolves recipients and sends one email,
  `best_effort=True` (a mail failure must not 500 the modal):
  - `recipients = [m.primary_email for m in guild.leadership_members() if m.primary_email]`
  - if `not recipients:` fall back to `list(settings.BETA_FEEDBACK_EMAILS)` (never drop a suggestion).
  - Subject: `f"[Suggestion · {guild.name}] {subject}"`.
  - Identity line: `is_anonymous` → `"Submitted anonymously."`; else
    `f"From: {user.get_full_name() or member.primary_email} ({member.primary_email})"`.
  - Body (text + branded HTML): the identity line, the guild name (linked to its page in HTML), the message,
    and — when **not** anonymous — a "Reply to {name}" `mailto:` so a lead can respond in one click.
  - `trigger_kind="hub.guild_suggestion"` (audited in `TransactionalEmailLog`).

### `BetaFeedbackForm` change — `hub/forms.py`

- Add the same `is_anonymous` toggle (default OFF) and honeypot.
- `send()` honors `is_anonymous`: when ON, the body's `From:` line becomes `Submitted anonymously.` instead of
  `From: {name} ({email})`. Everything else (category, `BETA_FEEDBACK_EMAILS` recipients, `best_effort`) is unchanged.

### Views (thin)

- `guild_suggestion_form(request, slug)` — `@login_required`, GET. Look up the guild by slug (404 if missing).
  Render `partials/guild_suggestion_form.html` with `{ form: GuildSuggestionForm(), guild, member: _get_member(request) }`.
  This is what the modal's `hx-get` loads on open (without it the modal opens empty — the blocker the review
  caught). Precedent: the GET branch of `guild_announcement_edit`, `hub/views.py:1785-1790`.
- `guild_suggestion_create(request, slug)` — `@login_required`, `@require_http_methods(["POST"])`. Guild by slug
  (404 if missing). Bind `GuildSuggestionForm`. **Invalid** → re-render `partials/guild_suggestion_form.html`
  with errors into `#guild-suggestion-body` (HTMX swap, 200). **Valid (or spam)** → on real submit
  `form.send(guild=guild, user=request.user)` (spam → skip the send); return `HttpResponse(status=204)` and:
  - `trigger_toast(resp, "Sent to the guild's leads — thanks!", "success")` — this sets `resp["HX-Trigger"]`.
  - **Close via a *different* header** so it doesn't clobber the toast:
    `resp["HX-Trigger-After-Settle"] = json.dumps({"close-modal": "guild-suggestion"})`. (`trigger_toast` already
    owns `HX-Trigger`; a second `HX-Trigger` would overwrite the toast. The shipped announcements editor uses
    exactly this split — `hub/views.py:1772-1776`. The modal's `@close-modal.window` string-compares the bare id,
    which the After-Settle header delivers.)
- `beta_feedback` — unchanged flow; the form now carries anonymity + a honeypot, so no view logic changes beyond
  passing the bound form (it already does). The honeypot/anonymity handling lives in the form.

## 6. UI / UX  ← completeness checklist applied per screen

### Screen A — Guild suggestion modal (`/guilds/<slug>/`, overview tab)

- **Entry point:** a new button **inside the "Get Involved" card's flex column** (`guild_detail.html:230-260`),
  directly below "Email Guild Lead / Contact the Guild." Match the siblings exactly: `pl-btn pl-btn--secondary`
  with inline `style="width:100%;"` (the card's buttons use inline width, not a class) and a small lightbulb/inbox
  SVG. Label: **"Suggestion Box."** Dropping it inside that flex `<div>` (which has `gap:0.5rem`) gives it the
  spacing above for free. The button does **two** things on click:
  ```html
  <button type="button" class="pl-btn pl-btn--secondary" style="width:100%;"
          @click="$dispatch('open-modal', 'guild-suggestion')"
          hx-get="{% url 'hub_guild_suggestion_form' guild.slug %}"
          hx-target="#guild-suggestion-body" hx-swap="innerHTML">…Suggestion Box</button>
  ```
  This is additive — the existing `mailto:` "Email Guild Lead" link stays; the box is the better path because it
  reaches **all** leads (not just the single FK lead), works when there's no named lead, and supports anonymity.
- **Container:** an empty `components/modal.html` shell placed once in `guild_detail.html`
  (`modal_id="guild-suggestion"`, `modal_title="Suggestion for {{ guild.name }}"`, `modal_size="md"`); its body
  `#guild-suggestion-body` is filled by the `hx-get` above. 3 fields → modal + toast per the FRONTEND.md
  interaction table. **Do not** try to inline the form via `modal_body=` — the modal only takes a flat
  `{{ modal_body }}` string and can't carry `{% csrf_token %}`/`{% url %}`/includes; HTMX-load it.
- **Components (in `partials/guild_suggestion_form.html`):** `components/form_field.html` for `subject`/`message`;
  `is_anonymous` auto-renders as a **toggle switch** (FRONTEND.md Rule 3). The form fields land in `form_field.html`'s
  own `.pl-form-group` wrapper, which is globally theme-correct (`components.css:448` uses `--hub-input-bg`/`--hub-text`
  with a light-mode override — no `--surface` white-box risk), so no extra `.hub-form` wrapper is needed. The
  honeypot is rendered manually (not through the field loop) inside an off-screen wrapper — a new `pl-honeypot`
  class (off-screen position, **not** `.sr-only`, which keeps the field in the tab/a11y tree and would trap real
  keyboard/screen-reader users), with `aria-hidden="true"`, `tabindex="-1"`, `autocomplete="off"` on the input.
- **The controls, named** (all inside `partials/guild_suggestion_form.html`):
  - The partial wraps everything in `<form hx-post="{% url 'hub_guild_suggestion_create' guild.slug %}"
    hx-target="#guild-suggestion-body" hx-swap="innerHTML" hx-disabled-elt="find button[type=submit]">`.
  - **Submit:** a `pl-btn pl-btn--primary` "Send Suggestion" button at the bottom. On success: 204 → success
    toast ("Sent to the guild's leads — thanks!") + modal closes (via the two-header split in §5). On invalid:
    the partial re-renders in place (`hx-target` is the body itself) with inline field errors.
  - **Cancel:** a `pl-btn pl-btn--secondary` "Cancel" `@click="$dispatch('close-modal','guild-suggestion')"`.
  - Submit + Cancel sit in a wrapping flex row (`pl-modal-actions` or inline `display:flex;gap:0.5rem;flex-wrap:wrap`)
    so they sit side-by-side on desktop and stack cleanly on narrow screens. No list/formset → no Add/Delete.
- **Contact-info clarity:** a muted line above the toggle — *"Your name and email{% if member %}
  ({{ member.primary_email }}){% endif %} go to the guild's leads so they can follow up."* (guarded —
  `member` can be None for an unlinked account; the GET view passes `member` into the partial) — and the toggle's
  description: *"Submit anonymously — hide my name and email from the leads."* Makes the default ("contact info
  included") explicit and the opt-out one tap.
- **States:**
  - *Empty:* n/a (single-shot form, not a list). The modal body is never blank — the `hx-get` fills it on open.
  - *Loading:* `hx-disabled-elt` disables the submit button while the POST is in flight (HTMX's actual
    disable mechanism — `hx-indicator` only toggles a spinner's visibility, it does **not** disable or relabel
    the button; precedent `members.html:53`, `confirm_modal.html:44`). A "Sending…" relabel is optional and would
    need a `.htmx-request` CSS swap; plain disable is sufficient.
  - *Error:* empty message → inline error under the field (the partial re-renders); mail backend failure is
    swallowed (`best_effort`) so the user still sees success (their suggestion was accepted) — leads-side delivery
    is logged in `TransactionalEmailLog`.
  - *Success:* green toast, modal closes; the page is otherwise unchanged (nothing to list).
  - *No dead end:* Cancel and the modal's X both close it; success tells the user it worked.
- **Dark + light:** no inline `background`/`color` on any control. The `<textarea>`/`<input>` sit inside
  `form_field.html`'s `.pl-form-group` scope so they inherit `--hub-input-*` tokens in both themes (verified
  `components.css:448`/`:940`). Toggle, honeypot wrapper, and buttons use `pl-` classes only. Verify both themes.
- **Mobile:** modal is `md` and single-column; fields stack; "Send Suggestion"/"Cancel" are real tap targets in
  a wrapping flex row; no horizontal scroll. 8px-grid spacing between fields and the toggle.

### Screen B — Org-wide Suggestion Box (`/feedback/`, relabeled)

- **Container:** the existing inline `.hub-form` on a dedicated page (4 fields now: category, subject, message,
  anonymous toggle → dedicated page is correct per the interaction table).
- **Break the blind loop.** Today `beta_feedback.html:13-15` renders `{% for field in form %}{% include
  "components/form_field.html" %}{% endfor %}`. Adding a `website` honeypot to the form would render it as a
  **visible labeled text input** through that loop (the opposite of a honeypot), and there's nowhere to inject the
  clarity line above the toggle. So render the fields **explicitly**: `category`, `subject`, `message`, then the
  muted clarity line, then `is_anonymous` — and render the honeypot manually inside the off-screen `pl-honeypot`
  wrapper (`aria-hidden`, `tabindex=-1`), outside the visible field list. (Each visible field still goes through
  `components/form_field.html`; only the loop is unrolled.)
- **Copy relabel:** page `<h1>` → **"Suggestion Box"**; intro → *"Have an idea for the makerspace, or something
  that isn't working? Send it to the team."* Category labels stay (Bug / Feature / General) but the page framing
  is suggestions, not "beta."
- **New control:** `is_anonymous` toggle (auto-rendered switch via `form_field.html`), description *"Submit
  anonymously — hide my name and email from the team."* Default OFF. Muted line directly above it: *"Your name and
  email are included by default so we can follow up."*
- **Submit:** the existing "Send Feedback" button → relabel **"Send Suggestion."** Full-page POST → Django
  `messages.success` ("Thanks — we'll take a look.") + redirect back (existing flow).
- **States:** validation errors render inline via `form_field.html`; success is a Django message; honeypot spam
  silently succeeds without sending. Dark/light already correct (existing page); the new toggle inherits the
  form's field scope. Mobile already reflows.

## 7. Notifications / emails / activity

Only emails — no in-app, push, Discord, or `SiteActivity`. Both sends go through `core.email.send()` directly
(audited in `TransactionalEmailLog`), **not** the `emit()` spine — so the rubric's "unique `period`" dedup rule
doesn't apply here; this is a deliberate one-shot transactional send, the same path `BetaFeedbackForm` already uses.

**Guild suggestion email** (`templates/membership/emails/guild_suggestion.{txt,html}`, or build via
`_flat_text_email_html()`), per the FRONTEND.md email rules:

- **Subject noun linked:** the **guild name** is a link to its detail page (absolute URL via the project base),
  not dead text.
- **One clear action:** when contact info is included, a **"Reply to {name}"** `mailto:` is the primary CTA so a
  lead can answer in one click; when anonymous, no reply path (and the email says so plainly).
- **Surfaces the human content:** the member's actual message is the body, not a bare "you have a suggestion."
- **Branded shell, no BETA;** `.txt` and `.html` kept in sync; subject/body in project (Portland) time;
  `best_effort=True` so a delivery failure never 500s the submitter.
- Recipients: `guild.leadership_members()` → `primary_email`; fallback `BETA_FEEDBACK_EMAILS`. `trigger_kind="hub.guild_suggestion"`.

**Org suggestion email:** the existing `BetaFeedbackForm.send()` text email, unchanged except the `From:` line
becomes "Submitted anonymously." when the toggle is on. Recipients unchanged (`BETA_FEEDBACK_EMAILS`).

## 8. Build order (each phase ships green)

1. **Forms + sends (no UI).** Add `GuildSuggestionForm` with `.send()`; add `is_anonymous` + honeypot to
   `BetaFeedbackForm` and honor it. Unit-spec both `.send()` paths (recipients, anonymity, fallback, honeypot).
2. **Guild views + URLs + modal.** Both views (`guild_suggestion_form` GET, `guild_suggestion_create` POST) and
   URLs; the `guild_detail.html` "Get Involved" button (open-modal + hx-get) + the empty modal shell +
   `partials/guild_suggestion_form.html`; the `pl-honeypot` CSS class. View + template specs (assert the modal
   loads the form on GET, the After-Settle close header, the inline-error re-render).
3. **Relabel `/feedback/`.** Template copy + the new toggle; spec the anonymity-in-body assertion.
4. **Email templates.** `guild_suggestion.{txt,html}` branded; assert guild-name link, reply CTA (present when
   named, absent when anonymous), absolute URL, txt/html parity.
5. **Version + changelog.** Bump `plfog/version.py` VERSION; one member-friendly CHANGELOG entry, e.g.
   *"Suggestion box — send ideas straight to your guild's leads (or the whole makerspace), anonymously if you
   like."* (Done at build time, one entry, per the changelog rule.)

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, `respx` for any HTTP, run in the `plfog-web` Docker image,
≥98% coverage gate. Cases:

- **`GuildSuggestionForm.send`** (mock `core.email.send`, never the DB): emails every `leadership_members()`
  `primary_email`; **anonymous=True** omits the From line and the reply CTA; **anonymous=False** includes both;
  **no leads** falls back to `BETA_FEEDBACK_EMAILS`; honeypot filled → no send; `best_effort=True` passed.
- **`BetaFeedbackForm.send`**: anonymous toggle swaps the From line; non-anonymous unchanged; honeypot → no send.
- **`guild_suggestion_form` view (GET):** `@login_required`; renders the partial with an unbound form + `guild` +
  `member`; the rendered partial contains the `<form>`, a "Send Suggestion" submit, and the contact-info line
  (guarded when `member` is None).
- **`guild_suggestion_create` view (POST):** `@login_required` (anonymous user redirected); GET not allowed;
  valid POST → 204 with **both** the toast header (`HX-Trigger`) and the close header (`HX-Trigger-After-Settle`
  = `close-modal: guild-suggestion`), `form.send` called once; invalid POST (empty message) → 200 re-render of
  the partial with the field error and **no** send; honeypot filled → 204 success path with `form.send` **not**
  called; unknown slug → 404.
- **Templates:** guild_detail renders the "Suggestion Box" button (with both `open-modal` and `hx-get`) + the
  empty modal shell; the loaded partial has a visible "Send Suggestion" submit and a Cancel; the honeypot is
  off-screen (`pl-honeypot`, `aria-hidden`, `tabindex=-1`), not a visible field; `/feedback/` renders the
  relabeled title, the anonymous toggle, and **no** visible "Website" field (honeypot stays hidden).
- **Email:** guild email links the guild name (absolute), includes the reply `mailto:` only when not anonymous,
  `.txt`/`.html` agree.

## 10. Open / deferred

- **No persistence / inbox / in-app / Discord.** Chosen explicitly — suggestions are email-only. A stored
  `GuildSuggestion` model with a lead-facing inbox tab on `guild_edit.html` is a clean future upgrade if leads
  later want to track/triage; it would slot beside the other guild-edit tabs. Out of scope here.
- **Rate-limiting.** `core/abuse_limits.py` exists but is a *global* throttle (no per-member key), too coarse
  for this. v1 leans on login-gating + honeypot; a per-member/day cap can be added later if abuse appears.
- **Categories / status-to-submitter / lead reply threads / public "you said, we did" board.** All deferred —
  none are needed for "send the leads a suggestion."
- **Org recipients.** Kept as the existing `BETA_FEEDBACK_EMAILS` inbox (reusing the form). If the org box should
  instead fan out to every admin Member, swap the recipient resolution in `BetaFeedbackForm.send()` — a one-line
  change, noted but not assumed.
```
