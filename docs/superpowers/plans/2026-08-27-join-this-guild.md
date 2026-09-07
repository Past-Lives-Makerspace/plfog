# Join This Guild (+ revived per-guild Welcome Email) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-27
**Surface:** FOG hub + public guilds surface (`guilds.pastlives.app`) — guild page (`templates/hub/guild_detail.html`); guild editor (`templates/hub/guild_edit.html`); Member Directory (`templates/hub/member_directory.html`); one transactional email.
**Related:**
- `docs/superpowers/plans/2026-08-26-guild-subscriptions.md` — the round (#237) that removed the old "Join This Guild" button and reframed a `GuildMembership` row as "following for updates."
- `docs/superpowers/plans/2026-08-27-guild-orientation-announcement-polish.md` — the round (#244) that dropped the per-guild welcome-email fields + templates.

---

## 1. Summary

A member browsing a guild page can join that guild in one click from a bold **Join This Guild** button in the page hero (and a sticky mini button that follows them down long pages), instead of hunting for a toggle buried in Settings. Joining is benefit framed, not a scary confirm: a short modal spells out what they get. The moment they join they land on the guild roster, pick up a guild badge on their Member Directory card, start receiving that guild's announcements, and get a warm, guild specific **welcome email** (with the guild's banner at the top and a "here's what your guild page can do" section) that guild leads can rewrite in their own voice. Once joined, the button quietly reads **Member**, with **Leave guild** tucked into an overflow menu.

This is a **revival**, not a green field feature. The join *mechanism* (`GuildMembership` + `Member.subscribe_to_guild`) is fully alive and already powers the first login picker, the Settings toggle, and the Discord `/join-guild` command. Two rounds ago the *button* was removed; one round ago the *welcome email* was stripped. We are re-attaching a front door and re-lighting the welcome email onto plumbing that already exists.

### Locked decisions (from brainstorm Q&A + senior-UX pass)

| Decision | Choice |
|---|---|
| New membership model? | **No.** "On the roster" and "subscribed to announcements" are already the *same* `GuildMembership` row (the `guild_members` resolver reads it directly). We reuse it verbatim; zero data migration for membership. |
| The one join path | `Member.subscribe_to_guild(guild)` (fat model). The hero button, sticky button, join modal, Settings toggle, first-login picker, and Discord `/join-guild` all funnel through it. No second subscribe path is created. |
| Where the welcome-email fields live | **Restore on `GuildOrientationSettings`**, mirroring the surviving `thankyou_email_*` trio field-for-field. That model is `get_or_create`'d for every guild in the editor already and is the established home for lead-authored guild emails. |
| Field naming | Name them `welcome_email_*` (not the old `join_email_*`) for parallelism with `thankyou_email_*`. Since the old columns are already dropped, this is a clean add, not a rename. |
| Default enabled? | **On by default** (`welcome_email_enabled` default `True`) with shared default copy, mirroring the thank-you email. The old field defaulted `False` (opt-in); the owner's new requirement is "fires automatically on join," so a lead does nothing and members still get a good welcome. |
| Welcome email editor location | A **new "Welcome Email" tab** on the guild editor, not buried under Orientations (where the thank-you email lives). The welcome email is join scoped, not orientation scoped; a guild that never runs orientations still wants to welcome new members. Reuses the existing `hub_guild_emails_save` multi-form endpoint. |
| Welcome email delivery class | **Transactional, no opt-out**, deduped once per (member, guild) forever. It is a direct response to an action the member just took (joining), like the orientation thank-you. |
| Fires on which join paths | **All of them** (hero button, Settings toggle, first-login picker, Discord `/join-guild`) uniformly, from inside `member_joined_guild`. The first-login picker can therefore send several at once; accepted because each is genuinely per-guild and useful (see §10 for the deferred batch option). |
| Member profile badge surface | There is **no standalone member profile page** in plfog. The Member Directory card *is* the member's public profile. Guild badges + the guild filter are re-added there. |
| Copy | Member-facing copy uses no em/standard dashes (arrow `->` allowed), Title Case headings, single-line `{# #}` comments only. |

---

## 2. What already exists (reuse, don't reinvent)

This is the heart of the plan: almost everything is assembly.

| Need | Existing thing | Location |
|---|---|---|
| Roster + subscription record | `GuildMembership` (member↔guild, `source` app/discord, unique) + `GuildMembershipManager.record_app_join` | `membership/models.py:4300-4367` |
| The single join path (records row, fires fan-out, self-heals Discord role) | `Member.subscribe_to_guild(guild) -> bool` | `membership/models.py:720-738` |
| The single leave path | `Member.unsubscribe_from_guild(guild)` | `membership/models.py:740-745` |
| Join fan-out hook (lead notice + activity today; **welcome email goes back here**) | `orientations.member_joined_guild(guild, member)` | `membership/orientations.py:944-962` |
| Announcement audience already reads membership | `resolvers.guild_members(context)` → `Member.objects.filter(guild_memberships__guild=…, status=ACTIVE)` | `core/events/resolvers.py:242-263` |
| Existing HTMX subscribe/unsubscribe endpoint (Settings toggle) | `hub.views.guild_membership_set` (`hub_guild_membership_set`) | `hub/views.py:2444-2472` |
| "My Guilds" grid data builder | `build_my_guilds_rows(member)` | `hub/guild_membership.py` |
| The email send helper the removed welcome used (renders `.txt`/`.html` shell, `email_to` bypasses prefs) | `emit_with_email_shell(...)` | `core/events/senders.py:75-149` |
| The exact "guild-lead-authored, per-guild, warm" email pattern to mirror | Revived orientation thank-you: fields, resolvers, form, templates, sender | fields `membership/models.py:8169-8207`; form `hub/forms.py:1585-1620`; templates `templates/membership/emails/orientation_thankyou.{html,txt}`; send in `orientations.complete_orientation` `membership/orientations.py:780-799` |
| Default-copy-with-lead-override pattern | `GuildOrientationSettings.resolved_thankyou_subject/body` + `membership/orientation_copy.py` | `membership/models.py:8195-8207`, `membership/orientation_copy.py` |
| Multi-form-per-endpoint save (via `form_id`) | `hub.views.guild_emails_save` (currently handles only `thankyou_email`) | `hub/views.py:3150-3182` |
| Guild banner image (+ crop/object-position) | `Guild.banner_image` ImageField; `HeroCropMixin.hero_object_position` | `membership/models.py:1635-1643`; `core/models.py:24-86` |
| Guild logo for badges | `Guild.logo_prefix` → `img/guild_logos/<prefix>_color.svg` (color) / `_bw.svg` | `membership/models.py:1808-1812` |
| Branded email shell | `templates/membership/emails/_base.html` (+ `_footer.html`, `_hero.html`) | `templates/membership/emails/` |
| Rich text: editor widget, sanitizer, email filters | `RichTextEditorWidget`, `sanitize_rich_html`, `rich_email_body` / `rich_email_text` | `hub/forms.py`, `membership/templatetags/rich_text.py` |
| Single email choke point + audit log | `core.email.send(...)` → `TransactionalEmailLog` | `core/email.py:61-73` |
| Absolute URL for member surface (email links + banner src) | `settings.MEMBER_BASE_URL`; `orientations._absolute_url()` | `plfog/settings.py:74`; `membership/orientations.py:43` |
| Back-to-top FAB pattern to mirror for the sticky mini-CTA | `.pl-scroll-top` (Alpine `show` past 400px) | `templates/hub/user_settings.html:695-703`; `static/css/hub.css:7116-7143` |
| Guild-page context flags | `is_member_of_guild`, `roster`, `member_count`, `guild.show_members`, `show_orientation`, `is_oriented` | `hub/views.py` `guild_detail` (~470-590) |
| Modal / confirm / toggle / form-field / toast components | `components/modal.html`, `confirm_modal.html`, `toggle.html`, `form_field.html`, `trigger_toast()` | `templates/components/`, `hub/toast.py` |
| Announcement composer email-preview partial (pattern for the editor preview) | `_compose_email_preview.html` | `templates/hub/partials/` |

**Genuine gaps to close (kept minimal):**

1. **Storage:** re-add four `welcome_email_*` fields to `GuildOrientationSettings` + two `resolved_welcome_*` properties (migration `0146`).
2. **Send:** re-attach the welcome email inside `member_joined_guild` (mirror the removed `emit_with_email_shell` call) + create `guild_welcome.{html,txt}` (now with a banner block) + a `guild_welcome_copy.py` default.
3. **Front door:** hero Join/Member CTA + sticky mini-CTA + benefit-framed join modal + thin join/leave views returning an HTMX partial.
4. **Editor:** a "Welcome Email" tab (form + preview + "Send test to me"), extending `guild_emails_save`.
5. **Directory:** re-add per-card guild badges + the guild filter dropdown.
6. **Housekeeping:** fix the stale `subscribe_to_guild` docstring ("welcome email when configured"); flip `guild_joined`'s `no_email` handling is *unchanged* (see §7 — the member welcome is a separate send, the lead "new follower" notice stays email-less).

---

## 3. Where the code lives

```
membership/
  models.py                 # + welcome_email_* fields & resolved_* props on GuildOrientationSettings; fix subscribe_to_guild docstring
  orientations.py           # member_joined_guild: re-add the welcome-email send (emit_with_email_shell)
  guild_welcome_copy.py     # NEW — STANDARD_WELCOME_BODY + standard_welcome_subject() (mirrors orientation_copy.py)
  migrations/
    0146_readd_guild_welcome_email.py   # NEW — AddField x4 (auto-reverse = RemoveField x4)
hub/
  forms.py                  # NEW GuildWelcomeEmailForm (mirror GuildThankyouEmailForm)
  views.py                  # NEW guild_join / guild_leave (thin, return _guild_join_cta.html); extend guild_emails_save + guild_welcome_test; add guild badges/filter to member_directory + _guild_edit_context
  urls.py                   # revive hub_guild_join / hub_guild_leave; add hub_guild_welcome_test
templates/
  hub/
    guild_detail.html       # hero CTA block + sticky mini-CTA + join modal; update Get Involved panel
    guild_edit.html         # new "Welcome Email" tab (mirror the Thank-you card)
    member_directory.html   # re-add guild badges on cards + guild filter <select>
    partials/
      _guild_join_cta.html      # NEW — hero CTA states (Join / Member+overflow), swapped by join/leave
      _guild_welcome_preview.html  # NEW — rendered welcome-email preview (modal body)
  membership/emails/
    guild_welcome.html      # NEW (banner block + greeting + body + "what you can do" + CTA)
    guild_welcome.txt       # NEW (text twin)
static/css/hub.css          # .pl-guild-join-fab (sticky), .pl-guild-cta, badge tweaks — tokens only
```

Home apps: `membership` (model + send + copy), `hub` (views/forms/templates). Everything stays inside the existing coverage/mypy scope (`source = ["plfog", "core", "membership"]` plus the hub tree already tested under `tests/hub/`).

---

## 4. Data model

**No new model.** `GuildMembership` is reused unchanged. The only schema change is restoring the welcome-email columns on `GuildOrientationSettings`.

### Re-added fields on `GuildOrientationSettings` (mirror `thankyou_email_*`)

| Field | Type | Note |
|---|---|---|
| `welcome_email_enabled` | `BooleanField(default=True)` | On by default; renders as a toggle. `help_text`: "Send a welcome email when a member joins this guild. On by default; leave the subject and body blank to send the standard welcome, or write your own." |
| `welcome_email_subject` | `CharField(max_length=200, blank=True, default="")` | Blank → standard subject. `help_text`: "Subject line of the welcome email." |
| `welcome_email_body` | `TextField(blank=True, default="")` | Blank → standard body. Rich text (sanitized). `help_text`: "Body of the welcome email (your personal note; line breaks preserved)." |
| `welcome_email_updated_at` | `DateTimeField(null=True, blank=True)` | Stamped on edit. `help_text`: "When the welcome email was last edited." |

All four carry `help_text` (house rule). No `TextChoices` needed (no choice fields here).

### Properties (mirror `resolved_thankyou_*`)

```python
@property
def welcome_email_subject_resolved(self) -> str:
    from membership.guild_welcome_copy import standard_welcome_subject
    return self.welcome_email_subject or standard_welcome_subject(self.guild.name)

@property
def welcome_email_body_resolved(self) -> str:
    from membership.guild_welcome_copy import STANDARD_WELCOME_BODY
    return self.welcome_email_body or STANDARD_WELCOME_BODY

@property
def welcome_email_ready(self) -> bool:
    """On + always has resolvable copy, so enabled alone is enough to send."""
    return self.welcome_email_enabled
```

### Default copy (`membership/guild_welcome_copy.py`, mirrors `orientation_copy.py`)

Single source of truth so the copy-review gallery and the send path read the same text. No dashes; arrow allowed.

```python
STANDARD_WELCOME_BODY = (
    "We're really glad you're here. Following a guild means you'll hear about what's happening, "
    "you'll show up on the guild roster, and you'll pick up the guild's Discord role automatically. "
    "Look around your guild page whenever you like, and come say hi in the channel. See you around the space!"
)

def standard_welcome_subject(guild_name: str) -> str:
    return f"Welcome to {guild_name}!"
```

The "here's what you can do on your guild page" list + the help-guide link are **static structure in the email template** (guild-agnostic, always correct), so the editable body stays the lead's short personal note. This mirrors the thank-you email, where the template supplies the greeting/eyebrow and `{{ body }}` is the lead's words.

### Migration

`0146_readd_guild_welcome_email.py` — four `migrations.AddField` on `guildorientationsettings` (immediately after the current head `0145_remove_guild_welcome_email`). Django auto-generates the reverse (`RemoveField` x4), so it runs backward cleanly — **no `RunPython`, so no hand-written reverse function is required** (the "migrations need reverse functions" rule applies to data migrations; this is a pure schema `AddField`, inherently reversible). Header comment must note: this restores *empty* columns; any copy a guild had before the `0145` drop is not recoverable (it was destroyed then), and leads re-author from the seeded default.

---

## 5. Business logic (fat models)

### Join (reused as-is)
`Member.subscribe_to_guild(guild)` already: `record_app_join` → on new/upgrade calls `member_joined_guild` → self-heals the Discord role → returns whether it was new/upgraded. **The hero button changes nothing here.** The thin view calls this method.

### Leave (reused as-is)
`Member.unsubscribe_from_guild(guild)` deletes the row + drops the Discord role.

### Welcome email — re-attach inside `member_joined_guild` (the one change)

Currently `member_joined_guild` is a bare `emit("guild_joined", …)` (lead-only in-app "New follower", `no_email=True`). Re-add a **second, member-facing** send beside it — do not overload the lead notice:

```python
def member_joined_guild(guild: Guild, member: Member) -> None:
    # (unchanged) lead-only in-app "New follower" + GUILD_JOINED activity
    emit("guild_joined", actor=member.user, target=guild, context={"guild": guild},
         title="New follower", body=f"{member.display_name} now follows {guild.name}.",
         url=reverse("hub_guild_detail", args=[guild.slug]),
         period=f"guild:{guild.pk}:join:{member.pk}")

    # (new) member-facing welcome email — transactional, once per (member, guild) ever
    settings_obj = GuildOrientationSettings.objects.filter(guild=guild).first()
    if settings_obj is None or not settings_obj.welcome_email_ready:
        return
    guild_url = _absolute_url(reverse("hub_guild_detail", args=[guild.slug]))
    banner_url = _absolute_url(guild.banner_image.url) if guild.banner_image else ""
    help_url = _absolute_url(reverse("hub_help_article", args=["getting-started", "your-guild-page"]))  # category+article slugs; see §7 note
    emit_with_email_shell(
        "guild_welcome",  # registered member-facing event (see §7); resolver finds nobody, explicit email_to carries it
        target=guild,
        context={"member": None},  # suppress in-app dup — email-only, like _emit_member_email's request-received path
        subject=settings_obj.welcome_email_subject_resolved,
        text_template="membership/emails/guild_welcome.txt",
        html_template="membership/emails/guild_welcome.html",
        template_context={
            "guild": guild, "greeting_name": member.display_name,
            "body": settings_obj.welcome_email_body_resolved,
            "guild_url": guild_url, "banner_url": banner_url, "help_url": help_url,
        },
        email_to=member.primary_email,
        email_trigger_kind="guild_welcome",  # TransactionalEmailLog audit label
        period=f"guild:{guild.pk}:welcome:{member.pk}",
    )
```

Guards / side effects:
- **Fires once per (member, guild) forever** via the `period` key — a leave-then-rejoin months later does not re-welcome (safe, non-spammy; matches how `guild_joined` keys its period).
- **Discord *reaction* mirror never triggers this** — `record_discord_join` (used by the reaction sync) does not call `member_joined_guild`, by existing design. Only explicit joins do. Kept.
- **No opt-out** (transactional). It rides the explicit `email_to` path, which bypasses preferences on purpose.
- Fix the stale docstring on `subscribe_to_guild` ("welcome email when configured, lead notice, activity row") so it once again matches reality.

### Editor validation lives in the form (not the view)
`GuildWelcomeEmailForm.clean_welcome_email_body` runs `sanitize_rich_html` (mirrors `clean_thankyou_email_body`). Because the email is on-by-default with default fallback copy, **no subject/body is required to enable it** (same as thank-you) — so there is no "you enabled it but left it blank" error to surface. `save()` stamps `welcome_email_updated_at` only when a welcome field actually changed.

---

## 6. UI / UX (completeness checklist applied per screen)

### 6.1 Guild page hero — Join / Member CTA  (`templates/hub/guild_detail.html`, hero at lines 54-105; partial `partials/_guild_join_cta.html`)

- **Layout & container:** a primary CTA in the hero `__content` block, directly under the guild name / "Led by" line, in the existing `display:flex; gap:0.5rem; flex-wrap:wrap` action row (currently editor-only buttons). It sits **before** the editor buttons for a logged-in non-member so Join is the first thing in the row. Rendered from `_guild_join_cta.html` so join/leave can swap just this fragment.
- **States (the CTA is a small state machine):**
  - **Not a member (linked member):** `<button class="pl-btn pl-btn--primary pl-guild-cta">Join This Guild</button>` that opens the join modal (`@click="$dispatch('open-modal','join-guild')"`). Bold, high contrast (brand gold on navy per tokens).
  - **Member:** a quiet `pl-btn pl-btn--secondary` reading **Member** with a check glyph, plus a **⋯ overflow** button (Alpine dropdown, `pl-guild-cta__overflow`) whose single item is **Leave guild** → opens the leave confirm modal. The overflow is a real button (44px tap target), theme-tokened popover using `--hub-elevated`.
  - **Not authenticated:** the hero shows no Join (the existing "Log in to the members hub" CTA already lives in Get Involved); on the public guilds surface the hero Join is hidden (joins happen on the members hub) — mirror the existing `is_guilds_surface` gate that already redirects shopping to the members hub.
  - **Unlinked account (`member is None`, authenticated):** no Join button (no membership to attach); Get Involved's existing branches already handle this.
- **Components used:** `modal.html` (join), `confirm_modal.html` (leave), `trigger_toast()`.
- **Feedback:** join POSTs to `hub_guild_join` (HTMX), which returns the re-rendered `_guild_join_cta.html` (now the Member state) swapped into the hero **plus** OOB swaps for (a) the sticky mini-CTA, (b) the Get Involved panel, (c) the member-count stat chip, (d) the roster card — and `trigger_toast("You joined {guild}! Check your inbox for a welcome.", "success")`. Leave mirrors it back to the Join state with an info toast.
- **Error state:** unlinked/expired session → toast "Your account is not linked to a membership." (mirrors `guild_membership_set`). A failed POST leaves the button as-is and toasts "Couldn't update — please try again."

### 6.2 Sticky mini-CTA  (`guild_detail.html`; CSS `.pl-guild-join-fab` in `hub.css`)

- **Pattern:** clone the back-to-top FAB (`.pl-scroll-top`): `position: fixed; bottom; right; z-index:90`, Alpine `x-data="{ show:false }" x-init="window.addEventListener('scroll', () => show = window.scrollY > 400, {passive:true})" x-show="show" x-cloak`. Reveals only after ~400px of scroll so it never covers the hero's own button.
- **Content mirrors the hero state:** not a member → a gold pill "Join {Guild}" opening the same `join-guild` modal; member → a quiet "Member" pill (no action, or opens the same overflow). Included in the OOB swap set so join/leave keeps it in sync.
- **Mobile:** offset so it clears the phone bottom-nav / safe-area (`env(safe-area-inset-bottom)`), same as `.pl-scroll-top`. Single tap target, thumb reachable.
- **Dark + light:** tokens only (`--hub-elevated`, `--color-tuscan-yellow`, `--hub-border`); no inline color.

### 6.3 Join modal — benefit framed  (`components/modal.html` with `modal_id="join-guild"`, `modal_size="md"`)

- **Title:** "Join {Guild}?" **Body:** an intro line ("Here's what joining {Guild} gets you.") then icon rows (each an inline SVG + text, `pl-benefit-row`):
  1. **You're added to the guild roster.**
  2. **A {Guild} badge appears on your profile** — the phrase "your profile" **links to the Member Directory** (`{% url 'hub_member_directory' %}?guild={{ guild.slug }}`).
  3. **You get this guild's updates and announcements** (email, in app, or Discord — your choice in Settings).
  4. **You can book orientations for this guild** — "orientations" **links to the Orientations tab** (`#guild-orientation`, the existing anchor the Get Involved button already scrolls to). Shown only when `show_orientation`.
  5. **You get the guild's Discord role automatically** (shown only when `guild.discord_role_ids`).
  6. Conditionally, real extras that exist: **See the guild's events, meetings, and calendar**; **Browse the gallery** (if `gallery_images`); **See the wishlist** (if `guild.wishlist or guild.donate_url`); **You're eligible to vote on guild funding** (member-wide, always true — include as a benefit).
- **Controls:** primary `pl-btn pl-btn--primary` **Join {Guild}** (HTMX POST → `hub_guild_join`, closes modal on success via the toast/OOB swap); secondary `pl-btn pl-btn--secondary` **Not now** (`@click="$dispatch('close-modal','join-guild')"`). Footnote in `hub-text-muted`: "You can leave any time."
- **States:** the modal is static content (no list editor, no loading fetch); the only in-flight state is the Join button's HTMX request — disable it with `hx-disabled-elt="this"` while posting. Success = modal closes + toast. Error = toast, modal stays open.
- **Not a destructive action** → plain modal, not `confirm_modal`. (Leave *is* destructive → `confirm_modal`, see 6.4.)

### 6.4 Leave guild — confirm  (`components/confirm_modal.html`, `confirm_id="leave-guild"`)

- Opened from the Member-state overflow. `confirm_title="Leave {Guild}?"`, `confirm_message="You'll stop getting this guild's updates, drop off its roster, and lose its Discord role. You can rejoin any time."`, `confirm_button_text="Leave guild"`, `confirm_button_style="danger"`. Posts to `hub_guild_leave` (HTMX) → returns `_guild_join_cta.html` in the Join state + the same OOB swaps + info toast. No typed-confirmation needed (low stakes, reversible).

### 6.5 Get Involved panel  (`guild_detail.html:290-338`)

- Replace the current follow-language copy. When **not** a member: drop the "Choose your guild updates in Settings" paragraph (the hero Join now owns that job) and keep the other actions (Teach a Class, Contact, Discord, Website). When a member: keep the quiet "You get this guild's updates" confirmation line; change "Manage in Settings" to a plain secondary link (updates channel prefs still live in Settings). The panel is part of the OOB swap set so it flips with join/leave. Keep the existing `data-help-key="guild.join-leave"`.

### 6.6 Member count chip + roster card  (`guild_detail.html:108-113`, `:355-367`)

Both are in the OOB swap set. On join, the "{n} members" chip increments and the joining member appears in the roster card (`guild.show_members` gated, `roster` from `Guild.roster_members()`), so the page reflects the join without a reload. Empty roster state (no members yet): the card is already conditionally hidden (`{% if … and roster %}`) — after the first join it appears.

### 6.7 Guild editor — new "Welcome Email" tab  (`templates/hub/guild_edit.html`; tabs at lines 14-24)

- **Layout & container:** a new tab button `Welcome Email` in the tab row, and an `x-show="section === 'welcome_email'"` pane. Inside, **one `hub-card`** holding the editor form — a near-verbatim clone of the Thank-you Email card (lines 385-397).
- **Form:** `GuildWelcomeEmailForm` (ModelForm on `GuildOrientationSettings`), own `<form method="post" action="{% url 'hub_guild_emails_save' guild.pk %}">` with `<input type="hidden" name="form_id" value="welcome_email">` (the house multi-form pattern; `guild_emails_save` gains a `welcome_email` branch).
- **Fields (all via `components/form_field.html`):**
  - `welcome_email_enabled` → auto-renders as a **toggle** (boolean rule).
  - `welcome_email_subject` → text input, hint "Leave blank to use: Welcome to {guild}!".
  - `welcome_email_body` → `RichTextEditorWidget` (wrapped in `.hub-form-group` so it inherits input tokens — Rule 13), hint "Your personal welcome note. Leave blank to use the standard welcome.".
- **Controls, named:**
  - **Save** — `pl-btn pl-btn--primary` labeled exactly **"Save"**, last element in the form, `margin-top:1rem` clear of the field above (Rules 18, 21). Full-page POST → on success `messages.success(request, "Welcome email saved.")` + redirect back to `?tab=welcome_email`; on invalid re-render the editor on this tab with form errors (mirror `guild_emails_save`'s thank-you branch).
  - **Preview** — a secondary button that HTMX-GETs `_guild_welcome_preview.html` (server-rendered welcome email using the *current saved* values + a sample member name) into a `modal.html` body (`modal_id="welcome-preview"`), reusing the announcement composer's preview approach (`_compose_email_preview.html`). Rendered, not guessed, so leads see the real banner + copy.
  - **Send test to me** — a secondary button (HTMX POST → `hub_guild_welcome_test`) that fires the actual welcome email to the editing lead's own `primary_email` and returns a toast "Test sent to {email}." (mirrors the classes welcome email's "Send a test to me", documented in `help_content.py`). Immediate-effect control, placed **above** the batch Save form so Save stays the last thing on the tab (Rule 21).
- **This tab is not a list editor** — no formset, so no "+ Add"/Delete controls apply. (The list-editor rule is N/A here; called out so the reviewer isn't looking for an Add button that shouldn't exist.)
- **States:** empty (fresh guild) → toggle on, subject/body blank, hints tell the lead the defaults that will be used; the Preview still renders (from defaults). Error → sanitizer never rejects (it cleans), so the realistic error path is only a server/validation re-render with field errors shown by `form_field.html`. Success → Django message + redirect (full-page form) / toast (test send, HTMX).
- **Dark + light:** tokens only; the rich-text body wrapped in `.hub-form-group`; the preview modal inherits component styling. Verify both themes.
- **Mobile:** single-column card, full-width controls (matches the thank-you card), Save reachable.

### 6.8 Member Directory — badges + guild filter  (`templates/hub/member_directory.html`; view `hub/views.py:267-330`)

- **Re-add the guild filter** to the existing `.pl-directory-filters` `<form method="get">` (beside the Skill select): a `<select name="guild" onchange="this.form.submit()">` with "All guilds" + one `<option value="{{ g.slug }}">{{ g.name }}</option>` per active guild, `selected` on the current `?guild=`. Style via `.pl-filter-control` (already token-scoped — Rule 13) and set `select option { background; color }` in the filter CSS if not already present.
- **View:** re-add `member_qs.filter(guild_memberships__guild__slug=guild_slug)` when `?guild=` is set (validate against active guilds; unknown slug → ignore, show all), and add `guilds` (active, name-ordered) + `selected_guild` to the context. Add `guild_memberships__guild` to `prefetch_related` (with an ordered `Prefetch` on `Member.joined_guilds`) so the per-card badges are N+1 free.
- **Re-add per-card badges:** below the identity block, render the member's joined guilds as chips (reuse `hub-badge`): each chip links to `{% url 'hub_member_directory' %}?guild={{ g.slug }}` and shows the guild logo (`img/guild_logos/<logo_prefix>_color.svg`, guarded on `logo_prefix`) + name. Respect directory privacy: badges show the same set the roster does (active membership); this does not leak hidden profiles because the directory queryset is already `directory_visible()` for non-admins.
- **Empty states:** a member in no guilds → no badge row (not an empty box). Filtered to a guild with zero visible members → the existing "{n} active members" count reads 0 and the grid is empty; add a one-line "No members match this filter." message (currently the grid just renders empty — close that dead end).
- **Dark + light + mobile:** chips wrap; filter row already reflows (existing `.pl-directory-filters`). Tokens only.

### 6.9 The welcome email itself  (`templates/membership/emails/guild_welcome.{html,txt}`)

Extends `membership/emails/_base.html`; mirrors `orientation_thankyou.html` with a banner added at the top. Structure:
1. **Banner** — `{% if banner_url %}<img src="{{ banner_url }}" width="100%" ... alt="{{ guild.name }}">{% endif %}` (absolute URL; if no banner, a simple branded header band — no broken image). Inline styles are expected in emails (Rule 15 exception).
2. **Greeting** — "Hi {{ greeting_name }},"
3. **Eyebrow** — "Welcome to {{ guild.name }}!" where **{{ guild.name }} is a link to `{{ guild_url }}`** (Rule 15 — subject noun is a link, never dead text).
4. **Body** — `{{ body|rich_email_body }}` (the lead's note or the default).
5. **"What You Can Do On Your Guild Page"** — a static styled list (Title Case heading): read announcements; book an orientation; see meetings and the calendar; meet the roster; check the wishlist. In `.txt`, prefix each with an arrow `-> ` (no dash bullets — honors the no-dash rule).
6. **Help link** — "New here? Read the quick guide to your guild page." linking `{{ help_url }}`.
7. **Primary CTA** — a "View Guild Page" button → `{{ guild_url }}`.
8. `_footer.html` (already carries the manage-prefs link via `core.email.send`).

`.txt` twin says the same thing, arrow bullets, absolute URLs. Subject and body share the project/Portland timezone context (no dates in this email, so no tz hazard, but keep the shell consistent).

---

## 7. Notifications / emails / activity

- **New event:** register `guild_welcome` in `core/events/registry.py` as a **member-facing transactional** email event. Because the send passes `context={"member": None}` and an explicit `email_to`, the resolver deliberately finds nobody (no in-app/push duplicate) and the explicit-email path delivers — exactly the `_emit_member_email` "request-received" trick already used for orientations. Add its member-facing copy row so the copy-review gallery can show it. It is **not** added to the member settings matrix as an opt-out (transactional, like the thank-you).
- **Unchanged:** `guild_joined` stays the **lead-only, email-less** "New follower" in-app notice (`no_email=True`). The member welcome is a *separate* send; we do not overload the lead notice (which is what made the old combined call confusing).
- **Activity:** the existing `GUILD_JOINED` `SiteActivity` row (from the `guild_joined` emit) already records the join for the guild pulse — no new activity kind.
- **`TransactionalEmailLog`:** every welcome send logs one row under `trigger_kind="guild_welcome"` via the `core.email.send` choke point (audit + deliverability).
- **Dedup / delivery:** `period=f"guild:{pk}:welcome:{member.pk}"` → once per (member, guild) ever, so the first-login picker's multiple joins each send once and never double-send.
- **Help article:** the welcome email links a new-member guide. The Help Center URL is `hub_help_article` and takes **two** slugs — `category_slug` + `article_slug` (`help/<category_slug>/<article_slug>/`). Recommend a short new article `your-guild-page` under an existing category such as `getting-started` (authored per `docs/HELP_AUTHORING.md`); until it exists, fall back to the existing `welcome-to-fog` article (under its own category). The email builds the URL with `_absolute_url(reverse("hub_help_article", args=[category_slug, article_slug]))` — absolute, never a bare path (Rule 15). Confirm the exact category slug against `membership/help_content.py` at build time.

---

## 8. Build order (phased; each phase ships green)

1. **Model + copy + migration.** Add the four `welcome_email_*` fields + `resolved`/`ready` properties to `GuildOrientationSettings`; add `membership/guild_welcome_copy.py`; generate `0146_readd_guild_welcome_email`. Run `manage.py check` (CI system checks catch index/constraint issues local pytest skips) + `manage.py makemigrations --check`. Ships green with model specs.
2. **Send path + email templates + event.** Re-attach the welcome send in `member_joined_guild`; register the `guild_welcome` event + copy; create `guild_welcome.{html,txt}`; fix the `subscribe_to_guild` docstring. Now every existing join path (Settings toggle, first-login picker, Discord `/join-guild`) already emails — provable by tests before any new UI exists.
3. **Front door (hero + sticky + modal + join/leave views).** `_guild_join_cta.html`; `guild_join`/`guild_leave` thin views + revived URL names returning the partial with OOB swaps; hero CTA, sticky FAB, join modal, leave confirm; Get Involved panel + count/roster OOB. CSS (tokens only, both themes).
4. **Editor tab.** `GuildWelcomeEmailForm`; extend `guild_emails_save` (`welcome_email` branch) + `guild_welcome_test` view/URL; the Welcome Email tab with Save + Preview + Send-test; `_guild_welcome_preview.html`.
5. **Directory.** Guild filter + per-card badges + view filter/prefetch + empty-state message.
6. **Housekeeping + release.** Help article (`your-guild-page`); run `ruff format . && ruff check --fix .`, `mypy` (via pre-push hook), full `pytest`, `manage.py check`; bump `plfog/version.py` VERSION + **one** member-friendly `CHANGELOG` entry stamped at the new VERSION (see §10 note on changelog collisions).

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, ≥ the repo's coverage gate (100% branch target), run in the `plfog-web` Docker image. Note the two-location convention: model/email specs under `tests/membership/`, view/template specs under `tests/hub/`.

- **Model (`GuildOrientationSettings`):** `welcome_email_body_resolved`/`subject_resolved` return the lead override when set and the standard copy when blank; `welcome_email_ready` tracks `enabled`; `save()` stamps `welcome_email_updated_at` only on a welcome-field change (not on an unrelated save).
- **Send (`member_joined_guild`):** joining via `subscribe_to_guild` (created) sends exactly one welcome email to `member.primary_email` with the resolved subject/body and the banner URL in context; an idempotent re-subscribe / leave-then-rejoin does **not** re-send (period dedup); a guild with `welcome_email_enabled=False` sends none; the lead "New follower" in-app still fires regardless; a Discord *reaction* join (`record_discord_join`) sends **no** welcome. Assert the `TransactionalEmailLog` row (`trigger_kind="guild_welcome"`). Use `respx` only if any HTTP is involved (none expected); never mock the DB/models.
- **First-login picker burst:** `answer_guild_updates_prompt([g1,g2,g3])` sends three welcomes (one per guild), each once.
- **Join/leave views:** `hub_guild_join` on a linked member creates the membership + returns `_guild_join_cta.html` in the Member state (+ toast trigger header); unlinked/anonymous → guarded (toast/redirect), no row; `hub_guild_leave` deletes + returns the Join state; public guilds surface hides the hero Join (gate honored).
- **Editor:** `GuildWelcomeEmailForm` sanitizes body HTML; `guild_emails_save` with `form_id="welcome_email"` saves + redirects to `?tab=welcome_email`; invalid re-renders on that tab; unknown `form_id` → 404; non-lead is forbidden (`_require_can_edit_guild`); `guild_welcome_test` sends to the lead's own email and toasts; preview renders without sending.
- **Directory:** `?guild=<slug>` filters to that guild's members; unknown slug → all; badges render per joined guild with the correct logo/link and are N+1 free (assert query count); hidden profiles never surface via a badge; zero-match filter shows the empty-state line.
- **Template lint:** run `tests/template_comment_lint_spec.py` (single-line `{# #}` only) after touching `guild_detail.html` / `guild_edit.html` / `member_directory.html`.
- **Copy-review:** the `guild_welcome` event copy renders cream-on-dark with gold links in the shell (Rule: copy-mode emails must be styled, not just wrapped) — but here we pass a real `.html` template, so verify the banner + linked guild name + CTA render in both a banner and no-banner guild.

## 10. Open / deferred

- **First-login welcome burst.** Firing one welcome per picked guild is accepted (each is genuinely per-guild). If members find 3+ at once noisy, a deferred option is to have `answer_guild_updates_prompt` suppress the per-guild welcome and send a single "welcome to your guilds" digest instead. Not built now (adds a second email path for a hypothetical complaint).
- **Help article `your-guild-page`.** Recommended but its authoring (screenshots per `HELP_AUTHORING.md`) is a small separate content task; the email falls back to `welcome-to-fog` until it lands.
- **Changelog collision guard.** The `CHANGELOG` text renders into every hub page's context; keep the new entry free of any literal marker strings that negative tests assert on (see the "changelog renders everywhere" gotcha). One curated entry per feature, stamped at the new VERSION.
- **Out of scope:** a standalone public member profile page (none exists; the Directory card is the profile); changing how announcement *channel* preferences work (still Settings → Notifications); any change to the Discord reaction sync or `discord_welcome_message` (that Discord-only field is separate from this email and stays as-is).
