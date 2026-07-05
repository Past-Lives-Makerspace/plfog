# Member Onboarding — `is_onboarded` + a "Get started" checklist — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-04
**Base:** release-0.20.x (anchor by symbol name). Sits on top of the just-landed My Guilds tab (v0.20.3).
**Surface:** FOG hub `pastlives.test` — the member **home page** (`/`), plus a tiny dismiss endpoint. Member-facing.
**Related:** the My Guilds tab (`2026-07-03-official-guild-membership.md`, shipped v0.20.3); the first-login welcome modal (C2) and the profile-completeness nudge (Batch-2 C1).

---

## 1. Summary

A brand-new member signs in and, today, sees a single "Finish setting up your profile" nudge — but nothing tells them the *other* first-week things: officially **join their guilds** (so they get guild emails and show on the roster) and **set a voting preference**. This feature introduces a member-hub **onboarding state** — a computed `Member.is_onboarded` — and a friendly, **dismissible "Get started" checklist card** on the home page that walks a new member through the three steps: **set up your profile → join your guilds → set a voting preference**. Each row links straight to the page that does it (profile editor, the My Guilds tab, the voting page) and auto-checks as it's done. The card disappears once they're onboarded, and they can dismiss it anytime — it **never blocks** anything. This is distinct from the existing *book/classes* onboarding wizard (`UserProfile.onboarding_completed_at` — "how'd you hear about us," class interests); this is the **guild-hub** onboarding.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Representation | **Computed `is_onboarded` property + a sticky `onboarding_dismissed_at`.** No stored "onboarded" boolean — the property is derived from real state so it can't drift; the timestamp only records "hide the card." |
| What counts as onboarded | **Profile complete AND joined ≥1 guild.** Voting is a **recommended, optional** step shown in the checklist but **not** required for `is_onboarded`. |
| Experience | **A dismissible "Get started" checklist card on the home page** that composes the existing pages (profile editor, My Guilds tab, voting). The first-login welcome modal stays as the first-touch intro and points at the card. No wizard. |
| Gating | **Never blocks.** Persistent-but-dismissible; members use the whole app from day one. |
| Relationship to the profile nudge | The checklist card **replaces** the standalone "Finish setting up your profile" home nudge — profile becomes **step 1 of 3** inside the card (no two overlapping cards). **Accepted behavior change:** today's nudge is *un-dismissable* while the profile is incomplete (`home.html:13`); folding it into a dismissible card means one ✕ hides profile guidance even mid-completion. Intended (nudge-only, the member's choice) — the profile editor still shows its own completeness and the dismiss toast points back. |

## 2. What already exists (reuse, don't reinvent)

All anchors verified on the current tree.

| Need | Existing thing | Location |
|---|---|---|
| **Profile step** — is the profile done, and what's missing | `Member.profile_completeness` → `ProfileCompleteness(missing, complete, percent)` (the "40% · Profile photo, Pronouns, Discord" data) | `membership/models.py:460` (dataclass `:207`) |
| Where the profile nudge is built into the home context | `build_home_context` → `"profile": member.profile_completeness` | `hub/home.py:65`–`:72` |
| **Guilds step** — has the member joined any guild | `Member.joined_guilds` (+ `GuildMembership`); the My Guilds tab does the joining | `membership/models.py` (`joined_guilds`); tab at `/settings/?tab=guilds` (`hub_user_settings`) |
| **Voting step** — has the member set a voting preference | `VotePreference` (+ `VotePreferenceForm`, `hub_guild_voting` view/page) | `membership/models.py:2539`; `hub/forms.py:569`; `hub/views.py:112` (`hub_guild_voting`) |
| **First-touch intro** — the first-login welcome modal + its dismiss | `Member.welcome_dismissed_at`, `dismiss_welcome()`, `has_started_profile`; `show_welcome_modal` gate | `membership/models.py:395`,`:484`,`:453`; `hub/views.py:64`–`:73` |
| The dismiss-and-persist pattern to mirror | `dismiss_welcome()` sets a timestamp via `save(update_fields=[…])` | `membership/models.py:484` |
| Home page context + template | `hub/home.py::build_home_context`; `templates/hub/home.html` (the profile nudge card renders here) | `hub/home.py`, `templates/hub/home.html` |
| Card shell + muted text + buttons | `hub-card`, `hub-text-muted`, `pl-btn`/`hub-btn` families | `static/css/hub.css` |
| HTMX toast + dismiss endpoint conventions | `trigger_toast()`; `@login_required`+`@require_POST` thin endpoints | `hub/toast.py`, `hub/views.py` |

**Genuine gaps to close (small):**
1. **One field** — `Member.onboarding_dismissed_at` (+ migration).
2. **Three fat-model reads** — `Member.is_onboarded` (bool), `Member.onboarding` (an `OnboardingChecklist` for the card), `Member.show_onboarding` (bool: not onboarded and not dismissed), and a `dismiss_onboarding()` method.
3. **One thin endpoint** — `onboarding_dismiss` (204 + toast).
4. **The card partial** on the home page, **replacing** the standalone profile nudge.

## 3. Where the code lives

```
membership/
  models.py                 # + onboarding_dismissed_at field; OnboardingChecklist dataclass;
                            #   is_onboarded / onboarding / show_onboarding properties; dismiss_onboarding()
  migrations/00XX_….py      # AddField(onboarding_dismissed_at) — reversible (auto RemoveField)
hub/
  home.py                   # build_home_context: swap the bare `profile` nudge for `onboarding`
                            #   (the checklist), keep passing the pieces the rest of home needs
  views.py                  # + onboarding_dismiss (thin: member.dismiss_onboarding(); 204 + toast)
  urls.py                   # + path settings/onboarding/dismiss/ name="hub_onboarding_dismiss"
templates/hub/
  home.html                 # replace the "Finish setting up your profile" block with the checklist include
  partials/_onboarding_checklist.html   # NEW — the "Get started" card
static/css/hub.css          # + .pl-onboarding-* (row layout, check/step states) — theme tokens only
plfog/version.py            # VERSION bump + member-friendly CHANGELOG entry (at build time)
```

Home app: `membership` (the state + logic), `hub` (context, endpoint, template). No new app; inside the existing coverage/mypy scope.

## 4. Data model

**One additive field on `Member`. No new model.**

| Field | Type | Note |
|---|---|---|
| `onboarding_dismissed_at` | `DateTimeField(null=True, blank=True, help_text="When the member dismissed the home 'Get started' checklist card; null = never dismissed. Does NOT affect is_onboarded — only hides the card.")` | Sticky-dismiss only. |

- **Migration:** one `AddField`; reverse is the auto-generated `RemoveField` (a real reverse — no `RunPython`). No data migration (null default is correct: nobody has dismissed yet).

### `OnboardingChecklist` (in-memory dataclass, mirrors `ProfileCompleteness`)

```python
@dataclass(frozen=True)
class OnboardingStep:
    key: str            # "profile" | "guilds" | "voting"
    label: str          # "Set up your profile"
    done: bool
    url: str            # where the row links to (relative; reverse()d)
    optional: bool      # True for voting
    hint: str           # e.g. "40% complete" for profile, "" otherwise

@dataclass(frozen=True)
class OnboardingChecklist:
    steps: list[OnboardingStep]
    required_done: int      # of the non-optional steps
    required_total: int     # = 2 (profile, guilds)
    complete: bool          # required_done == required_total  (== Member.is_onboarded)
```

## 5. Business logic (fat model — `Member`)

Views stay thin; all of this is on the model, fully typed, no side effects except `dismiss_onboarding()`.

- **`is_onboarded` (property → bool):** `self._profile_essentials_done and self._has_joined_guild`. **Voting is not included** (locked). **The profile gate is `_profile_essentials_done`, NOT `profile_completeness.complete`** — the latter's five signals include the **`show_in_directory` opt-out** (`membership/models.py:476`), so a member who legitimately hides from the directory could *never* be onboarded (card stuck at "80% · 1 of 2 done" forever, only exit the ✕). `_profile_essentials_done` checks only the **content** essentials (photo / pronouns / about-you / Discord), excluding the directory-listing *preference*.
- **`onboarding` (property → OnboardingChecklist):** builds the three steps:
  - **profile** — `done = _profile_essentials_done` (excludes the directory opt-out, per above); `url = reverse("hub_user_settings") + "?tab=profile"` — the **exact** link the current nudge uses (`home.html:33`); there is **no** `hub_profile_edit` route (`reverse` on it would `NoReverseMatch`); `hint = f"{profile_completeness.percent}% complete"` when not done.
  - **guilds** — `done = self._has_joined_guild`; `url = reverse("hub_user_settings") + "?tab=guilds"` (the My Guilds tab); `optional=False`.
  - **voting** — `done = self._has_voting_preference`; `url = reverse("hub_guild_voting")`; `optional=True`.
  - `required_done` counts profile+guilds; `complete = is_onboarded`.
- **`show_onboarding` (property → bool):** `self.onboarding_dismissed_at is None and not self.is_onboarded`. (Onboarded → hide; dismissed → hide.)
- **`dismiss_onboarding() -> None`:** `self.onboarding_dismissed_at = timezone.now(); self.save(update_fields=["onboarding_dismissed_at"])` — mirrors `dismiss_welcome()`.
- **Helpers** (private): `_has_joined_guild` = `self.joined_guilds.exists()`; `_has_voting_preference` = `VotePreference.objects.filter(member=self).exists()` — `VotePreference` is a `OneToOneField(Member, related_name="vote_preference")` (confirmed), so `hasattr(self, "vote_preference")` works too; "set up voting" == has that row. `_profile_essentials_done` = the profile **content** signals from `profile_completeness` **minus** `show_in_directory` — cleanest is to add an `essentials_complete: bool` field to `ProfileCompleteness` (computed in the same pass, excluding the directory signal) and read it here; pin it in specs.

**Perf note (prescriptive):** three properties each recompute `profile_completeness` + re-run `joined_guilds.exists()`, and the template reads `show_onboarding` (→`is_onboarded`) *then* renders `onboarding` (whose `complete` recomputes `is_onboarded`) → ~3× each per render, on top of `_my_guilds` already walking `joined_guilds`. So **build the `OnboardingChecklist` ONCE in `build_home_context`** and pass it + the derived `show`/`complete` booleans through the context — don't call the three properties independently from the template; `@cached_property` the `_profile_essentials_done` / `_has_joined_guild` helpers as a belt. Pin a query-count assertion in specs.

## 6. UI / UX  ← completeness checklist applied

One screen: the **home page "Get started" card**. Plus the trivial dismiss action. This card **replaces** the current standalone "Finish setting up your profile" nudge.

### Screen — the "Get started" checklist card (`templates/hub/partials/_onboarding_checklist.html`)

- **Screen / partial:** included from `templates/hub/home.html` where the profile nudge is today, rendered **only when `onboarding.` is passed and `show_onboarding` is true** (the view gates it). One `hub-card`.
- **Layout & container:** a `hub-card` titled **"Get started at Past Lives"** with a **dismiss ✕** top-right, a one-line intro, then a **vertical list of 3 step rows**, then a progress line. Not a form, not a formset — a **status checklist** whose rows are links to existing pages.
- **Components used:** `hub-card`; `hub-text-muted`; the existing gold `pl-btn`/`hub-btn` for the primary row action; `trigger_toast()` on dismiss. New layout-only classes `.pl-onboarding-*` in `hub.css`.

**The controls, named explicitly:**

- **Each step row** = a status icon + label + a link:
  - **Done row:** a check (✓, gold/`--color-tuscan-yellow`) + label + a muted "Done" — the whole row still links to its page (so they can revisit), but reads as complete.
  - **Undone row:** an empty circle + label + a right-aligned **link/button to the step's page** (`row.url`) labelled by the action — "Set up your profile", "Join your guilds → My Guilds", "Set a voting preference → Voting". The profile row shows its **`hint`** ("40% complete").
  - **Voting row** carries a small muted **"Optional"** tag (it doesn't block completion).
  - **Why no "+ Add / Delete" or toggles:** the three steps are a **fixed, derived status list** — the member doesn't add/remove/toggle them, they *complete* them on other pages. So the §1 list-editor triad and the "booleans are toggles" rule **don't apply** (called out so a reviewer doesn't flag a missing Add button or want the checks to be `pl-toggle`s). The rows are navigation, not form controls.
- **Dismiss ✕ (the one real action):** a `pl-btn pl-btn--sm` icon button, top-right, `hx-post` to `hub_onboarding_dismiss`, `hx-target="closest .hub-card"`, `hx-swap="outerHTML"`, `hx-disabled-elt="this"`. The endpoint returns **`200` with an EMPTY body** (so the `outerHTML` swap replaces the card with nothing) + a `trigger_toast("You can finish setup anytime — Settings → Guilds, your profile, and the voting page.", "info")` via `HX-Trigger`. **Do NOT return `204`** — HTMX runs no swap on 204, so the card would linger; the codebase's 204 endpoints all pair with `hx-swap="none"` (e.g. the My Guilds toggle, `_my_guilds.html:28`), and a node-removing swap needs a 200 body. The *model* method mirrors `dismiss_welcome()`, but the welcome modal's own dismiss is a full-page `<form>` POST — **not** this HTMX path, so don't copy its mechanics. No dead end: the toast names where to finish.
- **Progress line:** under the rows — **"{required_done} of {required_total} done"** + a friendly tail ("you're almost there!" at 1/2, "— just one more!" logic optional). Reflects **required** steps only (voting excluded from the count so an optional step can't make it read 2/3 forever).
- **No page-level Save** — nothing on this card is edited here; the actions are links + the dismiss. (Correct by construction; noted so the reviewer doesn't expect a Save.)

**States:**
- **Shown:** `show_onboarding` true (not onboarded, not dismissed). Default for a new member.
- **A step just completed:** on the next home load the row shows done and the count ticks up; when the last **required** step completes, `is_onboarded` flips true → the card is **gone** next load (no explicit "all done" card needed; optionally a one-time success toast — deferred, §10).
- **Dismissed:** `dismiss_onboarding()` set → card removed immediately (HTMX outerHTML swap) + the info toast; it does **not** come back (sticky). Not a dead end — the toast names where to finish.
- **Loading:** the dismiss `hx-post` is a quick empty-200; `hx-disabled-elt="this"` on the ✕ prevents a double-fire.
- **Error:** dismiss fails (network) → the card stays and a client error toast fires (`hx-on::response-error` → `$dispatch('show-toast', {type:'error', …})`), consistent with the My Guilds toggle error pattern.
- **Unlinked account** (`member is None`): the card simply isn't rendered (the home page already handles the no-member case) — no crash.

**Dark + light:** `hub-card` + theme tokens only. The check color uses `--color-tuscan-yellow` (reads on both themes); the empty circle uses `--hub-border`; text uses `--hub-text` / `--hub-text-muted`. **No inline `background`/`color`**; **no `--surface`**. Row links are `<a>`/`pl-btn`, already themed. Verify both themes.

**Mobile:** rows are `display:flex; justify-content:space-between; align-items:center; gap:0.75rem; flex-wrap:wrap` so the action link wraps under the label on narrow widths; the dismiss ✕ stays pinned top-right; tap targets are real buttons/links; 8px-grid spacing; every row/action clears the element above (`margin-top:0.5–0.75rem`). No table, no horizontal scroll.

### The welcome modal (unchanged mechanism, tiny copy tweak)

- Keep the first-login welcome modal (`show_welcome_modal`) as the first touch. **Caveat:** the modal renders from `base.html` on **every** hub page (gated in `_get_hub_context`), so any copy must be **location-agnostic** — do NOT say "checklist below" (a member with the modal up on `/settings/` finds nothing below). Safe copy: "…and your **Get started** checklist on the home page walks you through the rest." **Intended first-login sequence:** the modal is the overlay; dismissing it (or the login redirect) lands them on `/`, where the card is the funnel. The two timestamps (`welcome_dismissed_at`, `onboarding_dismissed_at`) are independent — modal *over* the home card is coherent, not awkward. No new modal, no gating.

## 7. Notifications / emails / activity

**None.** This is a purely on-page nudge; the existing first-login/welcome email already greets new members. No new event, email, or `SiteActivity`. (A "you haven't finished setup" reminder email is explicitly deferred — §10.)

## 8. Build order (phased; each phase ships green)

1. **Model + logic.** Add `onboarding_dismissed_at` (+ migration); `OnboardingChecklist`/`OnboardingStep`; `is_onboarded`, `onboarding`, `show_onboarding`, `dismiss_onboarding()`; the `_has_joined_guild` / `_has_voting_preference` helpers. Specs for every branch. Full suite + lint + mypy green. *(Invisible — no UI yet.)*
2. **Home wiring + card.** `build_home_context` passes `onboarding` + `show_onboarding`; `home.html` replaces the profile-nudge block with the `_onboarding_checklist.html` include; `.pl-onboarding-*` in `hub.css`. Verify both themes + mobile. Green.
3. **Dismiss endpoint.** `onboarding_dismiss` view (+ URL), HTMX outerHTML swap + toast; error-revert. View/template specs. Green.
4. **Housekeeping.** Bump `plfog/version.py` VERSION + a member-friendly CHANGELOG entry (new feature → its own entry).

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*`), factory-boy, ≥98% branch coverage, `plfog-web` Docker image.

- **`Member` (membership):**
  - `is_onboarded`: **true** iff profile complete **and** ≥1 guild joined; **false** if either missing; **voting state does not change it** (true with no voting pref, false is unaffected by adding one). Pin this — it's the one subtle rule.
  - `onboarding`: three steps with correct `done`/`url`/`optional`/`hint`; `required_total == 2`; `required_done` counts only profile+guilds; `complete == is_onboarded`.
  - `show_onboarding`: true when not onboarded and not dismissed; false when onboarded (even if not dismissed); false when dismissed (even if not onboarded).
  - `dismiss_onboarding()`: stamps `onboarding_dismissed_at`, saves only that field; idempotent-ish (re-dismiss just re-stamps).
  - No N+1: assert the home-context path resolves the checklist without extra per-guild queries.
- **`hub` — home + endpoint:**
  - `build_home_context` includes `onboarding`/`show_onboarding`; a member who's onboarded gets `show_onboarding=False` (no card).
  - `onboarding_dismiss`: `@login_required @require_POST`; sets the stamp, returns 204 + toast; GET/anon rejected; unlinked member → graceful (no crash, no change).
- **Template states (parse HTML, per `reference_nested_form_save_bug`):** the card renders 3 rows with the right check/circle + the action links (`?tab=guilds`, the voting URL, the profile-edit URL), the **Optional** tag on voting, the progress "{n} of 2", and the dismiss ✕ with its `hx-post`. Assert the **old standalone profile nudge is gone** (folded into the card) so we don't ship both. Card **absent** when onboarded/dismissed.
- **Gotchas:** the profile row `hint` uses `profile_completeness.percent` — keep in sync with that source. No tz windows.

## 10. Open / deferred

- **"You're all set!" success moment** — optional one-time toast/confetti when the last required step completes; deferred (the card simply vanishing is fine for v1).
- **Reminder email** for members who never finish setup — out of scope (the existing welcome email covers first touch); revisit if completion is low.
- **Admin visibility** — a "not yet onboarded" column/filter on the member list — deferred; `is_onboarded` is available if wanted later.
- **Un-dismiss / "show setup again"** — v1 dismiss is sticky and the toast points them to Settings/guild pages to finish; a "re-show checklist" control is deferred (low value — the underlying pages are always reachable).
- **Optional voting-step discoverability** — when profile+guilds complete, `is_onboarded` flips and the whole card (voting row included) disappears next load, so a member who never set a voting preference won't be prompted again — the **Guild Voting** nav link is the standing fallback. Accepted for v1 (voting is explicitly optional); revisit with a lighter standalone voting nudge if adoption is low. (Corollary: the "2 of 2 done" progress copy is essentially never seen — the card vanishes on completion — so keep the progress copy simple: "{n} of 2 done" with a friendly tail only at 0/2 and 1/2.)
- **Voting definition** — "set up voting" == has a `VotePreference` row; if product later wants "has actually cast a vote," swap the `_has_voting_preference` check (isolated one-liner).
- **Making voting required** — if guild voting becomes core, flip `optional` and include it in `is_onboarded` (the checklist already renders it).

## Out of scope

- The **book/classes** onboarding wizard (`UserProfile.onboarding_completed_at`) — separate system, untouched.
- Any **gating/blocking** of hub features (locked: never blocks).
- A multi-step **wizard modal** (locked: checklist card, not a wizard).
- New editing surfaces — the card only *links* to the existing profile editor / My Guilds tab / voting page; it edits nothing itself.

## Done checklist

- [ ] `Member.onboarding_dismissed_at` added (reversible migration); does **not** affect `is_onboarded`.
- [ ] `is_onboarded` = profile complete **and** ≥1 guild (voting excluded); `onboarding` checklist + `show_onboarding` + `dismiss_onboarding()` implemented (fat model, typed, no N+1).
- [ ] Home card `_onboarding_checklist.html`: 3 status rows linking to profile editor / My Guilds (`?tab=guilds`) / voting; **Optional** tag on voting; "{n} of 2 done" progress; dismiss ✕ (HTMX outerHTML + info toast); **replaces** the old profile nudge.
- [ ] Never blocks; card hidden when onboarded or dismissed; sticky dismiss; no dead ends (toast names where to finish).
- [ ] Dark + light verified; mobile reflow verified (rows wrap, ✕ pinned, 8px grid); theme tokens only, no `--surface`, no inline control colors.
- [ ] Specs green (≥98%) in `plfog-web`; the **voting-doesn't-affect-`is_onboarded`** and **show/hide** rules pinned; ruff + mypy clean.
- [ ] `VERSION` bumped; member-friendly CHANGELOG entry added.
```
