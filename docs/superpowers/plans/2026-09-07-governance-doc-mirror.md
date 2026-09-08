# Governance Document Mirror — Spec & Implementation Plan (Spec E)

**Status:** Spec only — not yet approved to build.
**Date:** 2026-09-07
**Surface:** FOG hub `members.pastlives.space` — a new `/governance/` register index and document page, a new
sidebar entry, one Django admin screen, one management command, one scheduled job.
**Related:** `2026-09-07-member-wiki-brief.md` (binding — read it first) · `2026-09-07-member-wiki-core.md`
(spec A, **hard dependency**) · `2026-09-07-member-wiki-guild-tab.md` (B) ·
`2026-09-07-member-wiki-moderation.md` (D). Reference implementation:
`github.com/Past-Lives-Makerspace/plm-kb-app` @ v1.49.0.

---

## 1. Summary

Past Lives' policies, bylaws, role explications, and signed agreements live as markdown in PLM's private
`org/` git repo, and are served today by Morlock's Knowledge Base app on his own box. **55 of the 59
published documents there are gated at guild-officer level and no ordinary member has an account.** So for
the membership those documents effectively do not exist: a member who wants to read the Member Agreement,
the Code of Conduct, or the fines policy has to ask someone.

This spec mirrors that corpus **read-only** into FOG. A member opens the same app they already use, reads
the policy with FOG's login, FOG's theme, FOG's search, next to the member wiki. Nothing is authored in
FOG and nothing is written back: the `org/` git repo stays the single source of truth, and Morlock's app
keeps the governance *workflow* — the approval ladder, the tracker, proposed edits, the moderation inbox.
FOG gets presentation and one login. That's the whole trade.

### Locked decisions (from the brief + resolved here)

| Decision | Choice |
|---|---|
| **Model name** | **`GovernanceDocument`**, not `PolicyDocument`. Two of the three registers (Document, Roles) are not policies — bylaws, articles, a glossary, a brand guide, a pricing guide, and role explications all live here. A `PolicyDocument` row whose `register` is `"role"` is a name that lies, and the first person to read it wrong writes a bug. "Governance" is also the exact word §2 of the brief uses for this content home, so the model, the URL, and the brief all say one word. Member-facing copy says **"Policies"** — that's the word members search for. |
| **Direction** | One way, always. No comments, no proposed edits, no approval ladder, no tracker, no moderation inbox in FOG — see §1.1. |
| **Register identity** | FOG **does not mint `reg_id`s** (DOC-###, POL-###, ROLE-###). Those are Morlock's register identity; a parallel counter in FOG would be a second, conflicting ID space for the same documents, and the counter is precisely what reshuffled 27 stable IDs in his 2026-08-12 incident. If IDs are ever wanted here they arrive in the source, not from us. |
| **Tier system** | FOG's own named roles. **No integer ladder, no second Profile model.** See §4.2. |
| **Default visibility** | `min_role = ADMIN`, fail-closed at the **top** rung. An unclassified or newly-arrived document is invisible to everyone but admins until someone publishes it deliberately — see §4.2, "Why the default is `ADMIN` and not `LEADERSHIP`". Publishing is then two deliberate steps (ADMIN → LEADERSHIP → MEMBER), and neither happens by a sync. |
| **Public tier** | **None.** Every mirrored document requires a FOG login, like the rest of the hub (brief §4, "Public access: None"). Morlock's four public documents keep their public home on his box; we cross-link rather than re-publish anonymously. |
| **Markdown** | A **new `governance` profile** in `membership/markdown.py`. Never a second renderer, never a loosened `help` profile. See §4.4. |
| **Source of truth at sync time** | Fetch the private repo as a **tarball from the GitHub API** with a fine-grained read-only token, extract to a temp dir, sync, discard. See §6.0 — this is the load-bearing architectural call. |
| **Cadence** | Once per deploy (`buildCommand`, the `seed_help_center` slot) **and** daily via `run_scheduled_tasks`, so a policy change reaches members without a plfog deploy. |
| **Rollout** | Dark. `SiteConfiguration.governance_page_enabled`, **default `False`** (unlike `help_page_enabled`/`wiki_link_enabled`, which default `True`) — the code ships before the content and an empty "Policies" nav entry in front of 200 members is worse than no entry. |
| **Search** | Folded into spec A's unified `/wiki/search/` with a source chip, filtered by the same visibility rule. `icontains`, per-term AND. **No Postgres FTS** (brief §4). Brief §9.1 makes the grouped `?source=` search a named requirement on A, so E has **no fallback search page** — §2.1. |
| **Minutes & finance** | Hard-excluded at the source, not tier-gated. See §5.3. |
| **Reading requires** | A linked `Member` of **any `status`**, on a request not previewing as Guest. Deliberately **not** `status == ACTIVE` — a member whose dues lapsed last week should still be able to read the membership agreement they are arguing about. Note this means `governance_roles` cannot route through `permissions._editing_member`, which requires `view_as.is_member` and therefore ACTIVE: see §4.2. |

### 1.1 Explicit non-goals

Stated so a reviewer can reject scope creep by pointing at this list rather than arguing taste:

- **No comments.** Morlock built per-section comments in v1.47.0 and reverted them entirely in v1.49.0, two
  days later, because "the shape isn't right". We are not re-litigating that on his behalf.
- **No proposed edits, no approval ladder, no tracker, no `/inbox/`.** Governance workflow stays on his box.
  A FOG copy would be a second, diverging record of who approved what — and an approval record you cannot
  trust is worse than none.
- **No writes of any kind.** No FOG field ever flows back to `org/`. The only FOG-owned field on the model
  is `min_role`, and it is a *visibility* decision, not content.
- **No `Record` / `Equipment` / `Person` mirroring.** His records library is executed agreements and filed
  PDFs; his people register is the member roster, which FOG already owns and is authoritative for.
- **No meeting minutes.** FOG has `Meeting`. Two minutes stores is the exact merge mistake the brief §2
  forbids.
- **No FOG-side hard delete in the UI.** Ever. A shell flag only (§5.6).

---

## 2. What already exists (reuse, don't reinvent)

Confirmed in the codebase on 2026-09-07.

| Need | Existing thing | Location |
|---|---|---|
| Markdown → safe HTML, profile-per-surface | `render_markdown(source, profile=…)`, `_ALLOWED_TAGS`, `_ALLOWED_ATTRS`, `_HEADING_ID_PATTERN`, `_allow_help_heading_attr`, `_harden_link`, `_harden_link_help` | `membership/markdown.py` (by symbol — the line numbers in this file drift every PR) |
| Template filter idiom for a sanitized body | `page_content` / `help_markdown` filters + the `# noqa: S308` convention | `membership/templatetags/membership_md.py` |
| TOC from rendered HTML, one regex pass | `_HELP_TOC_HEADING_RE` + `WikiArticle.toc()` | `membership/models.py:3055,3298` |
| Search queryset to copy (per-term AND `icontains`) | `WikiArticleQuerySet.search()` | `membership/models.py:3092` |
| Escape-then-`<mark>` snippet builder | `WikiArticle.search_snippet()` | `membership/models.py:3212` |
| Idempotent seed command house pattern (`update_or_create`, `--dry-run`, a written report, `CommandError` on a missing asset) | `seed_help_center` | `membership/management/commands/seed_help_center.py` |
| Read-only-document reading surface to match visually | `help_page` / `help_category` / `help_article` / `help_search` + their templates | `hub/views.py:4138-4300`, `templates/hub/help_article.html`, `help_search.html` |
| Breadcrumb + TOC + prose + aside chrome | `.pl-help-breadcrumbs`, `.pl-wiki-toc`, `.pl-md` / `.pl-md--help` | `static/css/hub.css:3583,3435,3663,3445` |
| Feature flag → sidebar + view redirect | `SiteConfiguration.help_page_enabled`; exposed by `core.context_processors.feature_flags` and consumed in **both** `templates/hub/base.html` sidebar blocks (the `request.view_as.is_admin` block ~:208 and the `{% else %}` block ~:349) | `core/models.py` (`SiteConfiguration`) |
| `view_as`-aware effective role | `ViewAs.is_admin` / `.is_guild_officer` / `.is_member`, `request.view_as` | `hub/view_as.py:109-160` |
| Request-level permission helpers (the filter/check house style) | `membership/permissions.py` — `is_effective_staff`, `_editing_member`, `editable_meeting_scopes` | `membership/permissions.py` |
| Guild leadership in one query | `Member.staffed_guilds` (`guild_lead` FK **or** any `GuildStaffMembership`) | `membership/models.py:1157` |
| Cross-guild staff tier | `Member.fog_role` / `is_fog_admin` / `is_guild_officer` | `membership/models.py:351,455,1021` |
| Scheduled job registry + Automations dashboard + "Run now" | `SCHEDULED_JOBS`, `Cadence`, `record_run`, `is_enabled` | `core/scheduled_jobs.py` |
| Audit feed | `SiteActivity.log(kind, actor=, target=, payload=)` | `core/models.py:1298` |
| HTTP client + its test double | `httpx` + `respx` (already dependencies) | `requirements.txt` |
| Page chrome components | `components/page_header.html`, `components/table_search.html` (**note: it renders a real `<form>` and an `<input type="search">`** — §6.4), `hub-card`, `pl-btn` | `templates/components/` |
| Site Settings toggle plumbing | `SiteSettingsForm.Meta.fields` + `site_settings.html`'s `field.name !=` exclusion chain and the explicit Features includes | `hub/forms.py` (`SiteSettingsForm`), `templates/hub/admin/site_settings.html` (**exclusion chain :184, Features includes :461-462** as of 2026-09-07 — grep `help_page_enabled` rather than trusting the numbers) |
| Help-key registry + its CI integrity test | `HELP_KEYS`, `KEY_PATTERN`; every `data-help-key` in any template must resolve | `core/help_registry.py`, `tests/hub/help_keys_spec.py` |
| Admin-only activity feed rendering | `_activity_feed.html` — renders `get_kind_display`, actor email, timestamp, optional email badge. **It never renders `payload`.** | `templates/hub/admin/_activity_feed.html` |
| Scheduled-run recording (no "note" field: only RUNNING / OK / FAILED + `error`) | `record_run`, `ScheduledTaskRun`, `ScheduledJobState.is_enabled` (**absence of a row means enabled**) | `core/scheduled_jobs.py`, `core/models.py` |

**Genuine gaps to build:** the model, the tier resolver, the `governance` markdown profile, the sync command
(fetch + classify + guard + render + report), two hub views + two templates, one CSS block, one Django admin
screen, one `SiteConfiguration` flag, one `SiteActivity.Kind`, one `ScheduledJob` row.

### 2.1 What I need from spec A — now settled by the brief, not negotiated here

E builds **after** A. **Brief §9.1 makes the multi-source search contract a named, non-optional requirement
on A's own scope**: `?source=`, grouped results, a source chip, and a browse list for an empty `q`. That
ruling is binding and supersedes A's §6.2 as drafted, so E no longer carries a fallback and no longer asks
A for anything as a favor. What E consumes, by name:

1. **`/wiki/search/`** (`hub_wiki_search`) accepting **result groups**, not one flat list — each group
   `{source_label, source_slug, results}` — plus the `?source=` filter chip row. E contributes a
   `governance` group.
2. **A's shared search-result partial**, `templates/hub/partials/_search_result.html` (brief §9.1 names it
   `_wiki_card.html` with an optional `snippet` parameter — **E uses whichever name A ships**; the contract
   is one card shape, `title` / `url` / `snippet` / `source_chip` / *at most one* `status_pill`, per brief §3
   "no pill salad").
3. **The `pl-` search chrome classes** A introduces for that page, so governance results carry no bespoke
   styling of their own.
4. A's decision on whether the Help Center is folded into the same box. It should be — three stores, one
   search, per brief §2 — but that is A's call, not mine.

**There is no E-side fallback search page.** If A's grouped search slips, E4 slips with it; E1–E3 are
unaffected and still ship. A private `/governance/search/` would fork the one-search-box contract in brief
§2 for the exact duration that makes it permanent.

E takes nothing from B or D. It emits no moderation events and has no guild tab.

---

## 3. Where the code lives

```
membership/
  markdown.py                                   + governance profile (_GOV_TAGS/_GOV_ATTRS/_harden_link_gov)
  models.py                                     + GovernanceDocument, GovernanceDocumentQuerySet
  permissions.py                                + governance_roles(), visible_governance_documents()
  admin.py                                      + GovernanceDocumentAdmin (min_role is the ONLY editable field)
  governance_source.py                          NEW — fetch/extract the org/ tarball; no Django imports
  governance_policy.py                          NEW — the port of sync_docs' POLICY: SKIP_DIRS, EXCLUDE_FILES,
                                                       SENSITIVE, GUARD_ALLOW, REDACTIONS, classify(),
                                                       frontmatter, title/version/status parsing, slugify
  management/commands/sync_governance_docs.py   NEW — orchestration only; imports the two modules above
  migrations/01XX_governance_document.py        NEW
core/
  models.py                                     + SiteConfiguration.governance_page_enabled
                                                + SiteConfiguration.governance_guard_ack  (§5.7)
                                                + SiteActivity.Kind.GOVERNANCE_SYNCED
  context_processors.py                         + governance_page_enabled
  help_registry.py                              + the "nav.governance" HELP_KEYS entry — REQUIRED, or
                                                  tests/hub/help_keys_spec.py fails CI on the nav's
                                                  data-help-key (§6.7)
  scheduled_jobs.py                             + ScheduledJob(key="sync_governance_docs", cadence=DAILY)
                                                  — lands in E5, not E2 (§5.8)
  views.py                                      + "Disallow: /governance/" in robots_txt
  migrations/00XX_governance_flag.py            NEW
hub/
  urls.py                                       + governance/, governance/d/<slug>/, governance/guard-ack/
  views.py                                      + governance_index, governance_document,
                                                  governance_guard_ack (admin POST, §5.7)
  forms.py                                      + governance_page_enabled in SiteSettingsForm.Meta.fields
templates/hub/
  governance_index.html                         NEW
  governance_document.html                      NEW
  governance_not_found.html                     NEW — the 404 body (§6.2). WITHOUT this file the copy in
                                                  §6.2 never ships: get_object_or_404 renders the
                                                  site-wide templates/404.html instead.
  partials/_governance_provenance.html          NEW — the provenance footer, one include, used on both pages
  base.html                                     + the Policies nav entry (BOTH blocks: the
                                                  request.view_as.is_admin block ~:208 and the else
                                                  block ~:349, next to help_page_enabled)
  admin/site_settings.html                      + the toggle (exclusion chain :184 + explicit include :461-462)
static/css/hub.css                              + one pl-gov-* block
plfog/settings.py                               + GOVERNANCE_REPO / _REF / _TOKEN / _SOURCE_DIR
render.yaml                                     + the buildCommand step
tests/membership/governance_document_spec.py            NEW
tests/membership/governance_policy_spec.py              NEW
tests/membership/management/sync_governance_docs_spec.py NEW
tests/membership/markdown_spec.py                       + the governance-profile block
tests/hub/governance_spec.py                            NEW
tests/membership/fixtures/governance_source/            NEW — a tiny fake org/ tree
```

**Why `governance_policy.py` is its own module and not inside the command.** Morlock's handoff says
`sync_docs.py` "is where the 'what is publishable' policy actually lives", and his `audit_snapshot_safety`
command had to import the filter *out of* `sync_docs` to replay it. Splitting the policy from the
orchestration up front means the guard and the redactions are importable, unit-testable without a
`Command`, and reusable by a future audit pass — without ever refactoring under pressure.

---

## 4. Data model

### 4.1 `GovernanceDocument`

Home: `membership/models.py`, near `WikiArticle` (the other read-only document store).

| Field | Type | Note |
|---|---|---|
| `slug` | `SlugField(max_length=200, unique=True)` | **Derived from the source path by exactly `sync_docs`' rule**: `re.sub(r"[^a-z0-9]+", "-", relpath[:-3].lower()).strip("-")`, so `policies/code-of-conduct.md` → `policies-code-of-conduct`. Byte-identical to his, which makes his `docs/INDEX.md` a valid crosswalk and lets a KB link `/doc/<slug>/` map 1:1 to `/governance/d/<slug>/`. Filled once by the sync; a renamed source file makes a *new* row and unlinks the old one (§5.6) rather than moving a live URL. |
| `title` | `CharField(300)` | From the first `# ` heading, run through the ported `clean_title` (strips `Role Explication:`, the `Past Lives Makerspace |` prefix, a trailing `v1.2`), falling back to a prettified file stem. `TITLE_OVERRIDE` is ported. |
| `register` | `CharField(20, choices=Register)` | `document` / `policy` / `role`. His fourth (`minutes`) is deliberately absent — see §5.3. |
| `doc_type` | `CharField(40, blank=True)` | `Policy`, `Bylaws`, `Role`, `Agreement`, `Glossary`, `Brand guide`, … Renders as quiet neutral text, never a pill. |
| `section_name` | `CharField(120)` | Sub-grouping inside a register, e.g. "Member policies", "Guild officer roles". |
| `section_emoji` | `CharField(8, blank=True)` | Ported from `SUBSECTION_META`. Decorative; the section name is always present as text. |
| `section_order` | `PositiveIntegerField(default=50)` | Ported. Unknown section → 50, which sorts it to the end rather than the top. |
| `status` | `CharField(20, choices=Status, blank=True)` | `canonical` / `adopted` / `draft` / `""`. Detected from the first 40 lines. **This is the one pill on the card.** |
| `version` | `CharField(40, blank=True)` | From YAML frontmatter `version:` (authoritative per PLM's document-standards §2.3), falling back to the visible `**Version:**` line. Ported verbatim — his older head-scan approach missed `**Version:** 1.0.0` and sometimes grabbed a version-history number. |
| `body_html` | `TextField(blank=True)` | Sanitized at sync time by the `governance` profile (§4.4). Stored, not rendered per request. |
| `search_text` | `TextField(blank=True)` | Frontmatter-stripped, markup-stripped, whitespace-normalized plain text. **Not truncated** — see §4.3. |
| `source_path` | `CharField(500)` | Provenance: the path relative to `org/`, e.g. `policies/facilities/fines-policy.md`. Shown in the footer. |
| `source_ref` | `CharField(120, blank=True)` | The git ref or commit SHA the content came from, from the tarball response. Shown in the footer. |
| `is_source_linked` | `BooleanField(default=True)` | `False` once the source file stops appearing in a sync. Never deletes. §5.6. |
| `min_role` | `CharField(20, choices=Role, default=Role.ADMIN)` | **The FOG-side visibility gate. NEVER written by the sync.** Default is the *top* rung, not `LEADERSHIP` — see §4.2, "Why the default is `ADMIN`". §5.5. |
| `synced_at` | `DateTimeField(null=True, blank=True)` | Set explicitly by the command on every successful write; a `--dry-run` never moves it. Rendered in the provenance footer. |
| `created_at` | `DateTimeField(auto_now_add=True)` | When FOG first learned of this document. |

Every field carries `help_text` per CLAUDE.md §3.

```python
class Register(models.TextChoices):
    DOCUMENT = "document", "Document Register"
    POLICY = "policy", "Policy Register"
    ROLE = "role", "Roles Register"

class Status(models.TextChoices):
    CANONICAL = "canonical", "Canonical"
    ADOPTED = "adopted", "Adopted"
    DRAFT = "draft", "Draft"

class Role(models.TextChoices):
    MEMBER = "member", "Any member"
    LEADERSHIP = "leadership", "Guild leadership & officers"
    ADMIN = "admin", "Admins only"
```

```python
class Meta:
    ordering = ["register", "section_order", "title"]
    indexes = [
        models.Index(fields=["min_role", "register", "section_order"], name="idx_govdoc_role_reg_order"),
    ]
```

> `idx_govdoc_role_reg_order` is 25 characters. Django's `models.E034` caps index names at 30 and it has
> bitten this repo before (PR #205) — **run `manage.py check` after the migration**, per brief §5.9.

`__str__` → `f"{self.title} ({self.get_register_display()})"`.

**Migration** is a plain `CreateModel` plus the index; the reverse is `DeleteModel`, generated. No data
migration, so no reverse function is needed. `SiteConfiguration.governance_page_enabled` and the
`SiteActivity.Kind` addition are separate `AddField` / `AlterField` migrations in `core`.

### 4.2 The tier mapping — FOG roles, not a second ladder

Morlock's ladder is `0 public / 10 member / 20 guild officer / 30 admin1 / 40 admin2 / 50 superuser`, stored
on a `Profile` one-to-one with `auth.User`. **His own handoff names this as the integration friction point:**
"FOG almost certainly has its own profile/membership model, so this needs reconciling — probably map FOG's
membership tier onto `permissions.py`'s ladder rather than keeping two profiles."

So: no `Profile`, no `level` integer, no `LEVEL_CHOICES`. FOG's roles are *named*, and `min_role` is a
`TextChoices` in FOG's own vocabulary. Ordering is expressed as **set membership**, not `<=`, which removes
the whole class of off-by-one ladder bugs.

| His level | FOG `min_role` | Who satisfies it, concretely |
|---|---|---|
| 0 public | *(deliberately absent)* | Nobody. Every document needs a login. |
| 10 member | `MEMBER` | A linked `Member`, **any `status`** (§1: reading is not editing), on a request that is not previewing as Guest. |
| 20 guild officer | `LEADERSHIP` | `view_as.is_guild_officer` (i.e. `fog_role` guild_officer or admin) **or** `Member.staffed_guilds.exists()` — `Guild.guild_lead` holder or any `GuildStaffMembership` row, one query, covers both — **and not previewing down** (below). |
| 30 admin1 / 40 admin2 / 50 super | `ADMIN` | `view_as.is_admin`. |

His 30/40/50 collapse to one FOG rung on purpose. His split exists because his box holds the historical
people register (646 contact records, former members, the waitlist's placement judgments) that FOG does not
mirror. Inventing a FOG `ADMIN2` to shadow a distinction that guards data we do not have is the second
parallel system, one migration later.

#### How wide is `LEADERSHIP`, really — and why the default is `ADMIN`

`LEADERSHIP` is deliberately **wider than Morlock's "guild leads and officers"**, and the spec says so
rather than hiding it. `GuildStaffMembership.Role` is `co_lead` / `secretary` / `treasurer` / `orienter`
**plus a free-text `custom_title`** (the model's XOR constraint requires exactly one), so
`staffed_guilds.exists()` is true for every orienter and every custom-title staffer on every guild.

That width is **correct for this codebase and is not narrowed**, because it is the house model, stated in
`GuildStaffMembership`'s own docstring: *"Every staff role grants the same authority as the guild lead."*
`can_edit_guild` already treats them identically. Minting a governance-only distinction between a co-lead
and an orienter would be a second, conflicting authority model for the same people — the exact mistake this
section opens by refusing.

**So the risk is closed at the default instead.** Before E5, run this on prod and write the number into this
spec, because "how many people is LEADERSHIP" is a question Josh must answer *before* publishing, not after:

```python
# DATABASE_URL="$PROD_DATABASE_URL" .venv/bin/python manage.py shell -c "..."
from django.db.models import Q
from membership.models import Guild, Member
leadership = Member.objects.filter(
    Q(pk__in=Guild.objects.exclude(guild_lead=None).values("guild_lead"))
    | Q(guild_staff_roles__isnull=False)
    | Q(fog_role__in=[Member.FogRole.GUILD_OFFICER, Member.FogRole.ADMIN])
).distinct()
print(leadership.count(), leadership.filter(guild_staff_roles__role="orienter").distinct().count())
```

**Measured population: _(TO FILL IN AT E5 — do not publish a document to `LEADERSHIP` before this number is
in this table)_.**

`min_role` therefore defaults to **`ADMIN`**, not `LEADERSHIP`. With a `LEADERSHIP` default, the moment the
flag flips all 59 documents become readable by that entire population with **no admin action on any
document** — and since the sync never writes `min_role` (§5.4), there is no later step at which anyone
reviews that. An `ADMIN` default makes publishing deliberate at *both* rungs: a document reaches guild
leadership only because someone moved it, and reaches the membership only because someone moved it again.
Four admins × 59 documents × one dropdown is a single afternoon on the `list_editable` changelist (§6.5),
and it is the afternoon in which the corpus actually gets read before it gets published.

#### Previewing down, and the trap in the house pattern

The house pattern E copies has a real hole, and E must not copy it. `can_edit_guild` resolves lead authority
through `_editing_member(request)`, which passes whenever `view_as.is_member` is true — **including for an
admin previewing as Member**. So an admin who is also a guild lead keeps lead authority while "previewing as
a member". For editing that is merely surprising; for E it would break the one thing §4.2 sells the preview
for — checking what is actually published before announcing it.

E therefore suppresses the `staffed_guilds` branch whenever the request is **previewing down**, defined as
`request.view_as.effective != request.view_as.actual`. `ViewAs.effective` equals `actual` when no role is
picked *or* when the picked role is the user's own highest, and is a strict subset only when the user has
deliberately stepped down — so this is exactly "you asked to see less". A plain-member guild lead (whose
`view_as_role` defaults to `member` and who has no dropdown at all) is unaffected, which is why the naive
test "`view_as_role` is at least `guild_officer`" is **wrong** and is not what E does.

**`AdminCapability` is not used here.** Every capability in the enum is an *approval duty*
(classes, spaces, discounts, events, billing, refunds, equipment); "may read the bylaws" is not one, and
minting a `GOVERNANCE` capability for a 59-document read-only mirror is a permission nobody would ever grant
separately. If a real need appears — an officer who should publish documents but not be an admin — it lands
as a capability then, in one place (`governance_roles`). Deferred, §10.

#### The resolver and the filter — `membership/permissions.py`

```python
def governance_roles(request: HttpRequest) -> frozenset[str]:
    """Which GovernanceDocument.Role rungs this request satisfies. Honors ``view_as``.

    Empty for anonymous users, users with no linked Member, and a request previewing as
    Guest — all of which then see nothing, which is the correct failure direction.

    Deliberately does NOT go through ``_editing_member``. That helper requires
    ``view_as.is_member``, and ``compute_actual_roles`` only grants ROLE_MEMBER to a
    Member whose ``status`` is ACTIVE — so a lapsed member resolves to ``{guest}`` and
    would be locked out of the membership agreement they are arguing about, against the
    §1 decision. Reading is not editing.
    """
    if not request.user.is_authenticated:
        return frozenset()
    view_as = getattr(request, "view_as", None)
    if view_as is None or view_as.view_as_role == ROLE_GUEST:
        return frozenset()
    member = getattr(request.user, "member", None)  # any status, deliberately
    if member is None:
        return frozenset()

    roles = {GovernanceDocument.Role.MEMBER}
    previewing_down = view_as.effective != view_as.actual
    if view_as.is_guild_officer or (not previewing_down and member.staffed_guilds.exists()):
        roles.add(GovernanceDocument.Role.LEADERSHIP)
    if view_as.is_admin:
        roles.add(GovernanceDocument.Role.ADMIN)
    return frozenset(roles)
```

Three properties worth stating because each is a test:

- **`view_as_role == ROLE_GUEST` is checked, not `is_guest`.** A lapsed member's `ViewAs` has
  `effective == {guest}` and `view_as_role is None` (`_highest_role` of an empty hierarchy set), so
  `is_guest` would exclude exactly the person §1 protects. Only an *explicitly picked* Guest preview
  returns nothing.
- **`staffed_guilds.exists()` is one query and only runs when the `fog_role` branch already failed** —
  Python's `or` short-circuits, so an officer or admin never pays for it.
- **`previewing_down`** is the guard described above. An admin previewing as Member gets `{MEMBER}` even if
  she leads three guilds.

```python
def visible_governance_documents(request) -> QuerySet[GovernanceDocument]:
    """Every GovernanceDocument this request may read. A FILTER, never a check.

    Views call this and render what comes back. A view that forgets to gate shows
    too little, never too much — the convention lifted straight from kb/permissions.py
    and already the house style in this module.
    """
    return GovernanceDocument.objects.filter(min_role__in=governance_roles(request))
```

There is **no** `can_read_governance_document(request, doc)` helper, on purpose. If a boolean check exists,
some future view will use it, and the first one that forgets to will leak.

**The one supported fetch** is `visible_governance_documents(request).filter(slug=slug).first()`, and
`GovernanceDocument.objects` must not appear in `hub/views.py` at all — one greppable rule, so a reviewer
can flag any other fetch as a defect. Note this is **not** `get_object_or_404`: that raises `Http404`, which
renders the site-wide `templates/404.html`, and §6.2's whole point is that a governance miss needs its own
body. See §6.2, "The 404 and how it is actually delivered".

**`view_as` behavior:** an admin previewing as Member sees exactly the `MEMBER` documents — including when
that admin is also a guild lead, per the previewing-down rule above. That is the point of the preview and the
cheapest way for Josh to sanity-check what he has published before announcing it.

### 4.3 Queryset

```python
class GovernanceDocumentQuerySet(models.QuerySet):
    def linked(self): ...        # is_source_linked=True
    def unlinked(self): ...      # is_source_linked=False — the admin to-do list
    def search(self, q: str):    # per-term AND icontains over title / search_text / section_name
```

`search` is a direct copy of `WikiArticleQuerySet.search`'s shape: split on whitespace, one chained
`.filter()` per term so terms AND rather than requiring a whole-string match, `none()` on empty `q`.
`icontains` only — **no Postgres FTS** (brief §4: local dev is SQLite and an FTS path would behave
differently in dev than in CI/prod).

**`search_text` is stored in full, not truncated to 2000 characters as his is.** His truncation means a
member searching for a term in §14 of the bylaws gets nothing. The corpus is 59 documents; the storage cost
is a rounding error and the bug is real. This is a deliberate, noted deviation.

**Model properties:**

- `get_absolute_url()` → `reverse("hub_governance_document", kwargs={"slug": self.slug})`
- `toc()` — one pass of `_HELP_TOC_HEADING_RE` over the *stored* `body_html`. Cheaper than
  `WikiArticle.toc()`, which re-renders on every call, because our HTML is already sanitized at rest.
  **This only returns anything because the `governance` profile runs the `toc` extension and allows an
  underscore in heading ids (§4.4).** `_HELP_TOC_HEADING_RE` is
  `<h([23])[^>]*\bid="([^"]+)"[^>]*>(.*?)</h\1>` — it requires an `id` and it matches **h2/h3 only**, so
  `toc()` lists two levels. h4 anchors still exist for deep links (the attribute filter allows them, matching
  `_allow_help_heading_attr`'s key set) but are deliberately not listed: a bylaws TOC that enumerated every
  h4 would be the thirty-chip scroller §6.4 rejects.
- `search_snippet(q, radius=90)` — the exact escape-then-wrap-in-`<mark>` algorithm from
  `WikiArticle.search_snippet`, over `search_text`. Escaping happens before the `<mark>` insertion, so the
  result is safe to `|safe` in the template.
- `status_label` / `status_tone` — the one pill. Blank status → no pill at all, not an "Unknown" pill.
- `is_stale` → `not self.is_source_linked`.

### 4.4 The `governance` markdown profile

A **new profile** in `membership/markdown.py`, joining `member` and `help`. Not the `help` profile, and not
a new renderer (brief §3: "Never introduce a second renderer").

**Why not reuse `help`:**

1. `help` allows **`iframe`** (Loom, YouTube-nocookie). An iframe is a full browsing context. The help
   profile earns that because a FOG admin authored the content in this repo; `org/` markdown is authored in
   a repo FOG does not control and does not review. This is the same reasoning the brief already applies to
   the wiki profile ("no `iframe` — that is the admin-authored help profile's privilege").
2. `help` allows **`img`** from `/static/help/`. Governance markdown cannot reference that tree, so the rule
   is dead weight and every image would be dropped by the src filter anyway.
3. `help` runs the **`admonition`** extension. `org/` markdown is not written with `!!! note` blocks;
   enabling it means an indented block somewhere in the bylaws could silently reinterpret as a callout.

**Why not reuse `member`:** it opens *every* link in a new tab, including the internal cross-document links
we are about to create, and it emits no heading `id` anchors — but note that **neither does `help`, for this
corpus.** See the next block; that misconception is what broke the first draft of this spec.

**Images do not degrade to their alt text — they disappear.** `bleach.clean(..., strip=True)` removes the
`<img>` element, and `alt` is an attribute, not content: `![Org chart](chart.png)` sanitizes to `<p></p>`
(verified empirically 2026-09-07). No profile choice changes this; only allowing `img` would, and E does not.
So an org chart embedded in a policy leaves **no trace on the page at all**. That is acceptable — the
alternative is proxying private-repo binaries — but it must not be silent: the sync emits a
`NOTE (image dropped)  policies/x.md — 1 image` line per affected file and counts them in the summary block
and the `SiteActivity` payload (§5.7), so an admin can go add the missing figure as a linked upstream note.

#### The heading-anchor problem, stated plainly

The `help` profile's anchors do **not** come from the renderer. `_MEMBER_EXTENSIONS` is
`["extra", "sane_lists", "tables"]` and `_HELP_EXTENSIONS` adds only `admonition` — **nothing in either list
generates heading ids.** Every anchor in a help article exists because a human hand-wrote `{#slug}` attr_list
syntax in `membership/help_content.py`. `org/` markdown was authored against Morlock's `mdconv`, which
generates its own anchors, so it will carry **zero** `{#…}` markers. Reusing the help extension list
unchanged would give `toc()` an empty list for all 59 documents, and §6.2's TOC, §6.4's `<details>`
disclosure, and the "why not reuse `member`" argument above would all describe an element that never renders.

So the `governance` profile adds the **`toc` extension**, and that immediately collides with the sanitizer:

```
>>> markdown.markdown("## Purpose\n\n## Purpose\n", extensions=["extra","sane_lists","tables","toc"])
'<h2 id="purpose">Purpose</h2>\n<h2 id="purpose_1">Purpose</h2>'
```

python-markdown dedupes a repeated heading with an **underscore** (`unique()` formats `"%s_%d"`; it is not
configurable), and `_HEADING_ID_PATTERN` is `^[a-z0-9-]{1,80}$` — **no underscore** — so `_allow_help_heading_attr`
drops those ids, bleach strips them, and the heading silently vanishes from the TOC with a dead anchor
pointing at it. PLM's bylaws repeat "Purpose", "Definitions" and "Notes" in every article. This is the
failure that must be pinned by a test, not by a comment.

The profile therefore gets **its own** id pattern and filter, next to the help ones and never replacing them:

```python
# Same shape as _HEADING_ID_PATTERN, plus the underscore python-markdown's toc extension
# uses to disambiguate repeated headings ("purpose", "purpose_1"). Governance docs repeat
# section titles constantly; without the underscore those anchors are stripped and their
# TOC entries disappear.
_GOV_HEADING_ID_PATTERN = re.compile(r"^[a-z0-9_-]{1,80}$")


def _allow_gov_heading_attr(tag: str, name: str, value: str) -> bool:
    """Bleach attribute filter for governance headings: pattern-valid ``id`` only."""
    return name == "id" and bool(_GOV_HEADING_ID_PATTERN.match(value))
```

`_HEADING_ID_PATTERN` and `_allow_help_heading_attr` are **not touched** — the help profile keeps its
stricter pattern, and its golden files keep passing untouched, which is the point of a separate filter.

**The profile:**

| Aspect | Value |
|---|---|
| Extensions | `[*_MEMBER_EXTENSIONS, "toc"]` — `extra`, `sane_lists`, `tables`, `toc`. Tables matter (the policy register and the pricing guide are tables); `toc` is what makes §6.2's TOC exist at all. |
| Extension config | `{"toc": {"marker": "", "toc_depth": "2-4"}}`. `marker: ""` **disables the `[TOC]` placeholder**: left enabled, a stray `[TOC]` in upstream markdown emits a `<div class="toc">` whose `div` bleach strips while keeping the inner `<ul><li><a>`, i.e. a duplicate link list injected into the page body. With it disabled a literal `[TOC]` renders as the text `[TOC]` — visible, harmless, and fixable upstream. `render_markdown` currently passes no `extension_configs`; the governance branch adds it. |
| Tags | `_ALLOWED_TAGS` exactly. No `img`, no `div`, no `iframe`. |
| Attributes | `_ALLOWED_ATTRS` plus `id` on `h2`/`h3`/`h4` via the **new** `_allow_gov_heading_attr` above. |
| Links | New `_harden_link_gov`: internal `/`-relative and `#` links stay same-tab with `rel="noopener"` (like `_harden_link_help`); **a `docs.google.com` document/forms/spreadsheets href is de-linked** (below); everything else gets the full member hardening (`rel="noopener nofollow noreferrer" target="_blank"`). |

**Cross-document links — a decision the brief left open, and it matters.** The corpus links between its own
documents with relative markdown paths: `](interviewing-framework.md)`, `](../document-standards.md)`,
`](facilities/trash-recycling-policy.md)`, `](policy-register.md)` — 30-odd of them. Rendered naively those
become dead `href="interviewing-framework.md"` links inside the hub. The sync therefore does a **link-resolve
pass** (in `governance_policy.py`, before rendering):

- A relative `*.md` target is resolved against the document's own `source_path`, slugified with the same
  rule, and rewritten to `/governance/d/<slug>/` **if and only if** that slug is in the current sync batch.
- If the target is not in the batch (excluded, guarded, or archived), the link is **stripped to its label
  text**. Fail-closed: a member never clicks through to a 404, and we never emit a link to a document we
  deliberately did not publish.
- Visibility is *not* consulted here — the target page's own gate handles that. The anchor text is the
  document author's own prose in a document the reader is already cleared for; the mirror is not inventing a
  disclosure, and per-reader body HTML would mean rendering per request. Noted so a reviewer sees it was
  decided, not missed.
- `mailto:` survives (bleach's default protocol allowlist).

**Google Docs / Drive links are de-linked, and a markdown-level rule is not enough to do it.** The rule: a
`docs.google.com/document|forms|spreadsheets` (and `drive.google.com/file`) target becomes plain text, label
kept. Reason: a Google share link is an unauthenticated capability URL one misconfiguration away from public,
and the corpus's Google Doc references are almost always *superseded originals* — exactly the class of
content behind his v1.45.0 incident, where `provenance:` Drive IDs rendered onto the public Code of Conduct
page. `calendar.google.com` embeds are kept: legitimately useful, no document body behind them.

Doing this in the link-resolve pass alone leaves **two live escapes**, both verified against this repo's
pipeline on 2026-09-07:

1. **A bare URL in prose is not link syntax.** `See https://docs.google.com/document/d/1AbC/edit for details.`
   passes through markdown and bleach as text, and then `bleach.linkify` — which every profile calls last —
   turns it into a live anchor:
   `<a href="https://docs.google.com/document/d/1AbC/edit" rel="noopener nofollow noreferrer" target="_blank">`.
2. **A hand-written anchor survives the sanitizer.** `_ALLOWED_ATTRS` allows `a: ["href", "title"]`, so
   `<a href="https://docs.google.com/…">the doc</a>` written as raw HTML in the markdown reaches the page
   intact.

Frontmatter stripping (§5.2) covers `provenance:` lines and nothing else, so neither escape is closed by it.
The rule therefore lives **in `_harden_link_gov`**, which `bleach.linkify` invokes for *both* author-written
anchors and auto-linked bare URLs — one seam, both escapes, after everything else has run:

```python
_GOV_DELINK_HOSTS_PATHS = (
    "docs.google.com/document", "docs.google.com/forms",
    "docs.google.com/spreadsheets", "drive.google.com/file",
)

def _harden_link_gov(attrs, new=False):
    href = attrs.get((None, "href"), "")
    if _is_google_doc_url(href):
        # Signal "drop this anchor, keep its text" — bleach.linkify removes an anchor
        # whose callback returns None, leaving the label behind.
        return None
    ...
```

Returning `None` from a linkify callback is bleach's own documented "unlink this" contract, so no
post-sanitize regex pass over the HTML is needed. **Fixture required:** one file whose body contains a bare
`https://docs.google.com/document/d/…` in running prose *and* a hand-written `<a href="https://docs.google.com/…">`,
asserting neither produces an `<a>` in `body_html` and both labels survive.

**Post-sanitize step:** exactly one, and it adds no tags to the allowlist — the table scroll wrapper in §6.4,
which wraps *already-sanitized* output and never runs anything back through bleach.

**Template filter:** add `governance_markdown` to `membership/templatetags/membership_md.py` for symmetry,
but the document page renders the *stored* `body_html` with `|safe` (it was sanitized at rest by the same
code path). The filter exists for the admin preview.

---

## 5. Business logic

Two importable modules plus a thin `Command`. `sync_docs`' policy is ported; its orchestration is not.

### 5.1 `membership/governance_source.py` — materializing the tree

```python
@dataclass(frozen=True)
class GovernanceSource:
    root: Path       # the extracted org/ directory
    ref: str         # the resolved ref or commit SHA, for provenance

def fetch_source(*, repo: str, ref: str, token: str) -> Iterator[GovernanceSource]:
    """Context manager. GETs the GitHub tarball, extracts to a TemporaryDirectory, yields, deletes."""
```

- One `httpx.get("https://api.github.com/repos/{repo}/tarball/{ref}", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, follow_redirects=True)`.
- Extract with `tarfile.extractall(filter="data")` — Python 3.13, path-traversal-safe by construction. An
  archive is untrusted input even from a repo we trust.
- The tarball's top level is a single `<owner>-<repo>-<sha>/` directory; `ref` is read from that name, which
  is the commit SHA — better provenance than the branch name.
- The `org/` root inside the repo is the repo root itself (his `sync_docs` reads `PLM_HUB/org`; the separate
  repo *is* that tree). A `GOVERNANCE_REPO_SUBDIR` setting, default `""`, absorbs the difference if the
  layout turns out otherwise. **This is the one fact I could not verify without repo access** (§10).
- Non-200 → `GovernanceSourceError` naming the status and the repo. 404 is called out specially, because
  404 is what GitHub returns for "no access" as well as "no such repo" and that ambiguity has cost people
  hours.
- **Nothing private ever touches the repo working tree or a persistent disk.** The temp dir is deleted on
  every exit path.

`--source <path>` bypasses the fetch entirely (local dev against a checkout, and how the test fixtures run).

### 5.2 `membership/governance_policy.py` — the ported publish policy

This is the port Morlock flagged as the most important file. Ported **verbatim in behavior**, restructured
into pure functions:

| Ported | Notes |
|---|---|
| `SKIP_DIRS` | `_private`, `archive`, `archives`, `.git`, `member-handbook-review`, `source`, `board`, `notes`, `finance`, `meeting-minutes` |
| `EXCLUDE_FILES` | `todo.md`, `readme.md`, `tour-cancel-backlog.md`, `leadership-comp-strategy.md`, `partner-register.md`, `vendor-register.md`, `software-register.md`, plus any `*.CHANGELOG.md` |
| `SENSITIVE` | SSN-shaped `\b\d{3}-\d{2}-\d{4}\b`, and `\bVenmo\b` (case-insensitive) |
| `GUARD_ALLOW` | `fiscal-sponsorship/donation-protocol-cash.md`. **His second entry, `meeting-minutes/guild-food-independence.md`, is ported as a commented-out line, not as live data** — `meeting-minutes` is in `SKIP_DIRS` here (§5.3), so the path is unreachable and a live entry would read as an allow-list decision nobody made. The comment records that it existed upstream, so a future reviewer diffing against `sync_docs` sees it was dropped on purpose. |
| `REDACTIONS` | Path-keyed replace list, plus the global Google-Docs-href rule (§4.4) |
| `STAFF_ROLES`, `WORK_TRADE_ROLES`, `SIGNED_AGREEMENTS`, `TITLE_OVERRIDE` | verbatim |
| `REGISTER_META`, `SUBSECTION_META` | minus the `minutes` register (§5.3) |
| `classify(relpath)` | verbatim, minus the `meeting-minutes/` and `finance/` branches |
| `_strip_frontmatter`, `clean_title`, `parse_version`, `detect_status`, the slug rule | verbatim |

**`_strip_frontmatter` lives here, not in `membership/markdown.py`.** It is a property of *this corpus's
source format*, not of rendering. Putting it in `markdown.py` would imply every profile strips leading
`---`, which would be wrong for a member-authored wiki page that opens with a horizontal rule.

The pipeline, in order — **the order is load-bearing and each step is a test**:

```
read → apply_redactions → sensitive_guard → archived_check → strip_frontmatter
     → parse (title / version / status) → classify → resolve_links → render
     → wrap_tables → build search_text
```

- **Redactions run before the guard**, matching his code: a redaction can *remove* the thing that would
  otherwise trip the guard, and a document that trips the guard for text we have already agreed to redact
  is a false skip.
- **Both the guard and the ARCHIVED check run before rendering**, so a guarded document's body never reaches
  the renderer at all.
- **Frontmatter is stripped for both `body_html` and `search_text`** — the second half of his v1.45.0 fix.
  A `provenance:` line that is invisible on the page but present in the search index surfaces as a result
  snippet, which is the same leak with an extra hop.
- **The ARCHIVED check** looks at the title *and* the first 200 characters, matching his. His changelog
  documents the known residual gap (a `.gitignore` matches paths, not content) and it applies here too.
- **`wrap_tables` is the one step with no counterpart upstream** and it is deliberately *last*, after
  rendering: it wraps each `<table>` in the already-sanitized HTML in a keyboard-scrollable `role="region"`
  container (§6.4). It runs on output we produced, never back through bleach, and `search_text` is built from
  the pre-wrap text so the wrapper markup never enters the search index.

**Fail-closed classification.** `classify()`'s final fallback returns
`("document", "Governance & legal", "Document")` — a real register and section, never a special "unknown"
bucket, and the *row still defaults to `min_role=ADMIN`* (§4.2), so an unclassified document is invisible to
everyone but admins until published by hand. There is no code path by which a new file in `org/` becomes
visible to a member, or to guild leadership, without a person acting on that document.

### 5.3 What is excluded at the source, not tier-gated

- **`finance/`** — his `SKIP_DIRS` excludes it because `sync_finance` owns it. Ours excludes it because FOG
  has no business mirroring financial statements at all. The sensitive guard is a backstop, not a policy;
  the policy is "don't fetch it".
- **`meeting-minutes/`** — FOG has `Meeting`. Two minutes stores is the merge mistake the brief forbids
  (§2). His `minutes` register and its `MIN-###` IDs do not exist in FOG.
- **`_private/`, `board/`, `notes/`, `archive*/`** — ported verbatim.

An exclusion is one line in a constant and a test. A tier is a decision someone can get wrong later.

### 5.4 The command — `sync_governance_docs`

```
manage.py sync_governance_docs [--dry-run] [--source PATH] [--ref REF] [--skip-if-unconfigured] [--purge-unlinked]
```

| Flag | Behavior |
|---|---|
| `--dry-run` | Report every decision; write nothing; `synced_at` untouched. Same contract as `seed_help_center --dry-run`. |
| `--source PATH` | Sync a local directory instead of fetching. Local dev + fixtures. |
| `--ref REF` | Override `GOVERNANCE_REPO_REF`. |
| `--skip-if-unconfigured` | With no `GOVERNANCE_REPO`/`GOVERNANCE_REPO_TOKEN`, print `governance sync not configured — skipping` and exit **0**. **Used by exactly one caller: `render.yaml`'s `buildCommand`**, where a hard failure would break every deploy before the token exists. |
| `--purge-unlinked` | Hard-delete rows whose source vanished. **Never in the UI, never in any scheduled or build invocation.** §5.6. |

#### Why the flag is inverted from the obvious design

The obvious design — an `--if-configured` flag that the scheduled job also passes — is wrong twice, and both
faults are the kind that ship green.

**1. The registry cannot pass a flag at all.** `ScheduledJob` is a frozen dataclass with no args field, and
*both* dispatch sites call `call_command(job.command)` — `core/management/commands/run_scheduled_tasks.py`
and `_handle_run_job` in `hub/views.py`. `call_command` treats the whole string as a command **name**, so
`command="sync_governance_docs --if-configured"` raises `CommandError: Unknown command` on both the nightly
run and the Run-now button. All 23 existing registry rows pass a bare name. E does not add an `args` field
for one job: threading a new field through two dispatchers and the dashboard is a change to shared
infrastructure that every other job then has to be re-read against, for a problem the default fixes.

**2. Skipping quietly on a schedule is the exact failure this spec quotes as its motivation.** `record_run`
marks a run **OK** whenever the body does not raise, and `ScheduledTaskRun` has only RUNNING / OK / FAILED
plus an `error` string — there is no "note" field to write a caveat into, and `_activity_feed.html` would not
render one anyway. A nightly job that exits 0 while syncing nothing paints a green row on the Automations
dashboard for however many weeks pass before `org/` access lands. That is *"it exits 0 either way"* (§5.7)
reproduced one layer up, in the surface built to detect it.

**So: unconfigured is loud by default.** A bare `manage.py sync_governance_docs` with no `GOVERNANCE_REPO`
raises `CommandError` naming exactly which variable is unset; `record_run` catches it, marks the run FAILED,
and the dashboard shows a red row reading *"governance sync not configured: GOVERNANCE_REPO is unset"* — the
truth, in plain language, in the place an admin looks. This also means a **rotated or revoked token goes red
the same night** rather than silently freezing the corpus, which is the failure mode that actually matters
after launch.

**And the registry row ships in E5, not E2** (§8). Registry jobs are enabled by default —
`ScheduledJobState.objects.is_enabled` returns `True` when no row exists — so registering the job before the
token exists would paint that red row nightly for weeks. Registering it alongside `render.yaml`'s env vars,
in the same phase, means the job appears on the dashboard at the moment it can succeed. Nothing else in E1–E4
depends on the registry row.

#### Non-negotiable safety properties

Each of these is a real incident in his `CHANGELOG.md`, not a hypothetical.

**1. Refuses to run on an empty or missing source tree.** (v1.45.1 — a bare run removed 50 documents and the
recreate reshuffled 27 stable `reg_id`s, reusing DOC-053/054 for different documents; nothing in the command
objected, and it was recovered from a pre-deploy backup.)

```
CommandError: No markdown found under <resolved path> — refusing to sync, because that would
unlink every governance document. GOVERNANCE_REPO is <set|UNSET>, ref <ref>.
Re-run with a real source:  manage.py sync_governance_docs --source /path/to/org
```

The check is `not root.is_dir() or not any(root.rglob("*.md"))`, evaluated **before any write**. Zero source
files is never a legitimate state.

**2. `min_role` is never written by the sync.** (His `Document.min_level` carries the comment "If this ever
lands in `sync_docs`' defaults, every content push silently republishes gated documents to everyone", and a
test enforces it.) Mechanically:

```python
SYNCED_FIELDS: Final[frozenset[str]] = frozenset({
    "title", "register", "doc_type", "section_name", "section_emoji", "section_order",
    "status", "version", "body_html", "search_text", "source_path", "source_ref",
    "is_source_linked", "synced_at",
})
```

The `defaults=` dict is **built from `SYNCED_FIELDS`**, and two tests guard it: a structural one
(`"min_role" not in SYNCED_FIELDS`) and a behavioral one (set a row to `MEMBER`, re-sync, assert it is still
`MEMBER`). The structural test is the one that survives a refactor.

**3. Defaults are fail-closed.** Covered in §5.2. Restated because it is the property a reviewer should check
first: *there is no input to this command that produces a member-visible document.*

**4. Never cascade-deletes.** §5.6.

**5. `--dry-run` and a written report.** §5.7.

**6. A `SKIP (guard)` is loud.** §5.7 — this one gets its own mechanism, because his guard once silently
dropped the entire finance register while the command exited 0.

#### The write

```python
GovernanceDocument.objects.update_or_create(slug=slug, defaults={...SYNCED_FIELDS...})
```

`update_or_create` on `slug`, exactly the `seed_help_center` idiom. Correcting a document upstream and
re-running is the intended workflow.

### 5.5 Publishing — the only FOG-owned decision

`min_role` starts at `ADMIN` (§4.2) and is changed **only** in the Django admin (§6.5). That is deliberate:

- 59 documents, four admins, and a decision made once per document. A bespoke hub publishing screen is
  gold-plating for that shape.
- Django admin gives the audit trail (`LogEntry`) for free, and "who published the fines policy to every
  member" is exactly the question someone will ask.
- Every other field on the admin is read-only, so the admin cannot become a back door into editing content
  that git owns.

**But an admin has to be able to find it.** A publishing screen with no route from the product is a screen
nobody uses, and the hub gives no hint that `/admin/membership/governancedocument/` exists. So the register
index's admin-only region (§6.1) carries a plain link — **"Manage who can read these"** → the changelist —
next to the guard and unlinked banners. It is one anchor, admin-only, and it is the difference between "the
admin does the job" and "the admin would have, if she had known where".

### 5.6 Stale documents — kept, never deleted, never hidden

(His v1.11.x data-loss guard: `sync_docs` used to cascade-delete a document *and its comments, proposed
edits, and approval history* when its source markdown was renamed or moved.)

A `linked()` row whose `source_path` stops appearing in a sync:

1. `is_source_linked = False`. Nothing else changes — **not `min_role`, not `body_html`**.
2. It **stays visible to exactly whoever could already see it.** Hiding it would make a policy silently
   disappear from under a member mid-argument, which is the same failure as deleting it, one step quieter.
3. The provenance footer gains one quiet line: *"This document's source file is no longer in the governance
   repo (as of 4 Mar 2026). It is kept here until an officer retires it."*
4. It appears in the admin's `unlinked()` filter and in the admin-only banner on the register index — the
   to-do list.
5. Deletion happens only via `--purge-unlinked`, run by hand, never from `render.yaml` or the scheduler.

FOG has no comments or approval history to lose, so the *reason* for his guard is weaker here — but the
member-facing reason (a policy must not vanish) is stronger, and a rename upstream is common.

### 5.7 The report, and making a guard skip impossible to miss

His v1.11.0 note is the sharpest warning in the whole changelog: *"the `SENSITIVE` guard in `sync_docs`
silently skipped the register on first sync… If a finance doc goes missing from the KB, check the sync output
for `SKIP (guard)` before assuming the sync ran clean — it exits 0 either way."* A warning in a log nobody
reads is not a control. So, three layers:

1. **stdout.** Every decision, in `seed_help_center`'s house voice, with guard skips in
   `self.style.WARNING`:

   ```
     SKIP (excluded)   operations/todo.md
     SKIP (archived)   governance/bylaws-2023-ARCHIVED.md
     SKIP (guard)      policies/reimbursement.md — matched: Venmo
     NOTE (image dropped) policies/space-layout.md — 2 images removed (no image support)
     KEPT (source gone) policies/old-fines-policy.md — marked unlinked
   Synced from Past-Lives-Makerspace/org@a1b2c3d:
     12 new, 44 updated, 3 unlinked, 2 skipped by the content guard, 7 excluded, 2 images dropped.
     Guarded: policies/reimbursement.md, operations/petty-cash.md
     Images dropped: policies/space-layout.md
   ```

   The guarded paths are repeated in the summary block, so a truncated CI log still shows them. The
   image-dropped lines exist because the sanitizer removes an `<img>` **and its alt text** (§4.4) — without
   the line, a figure disappears from a policy with no record anywhere that it was ever there.

2. **`SiteActivity`.** One `GOVERNANCE_SYNCED` row per run, payload
   `{created, updated, unlinked, excluded, guarded: [paths], images_dropped: [paths], ref}`. `actor=None`
   (system). This makes the run queryable.

   > **`SiteActivity` is not, by itself, an admin-visible audit surface for this.**
   > `templates/hub/admin/_activity_feed.html` renders `get_kind_display`, the actor email, the timestamp,
   > and an optional email badge — **it never renders `payload`**. An admin scrolling the feed sees
   > "Governance documents synced · System · Sep 7, 6:04 AM" and nothing else. The payload is for querying
   > (a shell, or the banner below); if the feed row should carry a summary, that is a change to
   > `_activity_feed.html` (`{% if a.payload.guarded %}` → one muted line), and it is **out of E's scope**
   > unless Josh wants it. §7 restates this so nobody plans against a feed that does not show the data.

3. **In the product.** The register index shows an **admin-only** banner when the last sync guarded anything:
   *"2 documents were held back by the content guard on the last sync (7 Sep, 6:04 AM): `policies/reimbursement.md`,
   `operations/petty-cash.md`. Reword them upstream, or add them to `GUARD_ALLOW` if the match is a false
   positive."* — plus a **"Got it"** button. A register that silently vanishes is now visible in the UI, not
   only in a log.

   **It must be dismissible, and it must not be a standing nag.** A guard trip is not necessarily a bug: the
   reference implementation's own resolution for a legitimate trip was to *reword the document upstream*
   rather than allow-list it, which means a document can sit guarded for weeks while everyone involved
   already knows. A banner computed from the last sync's payload would then render on every page load for
   every admin, forever — and a permanent banner is an ignored banner, including on the day the guarded set
   changes and it actually means something.

   So the banner renders only when the guarded set **differs from an acknowledged set**:

   - `SiteConfiguration.governance_guard_ack` — `TextField(blank=True, default="")`, the newline-joined
     acknowledged paths. One more field on a model that already holds every other site-wide toggle.
   - Render the banner when `sorted(last_run_payload["guarded"]) != sorted(ack.splitlines())`.
   - **"Got it"** POSTs to `governance/guard-ack/` (`_require_admin`, CSRF as normal), which writes the
     current guarded list into the field and redirects back. A *newly* guarded document re-raises the banner
     the next morning, because the sets differ again.
   - A guard trip that clears upstream also makes the sets differ; the banner then reads the new (shorter,
     possibly empty) set, which is correct — "this changed, look again" is the whole signal.

   The banner itself is **not linked** anywhere: neither Site Settings → Automations nor the changelist can
   fix a guard trip, and a link that resolves nothing teaches admins to ignore banners. The *unlinked*
   banner **is** linked, to `admin:membership_governancedocument_changelist?is_source_linked__exact=0` — that
   filter is the actual to-do list, and retiring a row is a real action available on that screen.

Deliberately **not** done: exiting non-zero on a guard trip. A legitimately guarded document would then break
every deploy, and the pressure to add it to `GUARD_ALLOW` just to get a release out is exactly how a guard
gets disarmed.

### 5.8 Scheduling

One row in `core/scheduled_jobs.SCHEDULED_JOBS`, **added in phase E5** (§5.4, "Why the flag is inverted"):

```python
ScheduledJob(
    key="sync_governance_docs",
    name="Governance document sync",
    description="Mirrors the makerspace's policy and governance documents from the org repository.",
    command="sync_governance_docs",   # a BARE command name — call_command(job.command) takes a name,
                                      # not a command line. No flags here, ever.
    schedule_label="Nightly ~6 AM",
    cadence=Cadence.DAILY,
)
```

`command` is a bare name because that is all the dispatchers can accept: both
`run_scheduled_tasks` and `hub/views._handle_run_job` call `call_command(job.command)`, and every one of the
23 existing rows passes a bare name. Any flag in that string is a `CommandError: Unknown command` at run
time, on the nightly job *and* on the Run-now button.

That gets the job into the Site Settings → Automations dashboard with run history, an enable toggle, and a
**"Run now"** button — which is the actual correction path an admin uses ten minutes after Morlock pushes a
policy change, and the reason the provenance footer can honestly say "then re-synced". Because the command is
loud when unconfigured (§5.4), a missing or revoked token shows on that dashboard as a **FAILED** run with
the reason in the error column, never as a green run that synced nothing.

---

## 6. UI / UX

Five screens. This surface is read-only, which raises the bar rather than lowering it: there is no form to
hide behind, so the states, the theming, and the empty cases *are* the feature.

### 6.0 First, the architectural question this section depends on: where `org/` comes from

Four options, weighed against Render's build/runtime model (`render.yaml`) and the fact that both repos'
visibility matters — **plfog is MIT-licensed and public**; `org/` is private.

| Option | Verdict |
|---|---|
| **(a) Git submodule** | **Rejected.** Render clones the linked repo with its own credentials and does not authenticate a *second* private repo, so a private submodule fails the build. Worse: it would place private governance markdown inside a public repo's working tree on every checkout, one `git add -A` from a permanent leak — and this repo already has a hard-won lesson about verifying what actually landed in a commit. And every content change would need a plfog commit. |
| **(b) Deploy-time clone with a read-only deploy key** | **Rejected as primary; kept as fallback.** It works, but it needs SSH key material written into `~/.ssh` in `buildCommand` on every service that syncs, and the clone lands in the app working directory (same leak surface as (a) for local dev). Fatally for the daily refresh: a Render cron's `buildCommand` runs at *deploy* time, not on each scheduled invocation, so the clone would be frozen at the cron's last deploy and "daily sync" would sync the same snapshot forever. |
| **(c) Checked-in export** | **Rejected, hard.** plfog is public and MIT. 55 of 59 documents are officer-gated upstream. Committing them is the exact leak the sensitive guard exists to prevent, made permanent by git history. |
| **(d) Payload fetched at sync time** | **Recommended.** Two sub-variants: a tarball from the GitHub API, or an object Morlock's CI pushes to R2. |

**Recommendation: (d), as a GitHub tarball.** `httpx.get` against
`https://api.github.com/repos/<owner>/org/tarball/<ref>` with `Authorization: Bearer <token>`, extracted to a
`TemporaryDirectory` with `tarfile.extractall(filter="data")`, synced, discarded.

Why this one:

- **Identical behavior everywhere** — `buildCommand`, the daily cron *at run time*, a Render one-off job,
  and local dev. No assumptions about which filesystem survives which phase. This is what kills (b).
- **Nothing private ever lands on disk persistently**, in a repo working tree, or in the public plfog repo.
- **It needs access to be granted, not key material to be installed.** A fine-grained GitHub PAT scoped to
  Contents: Read on that one repository — the smallest possible grant, revocable from a web page, and
  nothing on Morlock's side to build or maintain. Compare the R2 variant, which needs him to write and keep
  an exporter.
- **No `git` binary assumption** at cron runtime.
- **`respx` mocks it**, which is this repo's declared HTTP mocking tool (CLAUDE.md §7: "`respx` for HTTP
  mocking, not `responses` or `httpretty`"). The R2 variant would test through `django-storages`, which is
  more machinery for the same assertion.
- The commit SHA falls out of the tarball's top-level directory name, giving real provenance for free.

**Settings + secrets** (`plfog/settings.py`, all `os.environ.get` per house style):

| Var | Default | Where it must be set |
|---|---|---|
| `GOVERNANCE_REPO` | `""` (blank ⇒ feature off) | Render **web** service **and** the **`run-scheduled-tasks`** cron |
| `GOVERNANCE_REPO_REF` | `"main"` | optional; same two services |
| `GOVERNANCE_REPO_TOKEN` | `""` | **the secret** — fine-grained GitHub PAT, Contents: Read, single repo. Same two services. `sync: false` in `render.yaml` |
| `GOVERNANCE_REPO_SUBDIR` | `""` | in case the tree is nested under `org/` inside the repo (§10) |
| `GOVERNANCE_SOURCE_DIR` | `""` | local dev convenience; `--source` overrides |

`render.yaml` gains one `buildCommand` line after `seed_help_center`:

```
python manage.py sync_governance_docs --skip-if-unconfigured &&
```

Until the token exists, every deploy prints `governance sync not configured — skipping` and exits 0. This is
the **only** invocation that gets that flag (§5.4).

**The env vars go in two blocks, not one.** The `run-scheduled-tasks` cron is a separate Render service with
its own `envVars` list, and today that list contains only `PYTHON_VERSION`. The nightly sync runs *there*, so
`GOVERNANCE_REPO` / `_REF` / `_TOKEN` declared solely on the web service would leave the cron unconfigured —
and, with the loud default (§5.4), red every night while every deploy-time sync succeeded. Declare them on
both, and check the cron block specifically in review: it is the one that looks finished because it already
has an `envVars:` key.

> **Never give these a `previewValue`.** The web service carries `previews: generation: automatic`, so every
> pull request on this **public, MIT-licensed** repo spins up a preview environment that runs `buildCommand`.
> A `previewValue` on `GOVERNANCE_REPO_TOKEN` would pull all 59 documents — 55 of them officer-gated
> upstream — into a throwaway database created by a PR from anyone. `sync: false` with no `previewValue` is
> the correct shape: previews get a blank value, the build prints the skip line, and no private content is
> ever fetched outside production.

### 6.1 Screen 1 — Register index

- **Template:** `templates/hub/governance_index.html`
- **Route:** `governance/` → `hub_governance_index`. `@login_required`. Two early guards, in order:
  ```python
  if not SiteConfiguration.load().governance_page_enabled:
      return redirect("hub_home")          # the exact help_page shape (hub/views.help_page)
  if not governance_roles(request):
      return redirect("hub_home")          # no linked Member, or previewing as Guest
  ```
  The second guard is what keeps a **logged-in non-member** off this page. FOG has users who are not
  members — a class registrant has a `User` and no `Member` — and without it they would land on "No policies
  have been published to members yet", which is a false statement about the corpus rather than a true one
  about them. Redirecting is better than a third empty state: there is nothing on this page for them and
  nothing they can do about it.
- **Layout:** dedicated page, full width, no aside. Not a modal (this is a reading destination), not tabs
  (three registers is a scroll, not a tab bar, and a tab bar hides two thirds of the corpus from search-
  by-eyeball).
- **Chrome:** `{% include "components/page_header.html" with title="Policies & Governance" %}`. Under it,
  one sentence of orientation: *"The makerspace's official policies, bylaws, and role descriptions. These
  are mirrored from the governance repository and can't be edited here."* No breadcrumb — this is a
  top-level page.
- **Search:** `{% include "components/table_search.html" with action=... placeholder=... preserved_fields=... %}`
  in a `hub-card`, GET to `{% url 'hub_wiki_search' %}`, so the single search box of brief §2 is what a member
  actually uses. `source=governance` rides in through the component's own **`preserved_fields`** parameter
  (a list of `(name, value)` pairs it renders as hidden inputs) — named explicitly here because the obvious
  alternative, hand-writing an `<input type="hidden">` next to the include, would sit outside the component's
  `<form>` and silently do nothing. There is **no** `hub_governance_search` fallback (§2.1).
- **Body:** one `hub-card` per register, in `Register` order (Document / Policy / Roles). Inside a card,
  rows are grouped by `section_name` via `{% regroup %}` — which only groups *adjacent* rows, so the
  queryset must be ordered `("register", "section_order", "title")`, mirroring the lesson already encoded in
  `HelpCategoryQuerySet.landing_ranked`. Each section header is the emoji + the name, Title Case (Rule 22).
- **The row** (`.pl-gov-row`) — and this is the "one pill" contract from brief §3:
  - the title, an `<a>` to the document
  - **exactly one status pill**: `Canonical` / `Adopted` / `Draft`. Blank status → *no pill*.
  - everything else is **quiet neutral text**, never a colored chip: `Policy · v1.0.1 · Updated 12 Aug 2026`
  - `Unlinked` appears as neutral text too, not a fourth pill.
- **Admin-only region**, rendered once above the register cards, `{% if request.view_as.is_admin %}`. Three
  things, in this order, and members never see any of them:
  1. The **guard banner** when the guarded set differs from the acknowledged set (§5.7), with its **"Got it"**
     POST. Unlinked — nothing in FOG can fix a guard trip.
  2. The **unlinked banner** when any row has `is_source_linked=False`, linking to
     `admin:membership_governancedocument_changelist?is_source_linked__exact=0` — the actual to-do list.
  3. A plain **"Manage who can read these"** link to the changelist (§5.5). This is the only route from the
     product to the publishing screen.
- **States:**
  - *Empty, never synced* (no rows at all): `"No governance documents yet."` / *"Policies arrive from the
    makerspace's governance repository the first time the sync runs."* Admins additionally get a
    **"Run the sync"** `pl-btn--secondary` linking to Automations. Never a blank region (brief §3).
  - *Synced, but nothing visible to this viewer* (rows exist, `visible_governance_documents` is empty): a
    **different** message — *"No policies have been published to members yet. Ask a guild lead or an officer
    if you need one."* The causes differ and one of them is an admin's to-do; one generic empty state would
    hide that. With `min_role` defaulting to `ADMIN` (§4.2) this is the **expected state on day one** for
    every non-admin, which is exactly why it reads as a true statement about publishing rather than an error.
  - *Not a member at all:* never reaches this page — the route guard above redirects.
  - *Loading:* none. Plain server-rendered GET, no HTMX.
  - *Error:* the flag-off path redirects to `hub_home`; there is no other failure mode on this page.
- **No edit affordance anywhere.** Not a disabled button — no button (the brief's rule for Official pages,
  and this whole surface is Official).

### 6.2 Screen 2 — Document page

- **Template:** `templates/hub/governance_document.html`
- **Route:** `governance/d/<slug:slug>/` → `hub_governance_document`. The fixed `d/` segment is the same
  discipline the brief imposes on `/wiki/p/<slug>/`: a fixed segment means fixed routes and a document slug
  can never collide with `search/` or a future sub-page. Reserved slugs: `d`, `search`.
- **Fetch:** `visible_governance_documents(request).filter(slug=slug).first()`, then render the 404 body
  below when it is `None`. The **only** supported fetch (§4.2). Explicitly **not** `get_object_or_404` — see
  "The 404 and how it is actually delivered".
- **Layout:** `.pl-guild-grid` (main + aside), matching `help_article.html` — one visual language for
  "read a document in the hub".
- **Main column, top to bottom:**
  1. `.pl-help-breadcrumbs` — `Policies / <Register name>` (reusing the existing class rather than cloning
     it; the Help Center's breadcrumb is already the house breadcrumb, and the brief lists "a breadcrumb
     component" as an unextracted gap that spec A may take).
  2. `page_header.html` with the title. **No action button.**
  3. The single status pill + the neutral attribute line (`Policy · v1.0.1 · Section: Member policies`).
  4. Unlinked notice, if any — a quiet `hub-text-muted` line, not an alarm banner. A policy that is still in
     force does not deserve a red box because a file moved.
  5. **TOC** — `<nav class="pl-gov-toc" aria-label="On this page">` built from `document.toc()`. Two columns
     on desktop (the `.pl-wiki-toc` treatment). Hidden entirely when `toc` is empty. **This element only
     exists because §4.4 adds the `toc` extension and a heading-id filter that accepts an underscore** —
     without both, `toc()` returns `[]` for every document and this block never renders for anyone. Pin it
     with the duplicate-heading test in §9, not with a comment.
  6. **Body** — `<div class="pl-md pl-md--gov">{{ document.body_html|safe }}</div>`. `|safe` is correct and
     narrow: the HTML was sanitized at rest by the `governance` profile through the same bleach allowlist
     every other body uses, and the template carries a `{% comment %}` saying exactly that.
  7. **Provenance footer** — §6.3. Mandatory.
- **Aside:**
  - `hub-card` "In This Section" — the sibling documents in the same `section_name`, current one muted, cut
    to the same `visible_governance_documents` filter. Copied from `help_article.html`'s aside.
  - `hub-card` "Can't Find It?" → the unified search.
- **The gated-document decision — what a member sees for a document above their tier: *nothing at all*.**
  Not a titled-but-locked row, not a 403.
  - On the index, the row is not rendered — it is not in the queryset. That falls out of filter-not-check
    for free.
  - At the URL, the filtered fetch returns `None` and the view answers **404**, never 403. **A 403 confirms
    the document exists; a 404 does not.** Morlock's `require_register` decorator raises `Http404` for the
    same reason.
  - **Even a title discloses.** "Leadership Compensation Strategy" or "Guild Lead Interview Scorecard" tells
    a reader something real about what the organization is doing, and 55 of 59 documents are gated upstream
    precisely because the org has not decided to say those things yet. A locked row is a leak with a padlock
    drawn on it.
  - **The one named exception: cross-document body links.** §4.4's link-resolve pass rewrites a relative
    `.md` link to `/governance/d/<slug>/` for any document in the sync batch, **without consulting the
    reader's visibility** — so a `MEMBER`-visible document can contain a live link whose anchor text is a
    gated document's title, and whose href contains its slug. That is a real exception to "even a title
    discloses" and it is accepted here, on the record, for three reasons: the anchor text is the document
    author's own prose in a document the reader is already cleared to read; the target page still gates
    itself (the click lands on the 404 below, disclosing nothing further); and per-reader body HTML would
    mean rendering 59 documents per request instead of once per sync. **Tested** (§9): a `MEMBER` document
    linking to a `LEADERSHIP` document renders the link for a member, and following it 404s.

#### The 404 and how it is actually delivered

The 404 page for a governance slug must not distinguish "no such document" from "not yours", so it renders
**one message covering both** — *"That document isn't available. It may not exist, or it may not be published
to your role yet. Try the policy index, or ask a guild lead."* — with a link back to `/governance/`.

**That copy needs a template to live in, and `get_object_or_404` gives it none.** There is no `handler404` in
this project, so Django's default renders `templates/404.html`, whose body reads *"We couldn't find that
page. The link may be out of date, or the event may have been removed."* over a **"Browse Past Lives classes"**
button. A member following a stale link to the Code of Conduct would land on the class catalog, and the
sentence above would never ship. Worse, the obvious test — "a gated slug and a nonexistent slug render the
same message" — **passes vacuously** on that generic page, so CI stays green while the screen is wrong.

So:

- `templates/hub/governance_not_found.html` (§3) extends `hub/base.html` and carries the sentence, the
  `/governance/` link, and nothing else. No status pill, no title, no hint of which case occurred.
- The view returns `render(request, "hub/governance_not_found.html", status=404)`. Still a real 404 to a
  crawler and to `curl -I`; just this surface's body instead of the site-wide one.
- A `handler404` branching on the `/governance/` path prefix was considered and rejected: it is a project-wide
  change to serve one surface, and every other 404 in the app would then route through E's code.
- **The test asserts the literal sentence and the presence of `/governance/`** in both responses, not merely
  that the two match. Matching is necessary and nowhere near sufficient.

- **States:** success only, plus the 404 above and the flag-off redirect. No loading state (no HTMX), no
  error state (nothing can fail).

### 6.3 The provenance footer — `templates/hub/partials/_governance_provenance.html`

Non-optional on every document. The brief calls this out (§3, "Provenance footers… Spec E needs this") and
the reason is blunt: **a read-only mirror with no stated correction path reads as broken.** A member who
spots a typo in the Code of Conduct and finds no way to report it concludes the whole surface is abandoned.

```
─────────────────────────────────────────────────────────
About This Document
Source          policies/facilities/fines-policy.md
Repository      Past Lives governance repository (a1b2c3d)
Mirrored        7 Sep 2026, 6:04 AM
Corrections     This page is a read-only copy. Corrections are made in the governance
                repository and then re-synced — they can't be edited here. Spot something
                wrong? Tell a guild lead or an officer, or post in #general on Discord.
```

- `.pl-gov-provenance` — a `hub-card` with `--hub-text-muted` body text and a hairline top rule. Quiet: it
  is reference material, not a call to action.
- The timestamp is rendered in the project timezone via `{{ document.synced_at|date:"j M Y, g:i A" }}`, one
  timezone throughout (FRONTEND.md email rule, applied here for the same reason).
- The repository is named, **not linked** — a member has no access to it and a 404 on github.com is a worse
  answer than a sentence.
- When `is_source_linked` is false, one extra line: *"The source file is no longer in the repository (noticed
  7 Sep 2026). This copy is kept until an officer retires it."*
- The index page carries a one-line version of the same footer, so provenance is stated even to someone who
  never opens a document.

### 6.4 Dark + light, and 390px mobile

**Theming.** Tokens only — `--hub-card-bg`, `--hub-border`, `--hub-text`, `--hub-text-muted`,
`--color-tuscan-yellow` for links, `--hub-surface` for the TOC inset. No hardcoded hex, and specifically
never `var(--surface, #fff)`, which is not a token and silently yields a white box with invisible text on
dark (FRONTEND.md Rule 13). Status pills reuse the existing hub pill tokens rather than minting governance
colors — three pill states do not justify a new palette.

**There are exactly two form controls on this surface, and one of them is the Rule 13 shape.** The register
index includes `components/table_search.html`, which is a real `<form>` containing
`<input type="search" style="… background:rgba(0,0,0,0.1); color:inherit; …">` — an inline-styled input
outside any `.hub-form-group`, i.e. the precise pattern Rule 13 forbids, shipped today and inherited by every
caller. E does **not** fork the component to fix it (that is a repo-wide change affecting every table page),
but it must not claim the trap does not arise either: **"verify the search input renders legibly in light
mode"** is a line on the theming checklist below, and if it fails, the fix belongs in the component for
everyone. The second control is the admin-only **"Got it"** button on the guard banner (§5.7) — a
`hub-btn`, not an input, so Rule 13 does not reach it; Rule 18 does (it must not touch the register card
below it). The Rule 14 date-picker trap genuinely does not arise: there are no date inputs.

**Verify both themes** on: the index, the document page, a long table, the TOC, the two admin banners, and
**the search input in light mode**.

**Mobile, 390px.** These are long documents with numbered sections and markdown tables — a different problem
from the wiki's gloves-and-dust shop-floor reading, so the brief's §5.6 rules are applied where they fit and
consciously departed from where they do not:

- **Body text 17px minimum** (brief §5.6): `.pl-md--gov { font-size: 1.0625rem; line-height: 1.65; }` under
  `@media (max-width: 640px)`.
- **TOC becomes a `<details>` disclosure**, closed by default, labeled "On This Page (14 sections)" — **not**
  the wiki's horizontal chip row. Deliberate departure, justified: the bylaws TOC has thirty entries, and a
  thirty-chip horizontal scroller is strictly worse than a collapsed list you can scan. `<details>` also
  needs no Alpine, so it survives `hx-boost` body swaps for free (brief §5.9). Like the desktop TOC, this
  disclosure **only has contents because of §4.4's `toc` extension and underscore-tolerant id filter** — the
  "(14 sections)" count comes from `len(document.toc())`, which is `0` for every document without them.
- **Tables scroll inside their own container; the page never scrolls horizontally.** The CSS-only version
  (`.pl-md--gov table { display: block; … }`) was the first draft here and it is **wrong twice**, so E does
  the wrapper instead:

  1. It is already there. `static/css/hub.css` sets exactly
     `.pl-md table { display: block; width: max-content; max-width: 100%; overflow-x: auto; }`
     for **every** `.pl-md` body, so a `.pl-md--gov` copy is a duplicate declaration, and the
     `width: max-content` in it is redundant besides.
  2. `display: block` on a `<table>` **removes its table semantics** — VoiceOver/Safari stop announcing it as
     a table with rows and columns — and a scroll container with no `tabindex` **cannot be scrolled by
     keyboard**, so a wide policy table becomes both unnavigable and unreadable for the people most likely to
     need the policy.

  So the sync wraps each `<table>` in the already-sanitized output:

  ```html
  <div class="pl-gov-tablewrap" role="region" tabindex="0" aria-label="Table">…<table>…</table>…</div>
  ```
  ```css
  .pl-gov-tablewrap { overflow-x: auto; max-width: 100%; }
  .pl-md--gov table { display: table; width: auto; }   /* undo the inherited .pl-md rule */
  ```

  **This does not loosen the sanitizer.** The wrap happens *after* `bleach.clean` on output we produced, and
  nothing goes back through bleach — `div` stays off `_ALLOWED_TAGS`, so a `<div>` an upstream author writes
  is still stripped. It is one small function in `governance_policy.py`, applied to `body_html` only, and it
  is the reason §4.4 says there is exactly one post-sanitize step.

  > The same `display: block` problem exists today for `.pl-md` everywhere (Help Center, meeting notes). E
  > fixes it only under `--gov` because widening the fix is spec A's call, not a governance mirror's. Worth
  > handing to A as a one-line note.
- **Numbered sections:** deeply nested `<ol>` indent off the right edge at 390px. Under 480px,
  `.pl-md--gov ol { padding-left: 1.25rem; }` and `.pl-md--gov ol ol { padding-left: 1rem; }`.
- The aside stacks below the main column (existing `.pl-guild-grid` behavior).
- No hover-dependent affordances anywhere — there are none to begin with, this being a reading surface.
- 8px spacing grid throughout.

**CSS naming.** I grepped `static/css/hub.css` (8,418 lines), `components.css`, and `style.css`:
`pl-wp-` → **0 hits** anywhere (it is spec A's reserved prefix, and E must not squat on it);
`pl-gov*`, `pl-policy*`, `pl-doc*` → **0 hits**. `pl-wiki-toc` / `pl-wiki-article` are taken by the Help
Center at `hub.css:3435-3442`, and `pl-help-*` is the Help Center's block at `:3482` onward.

E therefore uses **`pl-gov-`**, a distinct governance prefix, *not* `pl-wp-` — because `pl-wp-` means "wiki
page" and a governance document is emphatically not one; sharing the prefix would make a future
"why is `.pl-wp-row` styled for a read-only page" a real question. New classes: `.pl-gov-row`,
`.pl-gov-section`, `.pl-gov-toc`, `.pl-gov-meta`, `.pl-gov-provenance`, `.pl-gov-banner`,
`.pl-gov-tablewrap`, `.pl-md--gov`. All go in `hub.css` (brief §5.7). `.pl-help-breadcrumbs`, `hub-card`,
`pl-btn`, `.pl-guild-grid`, `.pl-md` and the pill classes are **reused as-is**, not re-declared — with the
single documented override of `.pl-md table`'s `display: block` under `.pl-md--gov` (above).

### 6.5 Screen 3 — Django admin (`membership/admin.py`)

The publishing screen, and the only place `min_role` changes.

- Registered as `@admin.register(GovernanceDocument)` on `unfold.admin.ModelAdmin`, the house base class in
  `membership/admin.py`.
- `list_display = ("title", "register", "section_name", "status", "version", "min_role", "is_source_linked", "synced_at")`
- **`list_editable = ("min_role",)`** — publish a batch of documents from one screen, one Save. With the
  `ADMIN` default (§4.2) this screen is the entire publishing workflow, so it has to actually work.
  > **Verify this by hand before relying on it. `list_editable` has zero occurrences anywhere in this
  > repository** — every existing `ModelAdmin` here is view-and-detail-edit only — so it is **untested under
  > django-unfold's changelist templates**, which override Django's. E5's checklist carries an explicit line:
  > *open the changelist, change `min_role` on two rows, Save, confirm both persisted and that `LogEntry`
  > recorded both.* If Unfold does not render the inline widgets, the fallback is an
  > `@admin.action(description="Publish to any member")` bulk action on the selected rows — same audit trail,
  > one more click, and a pattern this repo does use.
- `list_display_links = ("title",)` — required whenever `list_editable` is set, or Django raises
  `admin.E122`/`E124` at `manage.py check` time.
- `list_filter = ("register", "min_role", "status", "is_source_linked", "section_name")` — `is_source_linked`
  is the filter the unlinked banner deep-links into (§5.7).
- `search_fields = ("title", "source_path", "slug")`
- **`readonly_fields` = every other field.** Content belongs to git; the admin must not become a back door
  into editing it. `has_add_permission` → `False`, `has_delete_permission` → `False` (deletion is
  `--purge-unlinked`, §5.6).
- A short `ModelAdmin` docstring rendered as the changelist description: *"Mirrored from the governance
  repository — read-only. The only thing you set here is who can read each document. Every document starts at
  `Admins only`: move it to `Guild leadership & officers` once you have read it, and to `Any member` only when
  it is meant for the whole membership."*
- Admin styling is `unfold-custom.css`; **no `pl-` classes here** (FRONTEND.md CSS table).

### 6.6 Screen 4 — Unified search results

Governance rows appear in spec A's `/wiki/search/`, as a group.

- Query: `visible_governance_documents(request).search(q)` — the **same** visibility filter, so search can
  never surface a title the index would not.
- Each result renders through **A's shared result partial** — `_search_result.html` or `_wiki_card.html`,
  whichever name A ships under brief §9.1; E consumes the contract, not the filename — with:
  - `title` + `url` → the document page
  - `snippet` → `document.search_snippet(q)`, pre-escaped with `<mark>`, `|safe` in the template exactly as
    `help_search.html` already does
  - **`source_chip` = "Policies"** — the distinct source chip the brief requires, neutral, so all three
    stores are distinguishable at a glance
  - **`status_pill`** = the one status pill (`Canonical` / `Adopted` / `Draft`), or none
  - one line of quiet neutral text: `Read-only · Policy · v1.0.1`
- The `?source=governance` chip filters to this group alone — that is the link the register index's search
  box posts to.
- Zero results in the governance group: nothing renders for the group at all (A's page owns the global
  zero-result state, which per brief §7 offers "Ask in #guild" and "Request this page").

### 6.7 Screen 5 — Sidebar entry

`templates/hub/base.html` has **two** sidebar blocks — not desktop and mobile, as an earlier draft of this
spec had it, but `{% if request.view_as.is_admin %}` (~:89-227) and its `{% else %}` (~:228-368). They are
separate markup and missing one is the classic half-shipped nav. `help_page_enabled` appears at ~:208 in the
first and ~:349 in the second; the Policies entry goes next to each:

```django
{% if governance_page_enabled and request.view_as.is_member %}
<a href="{% url 'hub_governance_index' %}" class="hub-sidebar__link {% active_nav 'hub_governance_index' %}"
   data-help-key="nav.governance"> <svg …/> Policies </a>
{% endif %}
```

**The `is_member` half is load-bearing, not decoration.** The `{% else %}` block renders for *every*
non-admin, including a logged-in user with no linked `Member` — a class registrant. Gated only on the
feature flag, that person would see "Policies" in the sidebar, click it, and be redirected home (§6.1). The
`request.view_as.is_member` test is the same one already used two entries away for the My Tab and
guild-membership links (~:167, ~:284, ~:316), so this is the house pattern, and it costs no query.

> **One consequence, stated:** `ViewAs` grants `ROLE_MEMBER` only to a `Member` whose `status` is ACTIVE, so
> a **lapsed** member sees no sidebar entry even though §1 deliberately lets them *read* (`governance_roles`
> does not require ACTIVE — §4.2). A stale link, a bookmark, or a URL from Discord still works for them,
> which is the scenario §1 actually cares about: reading the agreement they are arguing about. Making the
> nav match would mean either locking lapsed members out of reading or resolving a `Member` row in the
> context processor on every request; neither is worth it for the nav's sake.

Placed directly under **Help**, above the external Wiki link, in the same post-divider reference group.
Labeled **"Policies"**, not "Governance" — members search for the former. The flag comes from
`core/context_processors.feature_flags`, alongside `help_page_enabled` / `wiki_link_enabled`.

**`data-help-key="nav.governance"` requires a registry entry or CI fails.** `tests/hub/help_keys_spec.py`
walks every `.html` under `templates/`, collects every `data-help-key`, and asserts each one resolves in
`core.help_registry.HELP_KEYS` and matches `KEY_PATTERN`. An unregistered key is a red build, not a silent
no-op. So `core/help_registry.py` gains an annotation-only entry in the `nav.*` group, matching the
`nav.help` precedent exactly (`article_slug: None`, `anchor: None`, so `url_for` degrades to `/help/`):

```python
"nav.governance": {
    "title": "Policies",
    "short_text": (
        "The makerspace's official policies, bylaws, and role descriptions, mirrored here to read. "
        "They're maintained elsewhere, so they can't be edited in the app."
    ),
    "article_slug": None,
    "anchor": None,
},
```

`SiteConfiguration.governance_page_enabled` also needs its toggle wired in three places, matching the
`help_page_enabled` precedent exactly: `SiteSettingsForm.Meta.fields` in `hub/forms.py`, the `field.name !=`
exclusion chain in `templates/hub/admin/site_settings.html` (**:184** as of 2026-09-07 — it is one very long
line; grep `help_page_enabled` rather than trusting the number), and an explicit
`{% include "components/form_field.html" with field=form.governance_page_enabled %}` next to the other two
(**:461-462**). It renders as a **toggle** (Rule 3), automatically, via `form_field.html`.

---

## 7. Notifications / emails / activity

- **Emails: none.** Nothing here is time-sensitive to a member and "a policy was updated" is a Discord
  announcement Josh writes, not an automated send. The brief's own reasoning against per-page subscriptions
  (roughly zero subscribers per page at 200 members) applies with more force to documents nobody asked to
  watch.
- **Notifications: none.** No `EventType`, no `core/triggers.py` entry. E emits no wiki events; those belong
  to A/B/D.
- **`SiteActivity`: one new kind.**
  `GOVERNANCE_SYNCED = "governance_synced", "Governance documents synced"` — one row per successful run,
  `actor=None`, payload `{created, updated, unlinked, excluded, guarded: [...], images_dropped: [...], ref}`.

  **What this is and is not.** It is a queryable record and the data source for the index's admin banners
  (§5.7). It is **not** a human-readable audit surface on its own: `templates/hub/admin/_activity_feed.html`
  renders only `get_kind_display`, the actor email, the timestamp, and an optional email-status badge —
  `payload` is never touched. In `/manage/activity/` this kind therefore appears as
  *"Governance documents synced · System · Sep 7, 6:04 AM"*, with no counts and no guarded list. The place an
  admin actually learns what a sync did is the index banner and the Automations run history, both of which E
  builds. **If** the feed row should carry a summary, that is one `{% if a.payload %}` line in
  `_activity_feed.html` and it is a deliberate, separate decision for Josh — E does not assume it.
- **`ScheduledTaskRun`** rows come free from `record_run` via the registry entry (§5.8), so the Automations
  dashboard shows last-run, duration, and failures without any new plumbing — including the FAILED row that
  an unconfigured or revoked-token run produces (§5.4).
- **`robots.txt`.** `core.views.robots_txt` gains `"Disallow: /governance/"` alongside `/admin/`,
  `/accounts/`, `/settings/`, `/billing/`, `/tab/`. Hygiene only — the routes are `@login_required` and a
  crawler gets a redirect either way — but every other private area is listed and this one should not be the
  exception a future reader has to reason about.

---

## 8. Build order (phased; each phase ships green)

Green means: full suite + `ruff format . && ruff check .` + `mypy .` + `manage.py check`. Per brief §5.9,
**every PR bumps `plfog/version.py` VERSION**, and the wiki round is ONE member-facing feature — the first PR
of the round adds the changelog entry and **every later PR edits and re-stamps that same entry** rather than
adding a second. E's phases re-stamp A's entry.

**This spec builds after spec A.** Phase 4 additionally requires A's search page to exist.

| Phase | Contents | Ships green because |
|---|---|---|
| **E1 — Model & permissions** | `GovernanceDocument` + queryset + migration; `Role`/`Register`/`Status` choices; `governance_roles()` + `visible_governance_documents()` in `membership/permissions.py`; the `governance` markdown profile (`toc` extension, `_GOV_HEADING_ID_PATTERN`, `_allow_gov_heading_attr`, `_harden_link_gov`) + `governance_markdown` filter. No UI, no command. | Model + filter + profile are fully unit-testable with factories and fixture markdown. `manage.py check` catches the index-name cap. The duplicate-heading and Drive-URL tests both live here. |
| **E2 — The sync** | `governance_source.py` (fetch/extract), `governance_policy.py` (the ported policy + the table wrapper), `sync_governance_docs` with `--dry-run` / `--source` / `--ref` / `--skip-if-unconfigured` / `--purge-unlinked`; `SiteActivity.Kind.GOVERNANCE_SYNCED`. Settings vars added, still blank. **No `SCHEDULED_JOBS` row and no `render.yaml` change yet** (§5.4/§5.8). Still no UI. | The command is exercised against `tests/membership/fixtures/governance_source/` via `--source`, and the fetch via `respx`. Nothing user-facing changes, and nothing runs on a schedule that cannot yet succeed. |
| **E3 — Reading surface** | Both views + the three templates (index, document, **`governance_not_found.html`**) + the provenance partial; the guard-ack POST route + `SiteConfiguration.governance_guard_ack`; the `pl-gov-` CSS block; `SiteConfiguration.governance_page_enabled` (default `False`) + its migration + context processor + Site Settings toggle; the sidebar entry in both nav blocks + the **`nav.governance` help-registry entry**; `robots.txt`; `GovernanceDocumentAdmin`. | Flag defaults off, so production sees no change until Josh flips it. Template tests assert every state; `tests/hub/help_keys_spec.py` covers the nav key. |
| **E4 — Search** | Contribute the governance group to A's `/wiki/search/`; the `?source=governance` chip; `search_snippet`. | Additive to A's page; the group is empty when nothing is visible. **Blocked on A's grouped search (brief §9.1); there is no E-side fallback (§2.1).** |
| **E5 — Ops & release** | `render.yaml`: the `sync_governance_docs --skip-if-unconfigured` build step + the three env vars **on both the web service and the `run-scheduled-tasks` cron**, `sync: false`, **no `previewValue`**. The `SCHEDULED_JOBS` row. Run the §4.2 leadership-population query and write the number into this spec. First real sync (dry-run first, then live). **Verify `list_editable` actually saves under Unfold (§6.5).** Publish documents by setting `min_role` in the admin — `ADMIN` → `LEADERSHIP` → `MEMBER`, deliberately. Flip `governance_page_enabled` on. VERSION bump + re-stamp the round's changelog entry. | **Requires the `org/` access from §10.** E1–E4 ship without it; E5 cannot. |

> Spec only — do not build until approved.

---

## 9. Testing

BDD `*_spec.py`, `describe_*` / `it_*`, factory-boy, `respx` for HTTP, ≥98% branch coverage (`pyproject.toml`
`fail_under = 98`). Remember: **`context_*` is not a collected prefix** — every nested block is `describe_*`.

**Fixtures.** `tests/membership/fixtures/governance_source/` — a small fake `org/` tree, roughly ten files,
that exercises every branch: one file per register; one under `_private/`; one in `EXCLUDE_FILES`; one with
`ARCHIVED` in the title and one with it in the first 200 characters; one containing an SSN-shaped string; one
containing "Venmo"; one on the `GUARD_ALLOW` list containing "Venmo"; one with a redaction target; one with
YAML frontmatter carrying a `provenance:` Drive ID; one with a markdown table; one linking to a sibling `.md`
and one linking to an excluded `.md`; **one with two identically-titled `##` headings** (the duplicate-anchor
case); **one whose body contains a bare `https://docs.google.com/document/d/…` URL in running prose *and* a
hand-written `<a href="https://docs.google.com/…">`** (the two linkify escapes, §4.4); **one containing a
markdown image** (the silent-drop case). A `GovernanceDocumentFactory` in `tests/membership/factories.py`.

> **Fixture markdown must not hand-write `{#slug}` heading attributes.** That is the trap the first draft of
> this spec fell into: the `toc()` test passed against fixtures whose anchors a human had typed, while every
> real `org/` document — authored for a converter that generates its own anchors — would have produced an
> empty TOC. Fixtures here are plain `##` headings, exactly like the corpus.

### `tests/membership/governance_policy_spec.py` — the publish policy

| Case | Assertion |
|---|---|
| **Fail-closed classification** | An unrecognized path classifies as `(document, "Governance & legal", "Document")` and the created row's `min_role` is **`ADMIN`** — never `LEADERSHIP`, never `MEMBER`, and there is no `PUBLIC` choice to land on. |
| Classification table | Each ported branch: `guild-officers/*` → role; `STAFF_ROLES` → Staff roles; work-trade paths → Work trade; `SIGNED_AGREEMENTS` → Document/Forms & agreements; `policies/facilities/*` → Facilities policies; glossary → Standards. |
| **Sensitive guard fires** | An SSN-shaped string skips; "Venmo" skips (case-insensitively); the skip is reported and the row is **not created**. |
| **`GUARD_ALLOW` exempts** | The allow-listed path containing "Venmo" **is** synced. |
| **Redactions run before rendering** | The redacted literal appears in **neither** `body_html` **nor** `search_text`, and the replacement does. |
| **Redaction before guard** | A document whose only guard trip is inside redacted text is **not** skipped. |
| **Frontmatter stripped from both** | A `provenance:` Drive ID and a `status:` line are absent from `body_html` **and** absent from `search_text`. (Two assertions — the second is his v1.45.0 second-order bug.) |
| Frontmatter edge cases | A mid-document `---` horizontal rule survives; an unterminated opening block leaves the document intact rather than eating it. |
| ARCHIVED detection | Skipped when in the title; skipped when in the first 200 characters; **not** skipped when the word appears at character 900. |
| Exclusions | `SKIP_DIRS` at any path depth; `EXCLUDE_FILES` case-insensitively; `*.CHANGELOG.md`. |
| Version parsing | Frontmatter `version:` wins; falls back to `**Version:** 1.0.0` (no `v` prefix); empty when neither. |
| Title cleaning | `Role Explication:` prefix, `Past Lives Makerspace |` prefix, and a trailing `v1.2` are stripped; `TITLE_OVERRIDE` wins; a titleless file falls back to a prettified stem. |
| Slug stability | `policies/code-of-conduct.md` → `policies-code-of-conduct`, byte-identical to `sync_docs`. |
| **Link resolution** | A relative `.md` link to a synced sibling becomes `/governance/d/<slug>/`; a link to an excluded document is **stripped to plain text**; `../` resolves against the source path; `mailto:` survives; `calendar.google.com` survives. |
| **Google Docs — all three shapes** | (a) a markdown link `[the doc](https://docs.google.com/document/d/1AbC/edit)`, (b) a **bare** `https://docs.google.com/document/d/1AbC/edit` in running prose, and (c) a hand-written `<a href="https://docs.google.com/…">the doc</a>` all produce **zero `<a>` elements** in `body_html`, with the label text intact in every case. (b) and (c) are the escapes a markdown-level rule misses (§4.4) and each is its own `it_` — a single combined assertion would pass on (a) alone. `drive.google.com/file` likewise. |
| **Images are dropped, and reported** | A markdown image produces no `<img>` and **no alt text** in `body_html` (assert the alt string is absent — this is the surprising half), and the run's report and `SiteActivity` payload both name the file under `images_dropped`. |
| **Table wrapper** | A markdown table's `body_html` contains `<div class="pl-gov-tablewrap" role="region" tabindex="0"` wrapping a `<table>`; a document with no table gets no wrapper; and the wrapper does **not** appear inside `search_text`. |

### `tests/membership/management/sync_governance_docs_spec.py` — the command

| Case | Assertion |
|---|---|
| **Refuses an empty source tree** | A directory with zero `.md` raises `CommandError`; the message names the resolved path; **no rows are touched** (assert count and a pre-seeded row's `synced_at` are unchanged). |
| **Refuses a missing source tree** | Same, for a path that does not exist. |
| **Refuses on a failed fetch** | `respx` 404 and 500 → `CommandError` naming the status and repo; **no writes**. |
| **`min_role` never written — structurally** | `"min_role" not in SYNCED_FIELDS`. The test that survives a refactor. |
| **`min_role` never written — behaviorally** | Set a row to `MEMBER`, re-sync with changed content, assert `body_html` changed **and** `min_role` is still `MEMBER`. |
| **Stale sources are kept, not deleted** | Sync, remove a file from the fixture tree, re-sync: the row still exists, `is_source_linked is False`, `min_role` unchanged, `body_html` unchanged. Assert the total row count did **not** drop. |
| Re-linking | Restore the file, re-sync: `is_source_linked` returns to `True`. |
| `--purge-unlinked` | Only then is an unlinked row deleted — and a *linked* row never is. |
| **`--dry-run` writes nothing** | Row count unchanged, `synced_at` unchanged, and the report says "Would sync". |
| **Unconfigured is loud by default** | With no `GOVERNANCE_REPO`, a bare run raises `CommandError` and the message names the unset variable. **With** `--skip-if-unconfigured`, it exits 0 and prints the skip line. (The polarity matters: an earlier draft had it backwards, which made the nightly job green forever — §5.4.) |
| **The registry row is dispatchable** | `JOBS_BY_KEY["sync_governance_docs"].command` contains **no whitespace** — a structural assertion, because `call_command(job.command)` takes a command *name* and a flag in that string raises `CommandError: Unknown command` at run time on both dispatchers. Cheap, and it survives someone "helpfully" appending a flag. |
| Report content | Guarded paths appear in the per-file `SKIP (guard)` lines **and** in the summary block; counts are correct. |
| `SiteActivity` | One `GOVERNANCE_SYNCED` row per successful run; its payload `guarded` list matches the report; a `--dry-run` writes none. |
| Idempotence | Two consecutive syncs of the same tree: second reports `0 new`, and no field changes except `synced_at`. |
| Fetch + extract | `respx`-mocked tarball is extracted correctly; `source_ref` is the SHA from the archive's top-level directory; the temp directory is gone afterward. |
| Archive safety | A tarball containing a `../` member does not write outside the temp dir (`filter="data"`). |

### `tests/membership/governance_document_spec.py` — model & filter

| Case | Assertion |
|---|---|
| **Visibility per role** | A parameterised matrix: anonymous / no linked Member / member / guild lead / guild staff (orienter) / guild staff (**`custom_title`**, no preset role) / `fog_role=guild_officer` / `fog_role=admin` × `min_role` in {MEMBER, LEADERSHIP, ADMIN}. Each cell asserts the exact visible set. The two staff rows exist because `staffed_guilds` covers both shapes and §4.2 accepts that width deliberately — if someone later narrows it, these fail. |
| **`view_as` honored** | An admin previewing as Member sees only `MEMBER` documents; previewing as Guest sees **none**. |
| **`view_as` beats guild leadership** — the one that would have been wrong | An admin **who is also a guild lead**, previewing as Member, sees only `MEMBER` documents. Without the previewing-down guard (§4.2) she keeps LEADERSHIP, because the house pattern this copies (`can_edit_guild` via `_editing_member`) passes for a previewing admin. Mirror it: a `fog_role=guild_officer` who leads a guild, previewing as Member, also sees only `MEMBER`. |
| **A plain-member guild lead is not caught by that guard** | A member with `fog_role=member` who holds `Guild.guild_lead` (no role picked, so `effective == actual`) **does** see `LEADERSHIP` documents. This is the assertion that fails if the guard is written as "`view_as_role` must be at least `guild_officer`" — their `view_as_role` is `member`. |
| **Default is ADMIN** | A `GovernanceDocument` created with no explicit `min_role` is invisible to a plain member **and to a guild lead**, and visible to an admin. |
| **Lapsed member** | A member with `status != ACTIVE` still sees `MEMBER` documents (the §1 decision, pinned so nobody "tightens" it by accident) — even though `request.view_as.is_member` is `False` for them, which is exactly why `governance_roles` does not route through `_editing_member`. |
| `toc()` | Returns `(level, anchor, text)` for h2/h3 with ids; empty list for a body with none. |
| **`toc()` survives repeated headings** | A body with **two `##` headings of the same text** yields **two** entries with **distinct** anchors (`purpose`, `purpose_1`), and both anchors are present as `id=` attributes in `body_html`. This is the test that catches the underscore being stripped by the id pattern — the failure that would have silently deleted "Purpose", "Definitions" and "Notes" from every bylaws TOC. Assert on the anchors, not just the count. |
| **`toc()` works on unannotated markdown** | A document whose source contains **no `{#slug}` attributes at all** still produces a non-empty `toc()`. Without the `toc` extension this is empty for every real document while a hand-annotated fixture passes. |
| `search()` | Multi-word query ANDs per term; matches across title / `search_text` / `section_name`; empty `q` → `none()`; **case-insensitive**. |
| `search_snippet()` | `<mark>` wraps the hit; HTML in the source is escaped exactly once; a title-only hit falls back to the lead. |
| `get_absolute_url` | `/governance/d/<slug>/`. |
| Ordering & `__str__` | Register, then section order, then title. |

### `tests/membership/markdown_spec.py` — the `governance` profile (added block)

- `render_markdown(src, profile="governance")` **strips `<iframe>`** even from an allowlisted Loom URL.
- It **strips `<img>`** even from `/static/help/…`, and the `alt` text does not survive either.
- It keeps tables, lists, blockquotes, and code.
- **h2/h3/h4 get `id` anchors with no `{#…}` in the source** — the extension generates them.
- **A repeated heading's `_1` suffix survives sanitizing**, matching `_GOV_HEADING_ID_PATTERN`.
- A hand-written `id="evil onclick"` is dropped.
- **`marker: ""` holds:** a literal `[TOC]` in the source renders as the text `[TOC]`, and the output contains
  no injected list of heading links. (Left enabled, the extension emits `<div class="toc"><ul>…` whose `div`
  bleach strips while keeping the inner links — a duplicated TOC pasted into the body.)
- **The help profile is unchanged:** `render_markdown(src, profile="help")` on a body with two identical `##`
  headings still emits **no** ids, `_HEADING_ID_PATTERN` still rejects `purpose_1`, and every existing help
  golden file passes byte-identically. E adds a profile; it does not widen one.
- **A structural guard on the shared allowlists:** `_ALLOWED_TAGS` and `_ALLOWED_ATTRS` are asserted equal to
  their expected literal contents. A golden file cannot catch an in-place mutation of these — member markdown
  emits no heading ids, so loosening `_ALLOWED_ATTRS` for governance's sake would leave every `member` golden
  file green while quietly widening the member profile. (`img` appearing in `_ALLOWED_TAGS` is the specific
  regression this catches.)
- Internal `/` and `#` links stay same-tab with `rel="noopener"`; external links get
  `rel="noopener nofollow noreferrer" target="_blank"`.
- **Google Docs de-linking at the profile level**, in all three shapes (markdown link, bare prose URL,
  hand-written anchor) — the same three cases as the policy spec, asserted here against `render_markdown`
  directly so the rule is pinned to the profile and not only to the sync.
- `<script>`, `<style>`, `onclick=`, and inline `style=` are stripped in every case.
- `render_markdown(src, profile="nope")` still raises `ValueError` (the existing guard must keep covering the
  new name).
- A golden-file case under `tests/membership/fixtures/markdown_golden/`, matching the existing convention.

### `tests/hub/governance_spec.py` — views & templates

| Case | Assertion |
|---|---|
| Flag off | Both routes redirect to `hub_home`, for members **and** admins. |
| Anonymous | Both routes redirect to login (`@login_required`). |
| **Gated document 404s** | A member requesting a `LEADERSHIP` slug gets **404, not 403**, and the response body does **not** contain the document's title. |
| **404 text is E's, not the site's** | Both responses contain the **literal sentence** *"That document isn't available."* and a link to `/governance/`, and contain **neither** `"Browse Past Lives classes"` **nor** `"We couldn't find that page"`. Asserting only that the two responses *match* is not enough — it passes on the site-wide `templates/404.html`, which is exactly what `get_object_or_404` would have rendered (§6.2). |
| **404 text is indistinguishable** | The gated-slug and nonexistent-slug responses render the same message *and* the same status code (404). |
| **Cross-document link exception** | A `MEMBER` document whose body links to a `LEADERSHIP` document renders that link and its title for a plain member (the accepted disclosure, §6.2), and following it returns the 404 body above. |
| Index gating | A member's index HTML contains no gated title (assert on the raw response content, not the queryset — this is the leak a template can reintroduce). |
| **Logged-in non-member** | A `User` with **no linked `Member`** (a class registrant) is redirected from both routes, and the Policies entry appears in **neither** nav block for them. Without the `is_member` nav gate and the `governance_roles` route guard, they see the entry and land on "No policies have been published to members yet" — a false statement (§6.1, §6.7). |
| Empty states | Zero rows → the "never synced" copy, and the admin variant carries the Run-the-sync button while the member variant does not. Rows-but-none-visible → the *other* copy. |
| **Admin route to publishing** | An admin's index contains a link to the `GovernanceDocument` changelist; a member's does not. |
| One pill | An index row renders exactly one element carrying a status-pill class; `doc_type` / `version` render as neutral text. Blank status → zero pills. |
| Provenance footer | Present on every document page; contains `source_path`, the ref, the formatted `synced_at`, and the literal correction sentence. Unlinked adds its extra line. |
| Admin banner | Rendered for an admin when the last `GOVERNANCE_SYNCED` payload has guarded paths; **absent** for a member with the identical data. |
| **The guard banner is dismissible and stays dismissed** | After POSTing to `governance/guard-ack/`, the banner is **gone** on the next GET with the same guarded set; a **new** guarded path in a later sync brings it back; a member POSTing to that route gets denied. Without this, the banner renders on every page load for every admin forever, which is how a real signal becomes wallpaper (§5.7). |
| **The unlinked banner points somewhere useful** | Its link resolves to the changelist filtered by `is_source_linked__exact=0`, not to Site Settings → Automations (which can fix neither problem). |
| No edit affordance | The document page contains no `hub_governance_*edit*` URL, and its only `<form>`/button is the admin-only guard acknowledgement — never present for a member. |
| Sidebar | The Policies entry appears in **both** nav blocks when the flag is on (admin block *and* else block), and in neither when off. |
| **Help key resolves** | `tests/hub/help_keys_spec.py` passes — i.e. `"nav.governance"` is in `HELP_KEYS` and matches `KEY_PATTERN`. This is an existing spec that E must not break; it is listed because E adds the first `data-help-key` on this surface. |
| **robots** | `/robots.txt` contains `Disallow: /governance/`. |
| Search integration | A governance hit carries the "Policies" source chip; `?source=governance` filters to it; a gated document never appears for a member. |
| Template lint | `tests/template_comment_lint_spec.py` (existing) must pass — no multi-line `{# #}` (Rule 17, four leaks to date). |

**Gotchas to pin.** `synced_at` is timezone-aware and the footer renders in project time — freeze time in the
footer test rather than asserting a computed string. The `--dry-run` tests must assert `synced_at` is
*unchanged*, not merely that counts match, because a `defaults` bug can touch a row without creating one.

---

## 10. Open / deferred

### The blocking dependency

> **Read access to PLM's private `org/` repository, which Josh needs to ask Morlock for.**
>
> The brief already names this (§8.1, §4 "Governance docs"), and the handoff says the same: *"Ask Morlock for
> the `org/` tree if you want a realistic dataset."* Concretely, what to ask for:
>
> 1. **Read access to the repo** — ideally as a fine-grained GitHub PAT scoped to **Contents: Read on that one
>    repository** (not a classic token, not org-wide). That token is `GOVERNANCE_REPO_TOKEN`.
> 2. **The repo's `owner/name`**, and whether the markdown tree is at the repo root or nested under `org/`
>    (this sets `GOVERNANCE_REPO_SUBDIR`; §5.1 — the one structural fact I could not verify from the export).
> 3. **The two real values** behind `REDACTED_PERSONAL_EMAIL_1` / `_2` in his `REDACTIONS` table. The handoff
>    replaced them with placeholders before export, and without them the redaction rule for
>    `policies/member-communication-policy.md` silently does nothing — which is the failure mode the whole
>    redaction table exists to prevent.
> 4. A heads-up that **FOG will publish some of these to all members**, so his level-20 default is not the
>    end of the story. Josh sets `min_role` per document; Morlock should know which ones.
>
> **The spec is writable without any of this. The build stops at phase E5 without it** — E1 through E4 ship
> green against the fixture tree, so the code can land and sit dark while the access conversation happens.

### One accepted consequence, recorded rather than discovered

**The mirror copies 55 officer-gated documents into FOG's production Postgres.** §6.0 is careful that nothing
private lands on a persistent *filesystem* — the tarball goes to a `TemporaryDirectory` and is deleted — but
`body_html` and `search_text` are database columns, and that is the whole point of a mirror. The
consequences follow the database, not the sync:

- **Backups.** Render's daily Postgres backups now contain the governance corpus, under Render's retention
  and their access controls rather than GitHub's.
- **Laptops.** The documented way to read production data in this project is
  `DATABASE_URL="$PROD_DATABASE_URL" .venv/bin/python manage.py shell` from a development machine. Anyone with
  that URL can read all 59 documents regardless of `min_role`, which is a UI gate, not a storage one.
- **Preview environments.** Closed by the `previewValue` rule in §6.0 — but only by that rule, so it is not a
  detail to relax later.
- **Scope.** `finance/`, `meeting-minutes/`, `_private/`, `board/` and `notes/` never enter the database at
  all (§5.3), so the residency question covers policies, bylaws, roles and agreements — not the material
  where it would be sharpest. That is deliberate and is the main reason this is acceptable.

This is **accepted, not mitigated**: the trade is one login and one search for the membership, and it is
exactly the trade §1 describes. It is written down so nobody meets it for the first time during an incident,
and so item 4 of the ask above ("a heads-up that FOG will publish some of these") is understood by Morlock as
covering storage as well as publication.

### Deferred, with reasons

| Deferred | Why |
|---|---|
| **A hub-side publishing UI for `min_role`** | 59 documents, four admins, one decision per document. The Django admin with `list_editable` does the job and brings `LogEntry` auditing for free — **conditional on E5's hand-verification that `list_editable` renders and saves under django-unfold** (§6.5); if it does not, the fallback is an admin bulk action, still not a hub screen. Revisit if the corpus passes ~150 documents or if a non-admin ever needs to publish. |
| **A `GOVERNANCE` `AdminCapability`** | Nobody has asked to separate "publishes policies" from "is an admin". When someone does, it lands in `governance_roles()` — one function. |
| **Mirroring `reg_id`s** | FOG minting its own DOC-### would create a second, conflicting ID space, and his counter is exactly what reshuffled 27 IDs. If the IDs are wanted, they should arrive in the source markdown's frontmatter, which is a change on his side. |
| **A public (login-free) tier** | The brief locks the hub to `@login_required`, and his four public documents already have a working public home. Adding an anonymous render path for four documents means a whole no-member template branch. |
| **Mirroring `Record` / `Equipment` / `Person` / minutes** | §1.1. |
| **A word-level diff or version history of a governance document** | Beautiful on his box; 253 lines of vanilla JS (brief §3). FOG's mirror shows the current version; git is the history and Morlock's app renders it for the people who need it. |
| **An `audit_snapshot_safety` equivalent** | His replays every historical revision through the real filter. FOG stores no revisions of governance content, so there is nothing to replay. If FOG ever keeps them, this comes back — which is part of why `governance_policy.py` is importable (§3). |
| **Auto-linking equipment or guild names in governance prose** | His `kb/links.py` cross-links people and docs by name. Tempting and out of scope: a governance document that auto-links "Woodworking" into a FOG guild page is FOG editorializing a document it does not own. |
| **Retiring the external MediaWiki link** | Brief §4 — an ops task on A's timeline, not E's. |

### Deliberate deviations from the reference implementation, in one place

So a reviewer can check each one rather than rediscovering them:

1. **No `reg_id` minting** — removes the counter and its whole failure mode.
2. **No integer level ladder, no `Profile`** — FOG's named roles, set membership instead of `<=`.
3. **No `PUBLIC` rung.**
4. **`search_text` stored in full**, not truncated at 2,000 characters.
5. **No `minutes` register, no `finance` classification** — excluded at the source.
6. **A guarded skip is surfaced in the product**, not only in stdout (`SiteActivity` + a *dismissible* admin
   banner that reappears when the guarded set changes).
7. **Relative `.md` links are resolved or de-linked**, and Google Docs targets are de-linked in the linkify
   callback so bare URLs and hand-written anchors are covered too — he renders all of them as-is.
8. **Rendering goes through `membership/markdown.py`**, never his hand-rolled `mdconv`. One real cost:
   `mdconv` generates its own heading anchors, so the corpus carries none, and E has to add the `toc`
   extension plus an underscore-tolerant id filter to get a TOC at all (§4.4).
9. **Unlinked documents stay visible to their existing audience**, rather than being quietly demoted.
10. **`min_role` defaults to `ADMIN`, one rung above his level-20 default** — publishing is deliberate at both
    rungs, because FOG's `LEADERSHIP` is wider than his "guild leads and officers" (§4.2).
11. **Images are dropped and reported**, not rendered. He serves the repo's binaries; FOG does not proxy
    private-repo files, so the sync says so per file instead of losing a figure silently.
