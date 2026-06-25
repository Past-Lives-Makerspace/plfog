# Member Email Integrity — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-25
**Surface:** FOG hub (`pastlives.test:8000`) — the **Manage Members** admin list (`templates/hub/admin/members.html`, view `admin_members`). One backend touch in the membership app (queryset + signal).
**Related:** `docs/superpowers/specs/2026-04-07-user-email-aliases-design.md` (the three-email-store architecture this builds on). The nightly **`airtable_pull`** cron + the signup signal are the existing plumbing by which an emailless member becomes non-emailless — this feature only *surfaces* the problem, it does not fix data.

---

## 1. Summary

One member-record problem in three connected fixes, all on the **Manage Members** screen and its backing query:

1. **The email column lies.** The list renders `member.user.email` — the allauth **mirror** field the architecture says to never read directly. For unlinked Airtable members (`member.user is None`) it always shows "—" even though their email sits in `Member._pre_signup_email`; for some linked members the mirror is stale. We render **`member.primary_email`** instead (the property that already resolves the three stores correctly), with a prefetch so the list of up to 50 rows doesn't fan out one query per row.

2. **You can't see who has no email.** We add a **"Missing email"** filter chip (alongside the existing status/role/type filters) that shows only members whose `primary_email` resolves blank, each tagged with **why**: either "never signed up, no Airtable email" or "signed up, but no email on their account." A count badge on the chip tells the admin how many there are.

3. **The hole that mints them.** The user-creation signal silently creates ACTIVE members with a blank email. We do **not** block creation (that would break class-booking / guest-account flows) — instead we **log a loud warning** so it's visible in the logs, and the member automatically surfaces in fix #2's report (we derive "emailless" from `primary_email`, so no flag or migration is needed).

**How an emailless member gets fixed (existing plumbing, not built here):** the nightly **`airtable_pull`** cron writes the member's Airtable email into `Member._pre_signup_email`, and the signup signal (`membership/signals.py`) promotes that email onto the allauth account (verified + primary) when the member signs up. The report's only job is to surface *who* is still emailless and *why* — it changes no data itself.

### Locked decisions

| Decision | Choice |
|---|---|
| Email column source | Render `member.primary_email` (resolves linked → primary allauth `EmailAddress`, unlinked → `_pre_signup_email`), never `member.user.email`. |
| N+1 avoidance | View `prefetch_related` a `Prefetch("user__emailaddress_set", queryset=EmailAddress.objects.filter(primary=True), to_attr="_primary_emailaddresses")` — the exact hook `primary_email` already documents (`membership/models.py:336-339`). One extra query for the whole page. |
| "Emailless" definition | Derived, single source of truth: `primary_email == ""`. A **DB-level annotation** (`with_email_status()`) mirrors the property so the SQL filter and the Python property can never disagree. **No new model field, no migration.** |
| The "why" buckets | `no_airtable_email` = `user is None` AND `_pre_signup_email` blank. `no_account_email` = `user` set, no primary `EmailAddress`, blank mirror. Held as a `Member.EmailGap` `TextChoices` (labels) + a `email_gap_label` property. |
| Filter UI | A **chip** (link styled as `hub-btn`), not a new `<select>` — avoids adding a theme-sensitive form control entirely. Count badge reuses the existing `.hub-badge`. |
| Signal guard | Keep creating the member (don't break booking/guest flows); add a **loud `logger.warning`** when minting a member with a blank email. The member surfaces in the report automatically (derived). No persisted `needs_email` flag. |
| Manager location | Member queryset logic goes on **`MemberQuerySet` in `membership/models.py:120`** (where `active()` / `paying()` / `with_lease_totals()` already live), **not** `membership/managers.py` (that file holds `MemberEmailManager` only — the Member manager is `MemberQuerySet.as_manager()` at `models.py:283`). This matches the house pattern; flagged here because the brief named `managers.py`. |
| Count-badge scope | Counts emailless members **within the currently-applied status/role/type/search** (so the badge equals what the chip reveals). Default status is `active`; to audit former/invited members too, set **Status = All**. |

## 2. What already exists (reuse, don't reinvent)

Almost everything is in place — this is assembly plus one annotation and one log line.

| Need | Existing thing | Location |
|---|---|---|
| The list view to extend | `admin_members()` (paginated, status/role/type/search filters) | `hub/views.py:2031-2078` |
| The template to edit | Manage Members table (Email column at `:132`; filters form `:84-115`; table `:117-150`; empty row `:146-148`) | `templates/hub/admin/members.html` |
| URL name + access gate | `hub_admin_members` → `/manage/members/`, gated by `@fog_admin_required` | `hub/urls.py:153`; `hub/view_as.py:205-225` |
| Correct email resolver (the fix target) | `Member.primary_email` property — linked → primary `EmailAddress`, unlinked → `_pre_signup_email`; uses prefetched list when present | `membership/models.py:322-356` |
| The mirror to stop reading | `User.email` — kept in sync by allauth, "no application code should read or write this directly" | `2026-04-07-user-email-aliases-design.md` (three-store table) |
| Pre-signup email store | `Member._pre_signup_email` (`db_column="email"`, used only when `user is None`) | `membership/models.py:194-204` |
| Where Member queryset methods live | `MemberQuerySet` (`active`, `paying`, `with_lease_totals`) | `membership/models.py:120-141`; manager wired at `:283` |
| The offending create | `ensure_user_has_member` signal creates `Member(..., _pre_signup_email=instance.email or "")` | `membership/signals.py:90-100` |
| Loud-warning precedent (style to match) | `logger.warning("Cannot auto-create Member ... no MembershipPlan")` in the same signal | `membership/signals.py:84-88` |
| Count-badge styling | `.hub-badge` (tuscan-yellow pill) — reuse, no new color | `static/css/hub.css:922` |
| Chip / button styling | `.hub-btn`, `.hub-btn--sm` (`:856`), `.hub-btn--ghost` (`:863`), `.hub-btn--primary` | `static/css/hub.css` |
| The view tests to extend | `describe_admin_members()` | `tests/hub/admin_views_spec.py:51` |
| Linked-member + `EmailAddress` test patterns | `User.objects.create_user(... email=...)` (signal auto-creates the primary `EmailAddress`); `EmailAddress.objects.create(...)`; `MemberFactory` (`_pre_signup_email` sequence, `user=None`) | `tests/membership/member_email_staging_spec.py`; `tests/membership/factories.py:45-51` |

**Gaps to close (small):**
- Two chainable `MemberQuerySet` methods (`with_email_status`, `missing_email`) + a `Member.EmailGap` `TextChoices` + a `Member.email_gap_label` property — all in `membership/models.py`.
- View: prefetch the primary `EmailAddress` (N+1 fix), read the `email` filter param, compute `missing_count`, narrow the page when active.
- Template: Email column → `primary_email`; add the chip + badge; add the per-row "why" label in the missing view; context-aware empty state; move the inline-styled table onto a `.pl-members-table` class with a mobile card-stack.
- Signal: one `logger.warning` before the blank-email create.
- **No model field change, no migration.**

## 3. Where the code lives

```
membership/
  models.py          # + MemberQuerySet.with_email_status() and .missing_email()
                     # + Member.EmailGap (TextChoices) + Member.email_gap_label (property)
  signals.py         # loud logger.warning when minting a Member with a blank email (~:90)
hub/
  views.py           # admin_members: prefetch primary EmailAddress; email filter; missing_count
templates/hub/admin/
  members.html       # Email col → primary_email; Missing-email chip + badge; per-row "why";
                     # empty-state copy; table markup → .pl-members-table + data-label attrs
static/css/
  hub.css            # + .pl-members-table (base + responsive card-stack). Chip/badge reuse hub-btn + hub-badge.
tests/
  membership/member_email_status_spec.py   # NEW — with_email_status / missing_email / email_gap_label / signal warning
  hub/admin_views_spec.py                  # extend describe_admin_members — column source, filter, badge, empty, N+1, gating
plfog/version.py     # VERSION bump + member-friendly CHANGELOG entry — at BUILD time, last (see §8)
```

Home app: **`membership`** (queryset/model/signal) + **`hub`** (view/template/CSS). Everything stays inside the existing coverage/mypy scope.

## 4. Data model

**No schema change. No migration.** "Emailless" is *derived* from `primary_email`, which already exists. The only model-file additions are constants and a property:

```python
# membership/models.py — nested on Member (constants + labels only; no field uses it)
class EmailGap(models.TextChoices):
    NO_AIRTABLE_EMAIL = "no_airtable_email", "Never signed up — no email on file from Airtable"
    NO_ACCOUNT_EMAIL  = "no_account_email",  "Signed up, but has no email on their account"
```

```python
@property
def email_gap_label(self) -> str:
    """Human reason this member is emailless. Requires the `email_gap` annotation
    from MemberQuerySet.with_email_status() — only rendered in the missing-email view."""
    return self.EmailGap(self.email_gap).label if self.email_gap else ""
```

Per CLAUDE.md: `TextChoices` for the buckets, meaningful labels, the property documents its annotation dependency (fail loudly if accessed un-annotated — the template only renders it under the `email=missing` branch).

## 5. Business logic (fat models)

Two chainable methods on **`MemberQuerySet`** (`membership/models.py:120`), beside `active()`/`paying()`. The filtering and bucketing happen in SQL so the view stays thin and the 50-row list does not evaluate a Python property per row.

```python
def with_email_status(self) -> "MemberQuerySet":
    """Annotate `has_email` (mirrors the primary_email property at the DB level) and
    `email_gap` (the reason a member is emailless) for the Missing-email report."""
    from allauth.account.models import EmailAddress
    has_primary = Exists(EmailAddress.objects.filter(user_id=OuterRef("user_id"), primary=True))
    return self.annotate(_has_primary_email=has_primary).annotate(
        has_email=Case(
            When(Q(user__isnull=True) & ~Q(_pre_signup_email=""), then=Value(True)),
            When(_has_primary_email=True, then=Value(True)),
            When(Q(user__isnull=False) & ~Q(user__email=""), then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        email_gap=Case(
            When(Q(user__isnull=True) & Q(_pre_signup_email=""), then=Value(Member.EmailGap.NO_AIRTABLE_EMAIL)),
            When(Q(user__isnull=False) & Q(_has_primary_email=False) & Q(user__email=""),
                 then=Value(Member.EmailGap.NO_ACCOUNT_EMAIL)),
            default=Value(""),
            output_field=CharField(),
        ),
    )

def missing_email(self) -> "MemberQuerySet":
    """Only members whose primary_email resolves blank (no usable email)."""
    return self.with_email_status().filter(has_email=False)
```

- **`has_email` mirrors `primary_email` exactly:** unlinked → `_pre_signup_email` non-blank; linked → a primary `EmailAddress` exists *or* the mirror is non-blank. This 1:1 correspondence is the most important correctness property (a parametrized test asserts annotation == property for every case).
- `_has_primary_email` is annotated first, then referenced by the second `annotate()` — chaining is required for that reference.
- Chainable on the already-filtered view queryset: `qs.missing_email()` and `qs.missing_email().count()`.
- **Aggregate-mix note:** the view also has `class_count=Count("classes", distinct=True)`. Mixing that aggregate with the `Exists`/`Case` annotations is fine — `Exists` is a correlated subquery (no fan-out) and `Count(distinct=True)` already guards the classes join. A test asserts the row count and `class_count` are not multiplied.
- **Imports to add (these snippets are not drop-in as-is):** `membership/models.py:12` currently imports only `DecimalField, Q, Sum, Value` from `django.db.models` — add `BooleanField, Case, CharField, Exists, OuterRef, When`. The `EmailAddress` import stays a local import inside the method (avoids a module-level allauth dependency, matching `primary_email`).

**View wiring (thin — `hub/views.py:2031`):**

```python
qs = (
    Member.objects.select_related("user", "membership_plan")
    .annotate(class_count=Count("classes", distinct=True))
    .prefetch_related(Prefetch(
        "user__emailaddress_set",
        queryset=EmailAddress.objects.filter(primary=True),
        to_attr="_primary_emailaddresses",          # the hook primary_email reads
    ))
    .order_by("full_legal_name")
)
# ... existing status / role / type / search filters unchanged ...
email_filter = request.GET.get("email", "")
missing_count = qs.missing_email().count()          # emailless within current filters
if email_filter == "missing":
    qs = qs.missing_email()                          # page rows now carry has_email + email_gap
# ... paginate, render with email_filter + missing_count in context ...
```

The prefetch is what fixes the N+1: `primary_email` uses the prefetched per-user list (`getattr(user, "_primary_emailaddresses", None)`), so the page costs a constant number of queries regardless of row count.

**Imports to add (not drop-in as-is):** `hub/views.py:2034-2035` currently imports only `Paginator` and `Count, Q` (from `django.db.models`) inside `admin_members` — add `Prefetch` to the `django.db.models` import and `from allauth.account.models import EmailAddress`.

## 6. Signal change

`ensure_user_has_member` (`membership/signals.py:90-100`) keeps creating the member — **no hard block** (booking / guest-account flows depend on it). Add a loud warning before the create, matching the existing no-plan warning's style (`:84-88`):

```python
member_email = instance.email or ""
if not member_email:
    logger.warning(
        "Creating Member for user %s (id=%s) with NO email — this account has no "
        "usable email and will surface in Manage Members → 'Missing email'. "
        "See the member-email-integrity spec.",
        instance.username, instance.pk,
    )
Member.objects.create(
    user=instance,
    full_legal_name=name,
    _pre_signup_email=member_email,
    membership_plan=plan,
    status=Member.Status.ACTIVE,
)
```

No flag is persisted: such a member has `user` set, no primary `EmailAddress`, and a blank mirror → `has_email=False`, `email_gap="no_account_email"` → it appears in the report on its own. When the nightly `airtable_pull` later fills `_pre_signup_email` and the member signs up, the signup signal promotes that email to a primary `EmailAddress` and the member drops out of the report — no code here touches that path.

## 7. UI / UX  ← completeness checklist applied concretely

### Screen — Manage Members list (`templates/hub/admin/members.html`)

**Layout & container:** unchanged page shape — a `hub-card` with a filter row and a table. Read-only list; no forms added beyond the existing filter `<form method="get">`. The brief's required changes are: the Email column source, a filter chip + badge, a per-row "why" label in the missing view, a context-aware empty state, and a mobile-safe table.

**Components used:** existing `hub-btn` family for the chip, `.hub-badge` for the count, the existing GET filter form. No new components, no modal, no toggle — nothing here is a boolean or a destructive action.

**The controls, named explicitly:**

- **Email column (the fix):** `:132` changes from `{{ member.user.email|default:"—" }}` to `{{ member.primary_email|default:"—" }}`. Unlinked Airtable members now show their `_pre_signup_email`; linked members show their live primary address, not a stale mirror.
- **"Missing email" chip:** a link styled as a small button, placed in the `.members-filters` row after the Reset link. It toggles `?email=missing` while preserving the other filters:
  ```django
  {% if email_filter == 'missing' %}
    <a class="hub-btn hub-btn--sm hub-btn--primary"
       href="?status={{ status_filter }}&role={{ role_filter }}&type={{ type_filter }}&q={{ search }}">
       Missing email{% if missing_count %} <span class="hub-badge">{{ missing_count }}</span>{% endif %}
    </a>
  {% else %}
    <a class="hub-btn hub-btn--sm hub-btn--ghost"
       href="?email=missing&status={{ status_filter }}&role={{ role_filter }}&type={{ type_filter }}&q={{ search }}">
       Missing email{% if missing_count %} <span class="hub-badge">{{ missing_count }}</span>{% endif %}
    </a>
  {% endif %}
  ```
  An `<a>` inside the `<form>` is valid and behaves as a plain link (it does not submit the form). When **active**, the chip is `hub-btn--primary`; when inactive, `hub-btn--ghost` — exactly the active/inactive convention the existing buttons use.
- **Preserve the filter across Apply:** add a hidden input to the existing GET form so changing Status/Role/Type and clicking **Apply** keeps the missing-email view:
  ```django
  {% if email_filter == 'missing' %}<input type="hidden" name="email" value="missing">{% endif %}
  ```
  So the **chip** turns the filter on/off, and the form's **Apply** carries it forward — no surprising exits.
- **Preserve the filter across pagination (functional fix):** the existing Prev/Next links (`members.html:154-156`) rebuild the query string *without* the `email` param, so page 2 silently drops the filter — a real dead end, because the audit guidance (Status = All) easily pushes the emailless set past 50/page. Append the param to **both** hrefs:
  ```django
  ...&page={{ page.previous_page_number }}{% if email_filter == 'missing' %}&email=missing{% endif %}
  ...&page={{ page.next_page_number }}{% if email_filter == 'missing' %}&email=missing{% endif %}
  ```
  Now paging through a large emailless set stays in the filter.
- **Count badge:** the `.hub-badge` inside the chip shows `missing_count` (hidden when 0 — a clean chip plus the reassuring empty state on click). It is the emailless count within the current status/role/type/search scope, so it equals what the chip reveals.
- **Per-row "why" label (missing view only):** in the Email cell, under the "—", render the reason — gated so the `email_gap_label` property is only read when the annotation is present:
  ```django
  <td data-label="Email" ...>
    {{ member.primary_email|default:"—" }}
    {% if email_filter == 'missing' %}<div class="hub-note hub-text-muted">{{ member.email_gap_label }}</div>{% endif %}
  </td>
  ```
  Labels: "Never signed up — no email on file from Airtable" / "Signed up, but has no email on their account."

**States:**
- **Empty (missing view, none found):** the existing `{% empty %}` row becomes context-aware:
  ```django
  {% empty %}
  <tr><td colspan="6" ...>
    {% if email_filter == 'missing' %}No members are missing an email — nice.{% else %}No members match your filters.{% endif %}
  </td></tr>
  ```
- **Empty (normal list):** unchanged "No members match your filters."
- **Loading:** none — full-page GET (links + Apply), no HTMX swap here.
- **Error:** none added — an unknown `email` value is simply not `"missing"`, so the filter is inactive (no crash, no 500).
- **Success feedback:** navigation *is* the feedback — the chip flips to its active state and the list narrows; the badge shows the live count.
- **No dead ends:** the chip toggles off to its own href; Reset (already present) clears everything.

**Dark + light (verify both):**
- The chip reuses `hub-btn` / `hub-btn--sm` / `hub-btn--ghost` / `hub-btn--primary` and the badge reuses `.hub-badge` — all already defined with theme tokens, so both themes are correct with **no new color and no inline `background`/`color` on any control**.
- We add **no new `<select>`/`<input>`** (the chip is a link), so the white-box-on-dark failure class can't occur. The pre-existing filter selects/inputs (`members.html:14-62`) are untouched and out of scope.
- The "why" label uses `hub-note` / `hub-text-muted` (token-driven). No date/time pickers on this screen.

**Mobile (the table must not blow out the viewport):**
- The table is currently fully inline-styled `<th>`/`<td>` with no responsive handling → on a phone, six columns overflow. Move all of that styling onto a **`.pl-members-table`** class in `hub.css` (width:100%, `border-collapse`, token colors, and `<td>` padding that mirrors today's inline values), add a `data-label="…"` attribute to every `<td>`, and add a card-stack at narrow width — the FRONTEND "tables stack into cards" guidance, all token-based:
  ```css
  .pl-members-table { width: 100%; border-collapse: collapse; font-size: 0.9375rem; }
  .pl-members-table th, .pl-members-table td { padding: 0.55rem 0.75rem; }
  .pl-members-table th { text-align: left; color: var(--hub-text-muted); font-weight: 500; font-size: 0.875rem; }
  .pl-members-table td { color: var(--hub-text-muted); }            /* Email/Status/Role/etc, as today */
  .pl-members-table td:first-child { color: var(--hub-text); }      /* Name column, as today (two-tone) */
  .pl-members-table tr { border-bottom: 1px solid var(--hub-border); }
  @media (max-width: 640px) {
    .pl-members-table thead { display: none; }
    .pl-members-table tr { display: block; border: 1px solid var(--hub-border); border-radius: 8px;
                           margin-bottom: 0.75rem; padding: 0.25rem 0.75rem; }
    .pl-members-table td { display: flex; justify-content: space-between; gap: 1rem; text-align: right;
                           padding: 0.4rem 0; }
    .pl-members-table td::before { content: attr(data-label); color: var(--hub-text-muted);
                                   font-weight: 500; text-align: left; }
  }
  ```
  The two-tone `td` rule reproduces today's look (Name in `--hub-text`, the rest muted). **One deliberate change from today:** the row separator moves from the current inline `rgba(255,255,255,0.04)` (invisible on the light theme) to `var(--hub-border)`, so rows are visible in both themes. Result: **no horizontal scroll** — rows stack into labelled cards on phones, the desktop table is unchanged, and the inline-style debt the brief flagged is removed. `<td>` padding (`0.55rem`/`0.4rem`) mirrors today's inline values rather than snapping to the 8px grid; the card gaps/margins (`0.25/0.75/1rem`) stay on-grid. This also lands the "why" sub-line cleanly inside the Email card row.

**User-lens pass:** the primary admin task — "find members with no email and understand why" — is one click (the chip), the count is visible before clicking, every row says which kind of gap it is, and the empty state is friendly. Nothing is half-built: there's nothing to create/edit/delete here, only to see and act on (acting = the existing edit page + the nightly sync).

## 8. Build order (phased; each phase ships green)

Each phase is independently shippable (full suite + `ruff format .` + `ruff check .` + mypy), run in the `plfog-web` Docker image.

1. **Manager + model (logic first).** Add `MemberQuerySet.with_email_status()` / `.missing_email()`, `Member.EmailGap`, `Member.email_gap_label`. Tests: annotation == `primary_email` for every case; bucket labels; `missing_email().count()`.
2. **Signal warning.** Add the loud `logger.warning` for blank-email creation. Tests: warning emitted for blank email, not for a real email; the minted member appears in `missing_email()`.
3. **View + template + CSS.** Add the imports (§5); prefetch primary `EmailAddress`; read `email` param; compute `missing_count`; Email column → `primary_email`; chip + badge + hidden input; carry `email=missing` through the Prev/Next pagination hrefs; per-row "why"; context-aware empty state; `.pl-members-table` + card-stack. Tests: column source, filter, badge, empty state, **filter survives pagination**, N+1 guard, gating.
4. **Housekeeping.** Bump `plfog/version.py` `VERSION` + prepend a member-friendly `CHANGELOG` entry (sketch below).

> Spec only — do not build until approved. **Version bump + changelog happen at BUILD time, one entry per PR — not now.**

**Changelog sketch (member-facing, plain language — admin-visible improvements only; the signal warning is internal and not announced):**
- "The member list now shows everyone's real email — including members imported from Airtable who haven't signed up yet."
- "Admins can spot members with no email on file using a new 'Missing email' filter, so nobody slips through the cracks."

## 9. Testing

BDD `*_spec.py`, `describe_*` / `it_*` (note: **`context_*` is not collected** — use `describe_*` for every nested block), factory-boy, ≥98% coverage gate, run in `plfog-web` Docker (`--no-cov` for a subset). Seed a `MembershipPlanFactory()` before creating users so the Member-creation signal fires.

**`tests/membership/member_email_status_spec.py` (NEW) — `describe_MemberQuerySet` / `describe_with_email_status`:**
- Linked member with a primary verified `EmailAddress` → `has_email` True, `email_gap` "", **not** in `missing_email()`.
- Linked member with a primary **unverified** `EmailAddress` → `has_email` True (matches `primary_email`, which uses the primary flag, not verified) — guards against the filter diverging from the property.
- Linked member, **no** `EmailAddress`, blank mirror → `has_email` False, `email_gap` `no_account_email`, in `missing_email()`.
- Linked member, no `EmailAddress`, **non-blank** stale mirror → `has_email` True (property returns the mirror), not missing.
- Unlinked member with `_pre_signup_email` set → `has_email` True, not missing.
- Unlinked member with blank `_pre_signup_email` → `has_email` False, `email_gap` `no_airtable_email`, in `missing_email()`.
- **Parametrized equivalence:** for each of the above, `member.has_email == bool(member.primary_email)` — the load-bearing correctness assertion.
- `missing_email().count()` equals the number of emailless members; a member with multiple classes does **not** inflate the count (aggregate-mix guard).
- `describe_email_gap_label`: returns the correct human label per code; returns "" when `has_email`.

**`tests/membership/member_email_status_spec.py` — `describe_ensure_user_has_member` (signal):**
- Creating a user with a **blank** email → Member created (status ACTIVE), `logger.warning` emitted (assert via `caplog`), member is in `missing_email()`.
- Creating a user **with** an email → no warning; after signal/`migrate_to_user` the primary `EmailAddress` exists, so the member is **not** in `missing_email()`.

**`tests/hub/admin_views_spec.py` — extend `describe_admin_members` (`:51`):**
- Email column renders `primary_email`: an unlinked member with `_pre_signup_email` shows that address (was "—" before — the regression this fixes); a linked member with a primary `EmailAddress` but a stale/blank `user.email` shows the `EmailAddress`, not the mirror.
- `?email=missing` shows only emailless members and excludes members with an email.
- The "why" label text appears per emailless row (both bucket labels).
- `missing_count` in context equals the emailless count within the current filters; the badge renders it.
- Empty state: `?email=missing` with zero emailless → "No members are missing an email — nice."
- Hidden `email` input present in the GET form when the filter is active (preserves it across Apply).
- **Filter survives pagination:** with >50 emailless members, the rendered Prev/Next links carry `email=missing`, and `GET ?email=missing&page=2` still returns only emailless members (guards the page-2 dead-end fix).
- **N+1 guard:** with several linked + unlinked members, `django_assert_max_num_queries(...)` holds the view to a constant query budget (proves the `Prefetch` works).
- Gating: a non-admin user → 403 (existing `@fog_admin_required`).

**Gotchas:** `User.objects.create_user(email=...)` already triggers the signal to create the primary `EmailAddress`, so build the "no account email" case from a **blank-email** user (the PART 3 scenario) or by deleting the auto-created `EmailAddress`. No timezone/date-window math here.

## 10. Out of scope / deferred

- **No model field / migration.** Emailless-ness is derived from `primary_email`; a persisted `needs_email` flag was considered and rejected (it would duplicate the property and need a migration).
- **No fixing of data from this screen.** The report only *surfaces* emailless members. They become non-emailless via the **existing** nightly `airtable_pull` cron (writes `_pre_signup_email`) + the signup signal (promotes it to a primary `EmailAddress`). A *manual* Airtable email-sync action is **not** part of this work (redundant with the nightly cron).
- **No change to the pre-existing filter selects/inputs** (`members.html:14-62`) — untouched; the new filter is a link chip, so no new theme-sensitive control is introduced.
- **No hard block** in the signal — by design, to preserve class-booking / guest-account flows.
- **No new notifications / activity log entries** — this is an admin-visibility + integrity fix, not a member-facing event.
