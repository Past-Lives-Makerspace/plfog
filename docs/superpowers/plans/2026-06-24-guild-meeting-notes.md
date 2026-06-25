# Guild Meeting Notes — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-24
**Surface:** FOG hub (`pastlives.test:8000`) — guild pages (`templates/hub/guild_detail.html`) + a new dedicated management area, reached from the guild edit page.
**Related:** Builds on the guild-pages expansion (`2026-06-21-guild-pages-expansion.md`) and mirrors the dedicated-editor architecture of guild orientations (`2026-06-21-guild-orientations.md`).

---

## 1. Summary

Guild staff can post **meeting notes / agendas** on their guild's page — one entry per meeting, with a date, a title, an optional written-up body (Markdown), and any number of **attachments**, where each attachment is either an uploaded file (PDF/Word/slides/etc.) or an external doc link (e.g. a Google Doc). Any member who can see the guild page can read past meeting notes in a clean, reverse-chronological list; files are plain download links (we do **not** render PDFs in-app). Staff and leads add, edit, and delete notes from a dedicated management page, reached from the guild's edit page and from a staff-only button on the public Meeting Notes tab.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Entry shape | **Bundle per meeting.** One `GuildMeetingNote` = a meeting date + title + optional Markdown body + any number of child attachments, each of which is **either** an uploaded file **or** an external link. Needs a child model (`GuildMeetingNoteAttachment`) and an inline-formset sub-editor. |
| Typed-text rendering | **Full Markdown.** The body is Markdown source, rendered to **sanitized** HTML. No markdown library is installed yet, so this spec adds `markdown` + `bleach` to `requirements.txt`, a render helper, and a template filter. Sanitize with a `bleach` allowlist; links get `rel`/`target` hardening. |
| Who can VIEW notes | **Any member who can view the guild page.** Reading is open to all members; add/edit/delete is gated to staff/leads via `can_edit_guild`. |

## 2. What already exists (reuse, don't reinvent)

All confirmed in the codebase — the build is assembly. The note + attachment pair closely mirrors `GuildAnnouncement` (own form + own save + delete) and `GuildImage` (child upload row with `delete_orphan_on_replace`).

| Need | Existing thing | Location |
|---|---|---|
| Parent guild + related-content home | `Guild`; related-content models live right after it | `membership/models.py:635` (Guild), child models from `:896` on |
| Child upload row pattern (file + sort + `save()` orphan cleanup) | `GuildImage` — `ImageField(upload_to=…, validators=[validate_image_size])`, `sort_order`, `created_at`; `save()` → `delete_orphan_on_replace(self, "image")` | `membership/models.py:896-916` |
| "Own form + own Save + list with delete" precedent | `GuildAnnouncement` + `GuildAnnouncementQuerySet.active()` + `is_active` property + its own form/section in the edit page | `membership/models.py:949-992` |
| Orphan-file cleanup on replace | `delete_orphan_on_replace(instance, field_name)` (call in `save()` before `super().save()`) | `core/files.py:8-28` |
| Size-cap validator to mirror for documents | `validate_image_size` (reads a settings byte cap, raises `ValidationError`) | `core/validators.py:10-18` |
| Edit-permission source of truth | `can_edit_guild(request, guild)` (admin/officer OR lead OR any `GuildStaffMembership`) | `membership/permissions.py:51-56` |
| View-level 403 gate | `_require_can_edit_guild(request, guild)` → returns 403 `HttpResponse` or `None` | `hub/views.py:489-493` |
| Guild page (read) + tab strip | `guild_detail` view; tab strip (Overview · Guild Calendar · Buyables · FAQ) | `hub/views.py:374-487`; `templates/hub/guild_detail.html:100-106` |
| "Tab that is a link to a dedicated editor page" precedent | Orientations tab is an `<a>` to `hub_guild_orientation_edit`, not an inline tab | `templates/hub/guild_edit.html:14-15` |
| Dedicated page hosting a settings form + a repeated-row formset (+ Add / per-row Delete) | `orientation_settings.html` + `OrientationAvailabilityFormSet` (`extra=0, can_delete=True`) | `templates/hub/orientation_settings.html:33-58`; `hub/forms.py:529-531` |
| **Correct** editable-list pattern to copy (clone `empty_form` "+ Add", hidden `DELETE` driven by a real `pl-btn--danger pl-btn--sm`) | **Links** editor | `templates/hub/guild_edit.html:133-183` |
| Pattern to **avoid** (renders `DELETE` through `form_field.html` as a toggle) | FAQ editor | `templates/hub/guild_edit.html:88-131` |
| `CheckConstraint(condition=Q(...))` precedent (exactly-one / comparison guards) | `OrientationAvailability` end-after-start constraint | `membership/models.py:1670-1673` |
| File storage (S3 in prod via R2, local `MEDIA_ROOT` in dev) | `STORAGES` dict; `django-storages[s3]` already a dep | `plfog/settings.py:269`; `requirements.txt:13` |
| Form field / component library | `form_field.html`, `confirm_modal.html`, `toggle.html` | `templates/components/` |
| Factories to mirror | `GuildImageFactory` / `GuildAnnouncementFactory` (and `GuildFactory`, `MemberFactory`) | `tests/membership/factories.py:71-105` |
| Model spec home / view-template spec home | `tests/membership/guild_content_models_spec.py`; `tests/hub/guild_*_spec.py` | `tests/membership/`, `tests/hub/` |

### Genuine gaps to close (kept small)

1. **No Markdown stack.** `markdown` and `bleach` are not installed (confirmed: no import anywhere). Add both to `requirements.txt`, plus a render helper and a template filter (§5.1).
2. **No document validator.** There is only `validate_image_size`. Add a new `validate_document` (extension allowlist + size cap) in `core/validators.py` (§5.2).
3. **No `GuildMeetingNote*` models** — the two new models in §4.

## 3. Where the code lives

Mirror the guild-content architecture exactly: **models + business logic in `membership`** (right after the other `Guild*` content models), **views + templates in `hub`** (next to `guild_detail` / `guild_orientation_edit`). No new Django app — everything stays inside the existing `membership` + `hub` coverage/mypy scope.

```
membership/models.py            # + GuildMeetingNote, GuildMeetingNoteAttachment (+ body_html helper)
membership/markdown.py          # render_markdown() — markdown → bleach-sanitized HTML (NEW, small)
membership/templatetags/membership_md.py   # {{ note.body|guild_markdown }} filter (NEW templatetag module)
core/validators.py              # + validate_document
core/files.py                   # (reused as-is — delete_orphan_on_replace)
hub/forms.py                    # GuildMeetingNoteForm + GuildMeetingNoteAttachmentForm (w/ XOR clean) + FormSet
hub/views.py                    # guild_meeting_notes, guild_meeting_note_edit, guild_meeting_note_delete
                                #   (+ guild_detail gains meeting-notes context)
hub/urls.py                     # + 3 routes under /guilds/<pk>/meeting-notes/
plfog/settings.py               # + MAX_UPLOAD_DOCUMENT_BYTES, ALLOWED_DOCUMENT_EXTENSIONS
requirements.txt                # + markdown, bleach
static/css/hub.css              # + .pl-md (rendered-Markdown body container)
static/css/components.css       # + .pl-form-group input[type="file"]::file-selector-button (NEW — see §6 Screen C)
templates/hub/guild_detail.html # + "Meeting Notes" tab + panel (read)
templates/hub/guild_edit.html   # + "Meeting Notes" tab-link → management page (mirror Orientations link)
templates/hub/guild_meeting_notes.html       # management list page (NEW)
templates/hub/guild_meeting_note_edit.html   # add/edit page w/ attachment sub-editor (NEW)
tests/membership/factories.py   # + GuildMeetingNoteFactory, GuildMeetingNoteAttachmentFactory
tests/membership/guild_meeting_notes_models_spec.py   # NEW
tests/hub/guild_meeting_notes_spec.py                  # NEW (views + templates)
plfog/version.py                # version bump + member-friendly changelog (final phase)
```

## 4. Data model (`membership/models.py`)

Both models placed immediately after `GuildAnnouncement` (`membership/models.py:992`), before `GuildMembership`.

### 4.1 `GuildMeetingNote` — one entry per meeting

| Field | Type | Notes |
|---|---|---|
| `guild` | `ForeignKey(Guild, on_delete=CASCADE, related_name="meeting_notes")` | Parent guild. `help_text="Parent guild."` |
| `meeting_date` | `DateField` | `help_text="The date this meeting took place (or is scheduled for)."` |
| `title` | `CharField(max_length=300)` | `help_text="Short headline, e.g. 'June general meeting'."` |
| `body` | `TextField(blank=True, default="")` | **Markdown source.** `help_text="Optional written notes. Supports Markdown — bold, lists, links."` |
| `created_by` | `ForeignKey(AUTH_USER_MODEL, null=True, blank=True, on_delete=SET_NULL, related_name="+")` | Who posted it. Mirrors `GuildAnnouncement.author`. `help_text="Who posted these notes."` |
| `created_at` | `DateTimeField(auto_now_add=True)` | |
| `updated_at` | `DateTimeField(auto_now=True)` | |

```python
class Meta:
    ordering = ["-meeting_date", "-created_at"]   # newest meeting first; tie-break newest post

def __str__(self) -> str:
    return f"{self.title} — {self.guild.name} ({self.meeting_date:%Y-%m-%d})"

@property
def body_html(self) -> str:
    """Body Markdown rendered to sanitized HTML (safe to mark_safe in templates)."""
    from membership.markdown import render_markdown
    return render_markdown(self.body)
```

> `body_html` is convenience; templates use the `|guild_markdown` filter (§5.1) which is the single rendering path. Both call the same `render_markdown`.

No new manager is required — the default related manager on `guild.meeting_notes` plus `Meta.ordering` covers the page query. (`GuildAnnouncement`'s `active()` exists only because announcements expire; meeting notes never expire, so no queryset subclass.)

### 4.2 `GuildMeetingNoteAttachment` — a file **or** a link, repeated per note

Mirrors `GuildImage` for the upload mechanics (`upload_to`, `sort_order`, `created_at`, `save()` → `delete_orphan_on_replace`) but with a `FileField` (not `ImageField`) and **no** image normalization.

| Field | Type | Notes |
|---|---|---|
| `note` | `ForeignKey(GuildMeetingNote, on_delete=CASCADE, related_name="attachments")` | `help_text="Parent meeting note."` |
| `label` | `CharField(max_length=200, blank=True, default="")` | Friendly display name. `help_text="What to call this — e.g. 'Agenda PDF'. Defaults to the file name or link."` |
| `file` | `FileField(upload_to="guilds/meeting_notes/", blank=True, validators=[validate_document])` | `help_text="Upload a document (PDF, Word, slides, spreadsheet…). Leave blank if you're adding a link instead."` |
| `url` | `URLField(blank=True, default="")` | `help_text="Link to an external doc (e.g. a Google Doc). Leave blank if you uploaded a file instead."` |
| `sort_order` | `PositiveIntegerField(default=0)` | `help_text="Ascending; lower shows first."` |
| `created_at` | `DateTimeField(auto_now_add=True)` | |

**Exactly one of `file` / `url`** — enforced two ways. The **friendly per-row validation lives on the form** (`GuildMeetingNoteAttachmentForm.clean()` in `hub/forms.py`, §5.3), because the formset runs each *form's* `clean()` and attaches a row-level error there — that's what the user sees. The **model keeps only a DB `CheckConstraint`** as an integrity backstop (XOR over the two columns, both of which store `""` when empty — matching the `OrientationAvailability` `CheckConstraint(condition=Q(...))` style at `membership/models.py:1670`):

```python
class Meta:
    ordering = ["sort_order", "created_at"]
    constraints = [
        models.CheckConstraint(
            condition=((Q(file="") & ~Q(url="")) | (~Q(file="") & Q(url=""))),
            name="ck_meetingnoteattachment_file_xor_url",
        ),
    ]
```

> Do **not** rely on a *model* `clean()` for the XOR — `Model.clean()` is not run by the formset's save path automatically, so a bad row would reach the DB and raise an `IntegrityError`/500 instead of the friendly per-row message. The **form** `clean()` (§5.3) is the user-facing guard; the `CheckConstraint` is the last-resort backstop.

```python
def save(self, *args, **kwargs) -> None:
    delete_orphan_on_replace(self, "file")   # mirror GuildImage; no normalize_field_if_uploaded
    super().save(*args, **kwargs)

def __str__(self) -> str:
    return f"Attachment #{self.pk} for {self.note.title}"
```

Properties:

```python
@property
def is_file(self) -> bool:
    return bool(self.file)

@property
def is_link(self) -> bool:
    return bool(self.url)

@property
def display_name(self) -> str:
    """Label if set, else the file's base name, else the URL."""
    if self.label:
        return self.label
    if self.file:
        return self.file.name.rsplit("/", 1)[-1]
    return self.url
```

### 4.3 Migration

One migration in `membership/migrations/` creating both tables + the `CheckConstraint`. Reverse is a normal `DeleteModel` pair (Django generates a working reverse automatically — no `RunPython`, so no custom reverse function needed). Run `ruff format` on the generated migration and `git add` it in the same commit (per the migrations-need-ruff-format note).

## 5. Business logic (fat models / helpers; views stay thin)

### 5.1 Markdown render helper + template filter

`membership/markdown.py`:

```python
import bleach
import markdown as md

_ALLOWED_TAGS = [
    "p", "br", "h1", "h2", "h3", "h4",
    "strong", "em", "ul", "ol", "li",
    "a", "code", "pre", "blockquote", "hr",
]
_ALLOWED_ATTRS = {"a": ["href", "title"]}

def render_markdown(source: str) -> str:
    """Render Markdown to HTML, then sanitize: drop scripts/styles/unknown tags,
    harden links. Returns a string the template marks safe via the filter."""
    if not source:
        return ""
    raw = md.markdown(source, extensions=["extra", "sane_lists"])
    cleaned = bleach.clean(raw, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
    # Add rel + target to every surviving link.
    linkified = bleach.linkify(
        cleaned,
        callbacks=[_harden_link],          # sets rel + target
        parse_email=False,
    )
    return linkified
```

`_harden_link` sets `rel="noopener nofollow noreferrer"` and `target="_blank"` on every anchor (both author-written and auto-linked). Scripts, styles, `onclick`, `style=` attributes, and any tag outside the allowlist are stripped by `bleach.clean(strip=True)`.

`membership/templatetags/membership_md.py`:

```python
from django import template
from django.utils.safestring import mark_safe
from membership.markdown import render_markdown

register = template.Library()

@register.filter(name="guild_markdown")
def guild_markdown(value: str) -> str:
    return mark_safe(render_markdown(value or ""))   # safe ONLY because render_markdown sanitizes
```

Template usage: `{% load membership_md %}` then `<div class="pl-md">{{ note.body|guild_markdown }}</div>`. The body is **only ever** displayed through this filter (or `body_html`), never via `|safe` on raw `body`.

### 5.2 `validate_document` (in `core/validators.py`, beside `validate_image_size`)

```python
ALLOWED_DOCUMENT_EXTENSIONS = {
    "pdf", "doc", "docx", "odt", "ppt", "pptx",
    "xls", "xlsx", "csv", "txt", "md", "rtf",
}

def validate_document(upload: UploadedFile) -> None:
    """Reject documents over the size cap or with a disallowed extension."""
    name = (getattr(upload, "name", "") or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))
        raise ValidationError(f"Unsupported file type '.{ext}'. Allowed: {allowed}.")
    limit = settings.MAX_UPLOAD_DOCUMENT_BYTES
    size = getattr(upload, "size", None)
    if size is not None and size > limit:
        raise ValidationError(f"File must be {limit / (1024 * 1024):.0f} MB or smaller.")
```

`plfog/settings.py`: add `MAX_UPLOAD_DOCUMENT_BYTES = 25 * 1024 * 1024` (25 MB) and reference the extension set. (`ALLOWED_DOCUMENT_EXTENSIONS` can live on the validator module; settings owns only the byte cap, matching how `validate_image_size` reads `MAX_UPLOAD_IMAGE_BYTES`.)

### 5.3 Forms (`hub/forms.py`)

- `GuildMeetingNoteForm(ModelForm)` — fields `meeting_date`, `title`, `body`. `meeting_date` is a plain `forms.DateInput(attrs={"type": "date"})` — **no manual `onclick`/`showPicker` or `filter: invert(1)` needed.** Because the field renders inside `form_field.html`'s `.pl-form-group` wrapper, it already gets dark/light date-picker handling via `color-scheme: dark` (components.css:218-221) and the `color-scheme: light` override (:662-666). (The `showPicker`/`filter: invert` pattern from FRONTEND rule 14 is for the session-calendar *custom* popover — a different control — not a standard `.pl-form-group` date field.) `body` is a `Textarea`. No `guild`/`created_by` in the form — set in the view.
- `GuildMeetingNoteAttachmentForm(ModelForm)` — fields `label`, `file`, `url`, `sort_order`. **The exactly-one-of XOR validation lives here** as the user-facing guard (the formset runs each form's `clean()` and shows the row-level error):

  ```python
  def clean(self) -> dict[str, Any]:
      cleaned = super().clean()
      has_file = bool(cleaned.get("file"))
      has_url = bool(cleaned.get("url"))
      if has_file == has_url:   # both empty or both filled
          raise ValidationError("Each attachment needs exactly one of: an uploaded file OR a link.")
      return cleaned
  ```

  (Skip the check on rows flagged for deletion, à la the `OrientationAvailabilityForm.clean()` pattern at `hub/forms.py:520`.)
- `GuildMeetingNoteAttachmentFormSet = forms.inlineformset_factory(GuildMeetingNote, GuildMeetingNoteAttachment, form=GuildMeetingNoteAttachmentForm, extra=0, can_delete=True)` — mirrors `OrientationAvailabilityFormSet` (`hub/forms.py:529`). `extra=0` so no perpetual blank row blocks save.

### 5.4 Views (`hub/views.py`, all `@login_required`, thin)

| View | Job | Gate |
|---|---|---|
| `guild_meeting_notes(request, pk)` | Render the management list (notes + Edit/Delete). | `_require_can_edit_guild` |
| `guild_meeting_note_edit(request, pk, note_pk=None)` | Add (no `note_pk`) or edit. Multipart. Binds `GuildMeetingNoteForm` + attachment formset; on valid, sets `guild`/`created_by` (on create), saves, `messages.success`, redirects to the management list. | `_require_can_edit_guild` |
| `guild_meeting_note_delete(request, pk, note_pk)` | POST-only; deletes the note (CASCADE removes attachments; their `FileField`s are R2 objects — note that `delete_orphan_on_replace` only fires on *replace*, so a bulk delete should iterate or rely on a `post_delete` signal if we want the storage objects gone — see §10). `messages.success`, redirect. | `_require_can_edit_guild` |

`guild_detail` gains read-only context: `meeting_notes = guild.meeting_notes.prefetch_related("attachments")` and `can_edit_this_guild` (already computed at `hub/views.py:393`) to decide whether the staff "Manage" button + empty-tab visibility show.

Attachment deletion is handled **purely by the formset's `DELETE`** on the edit page (preferred — no separate endpoint), exactly like the Links editor. Removing a whole note is the only standalone delete endpoint.

### 5.5 URLs (`hub/urls.py`)

```python
path("guilds/<int:pk>/meeting-notes/", views.guild_meeting_notes, name="hub_guild_meeting_notes"),
path("guilds/<int:pk>/meeting-notes/add/", views.guild_meeting_note_edit, name="hub_guild_meeting_note_add"),
path("guilds/<int:pk>/meeting-notes/<int:note_pk>/edit/", views.guild_meeting_note_edit, name="hub_guild_meeting_note_edit"),
path("guilds/<int:pk>/meeting-notes/<int:note_pk>/delete/", views.guild_meeting_note_delete, name="hub_guild_meeting_note_delete"),
```

## 6. UI / UX  ← completeness checklist applied per screen

Three screens. All use `<div class="hub-card">` sections, `pl-`-prefixed classes, theme tokens only, and the component library.

---

### Screen A — Guild page "Meeting Notes" tab (read)

- **Template:** `templates/hub/guild_detail.html` (tab strip at lines 100-106; this is an Alpine `x-data="{ section: 'overview' }"` strip of `vote-tab` buttons).
- **Layout & container:** a new `vote-tab` button + a matching `<div x-show="section === 'notes'">` panel, alongside the existing Overview / Guild Calendar / Buyables / FAQ panels.
- **Tab visibility logic:** show the **Meeting Notes** tab when `meeting_notes` is non-empty **OR** `can_edit_this_guild` (so staff can always reach the empty state to add the first note). Hide it for non-staff when there are zero notes — matching how the FAQ tab is conditional (`guild_detail.html:105`).
- **Components used:** none of the form components (read-only); the `|guild_markdown` filter for body; standard `hub-card`.
- **The content, named explicitly:**
  - **Per-note card** (`pl-guild-section` / `hub-card`, reverse-chron): a header row with **meeting date** (`{{ note.meeting_date|date:"F j, Y" }}`) + **title** (`<h3>`); then the rendered body inside `<div class="pl-md">{{ note.body|guild_markdown }}</div>` (omitted entirely if body is blank); then an **Attachments** list.
  - **Attachment row:** for a file → a download link `<a href="{{ att.file.url }}" download>` with a document icon and `att.display_name`; for a link → `<a href="{{ att.url }}" target="_blank" rel="noopener nofollow noreferrer">` with an external-link icon and `att.display_name`. Files and links can interleave; the list `flex-wrap`s.
  - **Staff-only "Manage meeting notes" button:** `{% if can_edit_this_guild %}` a `hub-btn hub-btn--sm` linking to `hub_guild_meeting_notes`, sitting at the top of the panel.
- **States:**
  - **Empty (staff):** "No meeting notes yet. Post your first agenda or recap so members can catch up." + the **Manage / Add** button. (Non-staff never see an empty tab — the tab itself is hidden.)
  - **Loading:** none — server-rendered inline with the page (no HTMX on this tab).
  - **Error:** a missing file URL still renders the row by `display_name`; broken external links are the member's browser's problem. No 500 path here.
  - **Success:** n/a (read-only).
- **Dark + light:** body comes through `.pl-md` (new class in `hub.css`) — set `color: var(--hub-text)`, link color `var(--color-tuscan-yellow)` (confirm the rendered `<a>` is visibly distinct from body text in **both** themes), and spacing for `ul/ol/blockquote/code/pre`. Any tinted inset background on `code`/`pre`/`blockquote` uses **`--hub-surface`** (a real token) — **never `--surface`** (not a defined token → silently falls back to white, FRONTEND rule 13). Verify both themes. No inline `background`/`color` on anything. (No form controls on this screen, so no input-token concern.)
- **Mobile:** note cards already stack (single-column `pl-guild-grid` collapses); the attachment list `flex-wrap`s so download links wrap rather than overflow; tap targets are full `<a>` rows, not tiny icons.

---

### Screen B — Management list page

- **Template:** `templates/hub/guild_meeting_notes.html` (new). Structure mirrors `orientation_settings.html` top matter: a "← Back to {{ guild.name }}" ghost button, an `<h1 class="hub-page-title">`, a one-line muted description.
- **Reached from:** (1) a new **Meeting Notes** tab on `guild_edit.html` rendered as an `<a>` link — **mirror the Orientations tab-link at `guild_edit.html:14-15`**, not an inline tab (file + formset editing is heavier than a single section); (2) the staff button on Screen A.
- **Layout & container:** a `hub-card` list. Each row: meeting date + title (+ a small "N file(s) / M link(s)" count), with two controls.
- **Components used:** `confirm_modal.html` for delete.
- **The controls, named explicitly:**
  - **"+ Add meeting notes"** — a primary `pl-btn pl-btn--primary` at the top, linking to `hub_guild_meeting_note_add`. (This is the page's obvious primary action.)
  - **Per row Edit** — `hub-btn hub-btn--sm` → `hub_guild_meeting_note_edit`.
  - **Per row Delete** — a **trigger button** plus a sibling `confirm_modal.html` include whose `confirm_id` matches. `confirm_modal.html` opens on the `@open-confirm.window` event when `$event.detail` equals its `confirm_id` (`confirm_modal.html:17`), so the trigger must dispatch that event with the same id:
    ```html
    <button type="button" class="pl-btn pl-btn--danger pl-btn--sm"
            @click="$dispatch('open-confirm', 'delete-note-{{ note.pk }}')">Delete</button>
    {% include "components/confirm_modal.html" with confirm_id="delete-note-{{ note.pk }}" confirm_title="Delete these meeting notes?" confirm_message="This removes the "|add:note.meeting_date|add:" notes and all their attachments. This can't be undone." confirm_action_url=... confirm_button_text="Delete notes" %}
    ```
    The `confirm_id` on the include **must** match the id dispatched by the trigger (`delete-note-{{ note.pk }}`). The modal's own form does the full-page POST to `hub_guild_meeting_note_delete` → `messages.success` → redirect back here.
- **States:**
  - **Empty:** "No meeting notes yet. Add your first one." + the **+ Add** button (not a bare blank region).
  - **Loading:** none (server-rendered; confirm modal is client-side Alpine).
  - **Error:** delete of a missing note → 404 via `get_object_or_404`; non-staff → 403 from `_require_can_edit_guild`.
  - **Success:** Django `messages.success` ("Meeting notes deleted.") shown on redirect (full-page action, so messages — not a toast — per the interaction table).
- **Dark + light:** all `hub-card` + `pl-btn`/`hub-btn` tokens; verify both. No raw inputs on this page.
- **Mobile:** rows stack; Edit/Delete buttons drop below the title on narrow widths (flex-wrap), staying full-size tap targets.

---

### Screen C — Add / Edit note page (dedicated page, not a modal)

A dedicated page (not a modal) because it has a file input **and** a repeated formset **and** validation re-render — all fragile inside a modal (FRONTEND interaction table: 4+ fields / file upload → dedicated page).

- **Template:** `templates/hub/guild_meeting_note_edit.html` (new). Title "Add meeting notes" / "Edit meeting notes". Back link to the management page.
- **Form:** `<form method="post" enctype="multipart/form-data" class="hub-form">` (multipart is mandatory for the file inputs).
- **Components used:** `form_field.html` for every field.
- **Note fields (top `hub-card`):**
  - `{% include "components/form_field.html" with field=form.meeting_date %}` — `<input type="date">`. **No manual `showPicker`/`filter: invert`.** It renders inside `form_field.html`'s `.pl-form-group` wrapper, which already gives date inputs dark/light picker handling via `color-scheme: dark` (components.css:218-221) + the `color-scheme: light` override (:662-666). Just render the normal field.
  - `{% include "components/form_field.html" with field=form.title %}` — required.
  - `{% include "components/form_field.html" with field=form.body field_hint="Supports Markdown — **bold**, lists, links. Optional." %}` — the `<textarea>` renders **inside `.pl-form-group`** (form_field's wrapper for non-checkbox fields, components.css:165), so it inherits the theme input tokens (`.pl-form-group input/textarea/select` are fully tokenized) and is **not** a bare white box (FRONTEND rule 13). Optional.
- **Attachment sub-editor (second `hub-card`, "Files & links"):** copy the **Links editor** (`guild_edit.html:133-183`) in shape for the row layout + the clone-`empty_form` "+ Add" JS — **but the Delete button must NOT be copied from Links.** (The Links saved-row Delete at `guild_edit.html:~146` contains `this.form.after.value = 'edit'`, which targets a hidden `after` field that exists only on the guild_edit page; this edit page has no such field, so `this.form.after` is `undefined` → `TypeError` → Delete silently never submits.)
  - `{{ attachment_formset.management_form }}`, a `#note-attachment-rows` container, one `hub-card` per existing row.
  - Each row: `label` + `file` + `url` laid out with the Links editor's flex row (`label` flex:1, then `file` and `url`), plus a hint line "Add **one** per row: upload a file *or* paste a link." For a saved row, also show the current file name / link as text.
  - **"+ Add file or link"** button — clones a hidden `<template>` of `attachment_formset.empty_form`, replaces `__prefix__`, bumps `id_<prefix>-TOTAL_FORMS` (exactly the Links `+ Add a link` JS). `extra=0`.
  - **Per-row Delete (saved row)** — copy the **recurring-hours Delete in `orientation_settings.html` (lines ~50-54)** *verbatim*, not the Links one: `<div style="display:none;">{{ f.DELETE }}</div>` + a real `pl-btn pl-btn--danger pl-btn--sm` with `style="margin-top:0.75rem;"` and `onclick="document.getElementById('{{ f.DELETE.id_for_label }}').checked = true; this.form.requestSubmit();"`. **Drop the `this.form.after.value = 'edit'` line** from the Links pattern entirely. **Unsaved cloned row:** a **Remove** `pl-btn pl-btn--danger pl-btn--sm` that just removes the DOM node. **Never** a DELETE toggle (do not copy the FAQ editor).
- **Save/submit:** a `pl-btn pl-btn--primary` **"Save"** + a `pl-btn pl-btn--secondary` **"Cancel"** link to the management page, in a flex row with `gap:1rem` at the bottom. On valid POST → save note + attachments, `messages.success("Meeting notes saved.")`, redirect to `hub_guild_meeting_notes`.
- **Validation messages (explicit):**
  - Missing `title` or `meeting_date` → the field's inline error via `form_field.html`.
  - An attachment row with **both file and url** OR **neither** → "Each attachment needs exactly one of: an uploaded file OR a link." (form `clean()`, §5.3).
  - File too big → "File must be 25 MB or smaller." (validator).
  - Wrong extension → "Unsupported file type '.xyz'. Allowed: csv, doc, docx, …" (validator).
- **Re-render on error:** the page re-renders with all bound values and inline errors. **Already-saved attachments survive** (they re-render from the instance). **Caveat to surface in the UI:** a browser **cannot** re-populate a chosen-but-unsaved file input after a validation error — so if the form bounces, that row's file picker is empty again. The server-side error copy must be unambiguous ("Re-attach the file for '<label>' — it wasn't saved because of the error above."), and rows that *did* save show their saved file so the user knows what's already there.
- **States:**
  - **Empty (add mode):** the note fields blank; the attachment editor shows **zero rows** (`extra=0`) plus the "+ Add file or link" button and a one-line hint "No files or links yet — add one below." So it's never a blank confusing region.
  - **Loading:** none (full-page POST). The Save button can get a disabled/pending style on submit if desired, but no HTMX spinner.
  - **Error:** as above — friendly inline errors, page stays put, no 500.
  - **Success:** redirect + Django success message.
- **Dark + light:** every control goes through `form_field.html`/`.pl-form-group` → theme input tokens; the date field is handled by `.pl-form-group`'s `color-scheme` rules (no manual invert needed). **One genuinely new style is required:** `<input type="file">`'s native `::file-selector-button` has **no rule in any CSS file today**, so on the dark theme it renders as an OS-default light button. Add a `.pl-form-group input[type="file"]::file-selector-button` rule (theme input tokens: `background: var(--hub-input-bg)`, `color: var(--hub-text)`, `border: 1px solid var(--hub-input-border)`) in `components.css`, with the `[data-theme="light"]` counterpart — flagged as a **new** style, not something that "just works." **No inline `background`/`color`** on any input/textarea/select; if any wrapper uses `x-show`, layout lives in a CSS class, never inline `display` (FRONTEND rule 12). Verify both themes.
- **Mobile:** the attachment row's `label`/`file`/`url` flex columns wrap to full width (`min-width` + `flex-wrap`, like the Links editor); the Delete/Remove button clears the field above it (`margin-top:0.75rem`); Save/Cancel stack.

## 7. Notifications / emails / activity

**None for v1.** No per-note emails, push, or `SiteActivity` rows — posting meeting notes is a quiet content action, like editing the guild About text or adding a FAQ (neither notifies). Adding notifications later is a clean follow-up (§10) and out of scope here.

## 8. Build order (phased; each phase ships green)

Each phase lands green (full suite + `ruff format`/`ruff check` + `mypy`), run in the `plfog-web` Docker image.

1. **Markdown + validator infra.** Add `markdown` + `bleach` to `requirements.txt`; `membership/markdown.py` (`render_markdown` + `_harden_link`); `membership/templatetags/membership_md.py` (`guild_markdown` filter); `validate_document` + `MAX_UPLOAD_DOCUMENT_BYTES` in `core`/settings. Specs for sanitization (script stripped, link hardened) and the validator. **No models yet.**
2. **Models + forms.** `GuildMeetingNote`, `GuildMeetingNoteAttachment` (+ XOR `CheckConstraint`, `clean()`, properties, `save()` orphan cleanup, `body_html`), migration (formatted + committed together), factories, `GuildMeetingNoteForm` + `GuildMeetingNoteAttachmentFormSet`. Model specs (XOR constraint, ordering, `display_name`, orphan cleanup). **No UI.**
3. **Read tab.** `guild_detail` context + the Meeting Notes tab/panel in `guild_detail.html`, `.pl-md` styles in `hub.css`. Tab-visibility + empty-state + attachment-rendering specs.
4. **Management + add/edit editor.** `guild_meeting_notes` / `guild_meeting_note_edit` / `guild_meeting_note_delete` views + URLs; the two new templates; the `guild_edit.html` tab-link; the attachment sub-editor (clone Links editor); `confirm_modal` delete. View-gating + form-validation + formset add/delete + template-state specs.
5. **Housekeeping.** **Bump `plfog/version.py` `VERSION`** (patch on `release-0.19.x`) + a plain-language, member-friendly **CHANGELOG** entry (e.g. *"Guild staff can now post meeting notes and agendas on the guild page — with a date, write-up, and any files or links members can download. Members can read past meetings in a clean list."*). Finalize this doc's status.

> Spec only — do not build until approved.

## 9. Testing (BDD `*_spec.py`, ≥98% gate, run in Docker `plfog-web`)

New factories in `tests/membership/factories.py`: `GuildMeetingNoteFactory` (mirror `GuildAnnouncementFactory`) and `GuildMeetingNoteAttachmentFactory` (mirror `GuildImageFactory`; a `file` trait and a `url` trait so a test can pick either — never both). All `describe_*` / `it_*`, factory-boy, no DELETE-toggle anti-patterns.

- **Model — `tests/membership/guild_meeting_notes_models_spec.py`:**
  - XOR constraint: an attachment with **both** file and url raises `IntegrityError`; with **neither** raises `IntegrityError`; with exactly one saves. (Plus the `clean()` raising `ValidationError` for the same two bad cases — the friendly path.)
  - `Meta.ordering`: notes come back newest-meeting-first, tie-broken by `-created_at`; attachments by `sort_order`.
  - `display_name`: label wins; else file base name; else url.
  - `is_file` / `is_link` correctness.
  - `delete_orphan_on_replace` on `file` replace (mirror the GuildImage spec).
  - `__str__` for both models.
- **Markdown sanitization (Phase-1 spec):**
  - A `<script>` (and `<style>`, `onclick=`, `javascript:` href) in the body is stripped — assert it's absent from `render_markdown(...)`.
  - Allowed Markdown (`**bold**`, `- list`, `[x](https://…)`) renders to the expected tags.
  - Every output `<a>` gets `rel="noopener nofollow noreferrer"` + `target="_blank"`.
  - `validate_document`: bad extension raises; oversize (mock `.size`) raises; allowed small file passes.
- **Views/templates — `tests/hub/guild_meeting_notes_spec.py`:**
  - **Gating:** a non-staff member **can GET** the guild page and see the notes tab/panel when notes exist; a non-staff member **gets 403** on `guild_meeting_notes` / `_add` / `_edit` / `_delete`; a lead and a `GuildStaffMembership` member get 200/can mutate (mirror existing guild_edit gating specs).
  - **Tab visibility:** hidden for non-staff with zero notes; shown for staff with zero notes (empty state present); shown for everyone when ≥1 note.
  - **Form validation through the view:** posting an attachment row with both/neither file+url re-renders with the friendly error and saves nothing; a too-big / wrong-extension file re-renders with the validator message.
  - **Formset add/delete:** adding a second attachment row persists both; deleting a saved row (DELETE flag) on edit removes just that attachment and keeps the note + other rows.
  - **Delete:** POST to `guild_meeting_note_delete` removes the note (and attachments cascade); GET/other methods don't.
  - **Template states:** empty management page shows the empty copy + Add button; rendered note shows file rows as download links and url rows with `rel`/`target`.
- **Gotchas:** use a real small in-memory file (`SimpleUploadedFile`) for file-attachment fixtures; for `meeting_date` ordering, set explicit dates (don't rely on `auto_now_add` for the meeting field). Keep file fixtures tiny so the suite stays fast; clean up via `MEDIA_ROOT` tmp (factory `file` trait should write into the test storage).

## 10. Open / deferred

- **Storage cleanup on cascade delete.** `delete_orphan_on_replace` only frees the R2 object on *replace*. Deleting a whole note CASCADEs the attachment rows but does **not** delete their underlying files. v1 accepts this (mirrors current `GuildImage` behavior on guild/image deletes — orphaned objects are tolerated). A `post_delete` signal that calls `att.file.delete(save=False)` is the clean follow-up if storage hygiene matters; flagged, not built.
- **No in-app PDF rendering / preview** — explicitly out of scope. Files are download links only, as decided.
- **No per-note notifications / emails / activity feed** — out of scope for v1 (§7). Could later fire a `guild_announcement`-style member email or a `SiteActivity` row when notes are posted.
- **No versioning / edit history of notes** — out of scope. Edits overwrite in place (`updated_at` is the only trail).
- **No sort/drag reordering of notes** — they're ordered by `meeting_date`; attachment order uses `sort_order` but the v1 editor sets it implicitly by row index (no drag handle). A drag-reorder is a later nicety.
- **Genuinely new styles/conventions introduced (flagged, not slipped in):**
  - **`.pl-md`** — the rendered-Markdown body container (new class, `hub.css`).
  - **`.pl-form-group input[type="file"]::file-selector-button`** — a new `components.css` rule. Today **no** CSS file styles the native file-selector button, so without this it renders as an OS-default light button on the dark theme. Theme-tokened (`--hub-input-bg` / `--hub-text` / `--hub-input-border` + a `[data-theme="light"]` counterpart).
  - **`MAX_UPLOAD_DOCUMENT_BYTES`** settings constant — follows the existing `MAX_UPLOAD_*` naming.
  - All three extend existing conventions (`pl-` prefix, theme tokens, `MAX_UPLOAD_*`) rather than break them. Confirm the 25 MB cap with the user (could be tighter if R2 egress is a concern).
