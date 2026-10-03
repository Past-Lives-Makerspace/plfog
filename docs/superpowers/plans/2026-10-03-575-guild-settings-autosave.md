# #575 Guild Settings save themselves as you edit

Closes #575. One PR: a shared autosave script, the guild edit page and its save views, CSS, specs and e2e.

## Today

`templates/hub/guild_edit.html` holds a dozen `<form>`s, each posting to its own endpoint behind a Save button, all gated and shaped the same way: validate, save, side effects, redirect back to the tab with a Django message; on an invalid post, either re-render the page with the bound form (main form, orientation settings and types, emails, mailing list, studio hours) or flash an error and drop the input (FAQ, Links). The Leadership Directory editor (`templates/hub/admin/leadership.html`, `hub/leadership_views.py`) already autosaves: one queue, one request at a time, the meeting workspace save pill (`.pl-meeting-savestate`: Saving, Saved, Couldn't save), a 422 contract (`{"errors": {...}}` plus an error toast through `HX-Trigger`, the field reverts), and a redirected answer read as a signed out session.

## The mechanism, once

### Client: `static/js/guild_autosave.js`

One Alpine component `plGuildAutosave`, registered the defensive way (`if (window.Alpine) register(); else document.addEventListener("alpine:init", register);`), loaded from `hub/base.html`'s `<head>` with `defer`, **before** `alpine.min.js`, like `card_focus.js` (FRONTEND.md, Scripts under hx-boost; `tests/hub/base_scripts_spec.py` pins the list, so add the file there). The page root `<div x-data=...>` at the top of `guild_edit.html` becomes `x-data="plGuildAutosave({ section: ... })"` and keeps `section`.

It listens on the root for `input` and `change` and acts on fields inside any `form[data-autosave]`:

- **Typed fields** (text, url, email, number, textarea, the rich text textareas): 800ms after the last keystroke, and at once on `change` (blur). A required field left blank waits for blur, as the leadership editor does.
- **Toggles, selects, radios, date, time, color, file inputs**: at once on `change`.
- **Hours forms** (`data-autosave="change"` on the Studio Hours and Guild Hours forms): every field saves on `change` only, never on the typing pause, because those saves materialize slots and push to Google.
- **A new formset row** (no value in its hidden `id` input) whose required fields are not all filled is posted **as rendered**: its controls' `defaultValue` / `defaultChecked` go into the FormData instead of what is typed, so Django's `empty_permitted` skips it as unchanged and the rest of the form still saves. It posts for real once every required field in the row has a value.

**The post**: the whole form as `FormData(form)` (multipart, so the banner image and FAQ documents ride along), `fetch(form.action, { method: "POST", headers: { "X-Autosave": "1", "X-CSRFToken": token }, credentials: "same-origin" })`, through one promise chain for the page so saves never overlap. A redirected response is the signed out case: toast "You were signed out, so that change was not saved. Reload the page and sign in." and `state = "error"`. Any network failure sets `state = "error"`.

**On 200** `{"saved": true, "rows": {"<prefix>": [pk | null, ...]}}`: for each formset prefix, write every non null pk into that row's hidden `id` input (`<prefix>-<i>-id`), then set `INITIAL_FORMS` to the number of rows now carrying an id (saved rows render first and new rows are appended, so the order holds). Clear inline errors in that form. `done()`.

**On 422** `{"errors": {"<html field name>": ["..."], "<prefix>-<i>-__all__": ["..."], "__all__": ["..."]}}`: show each list under its field as `.pl-field-errors` / `.pl-field-error` (create or reuse the list inside the field's `.pl-form-group`; a row or form level error goes at the top of its row or form), keep what was typed, `state = "saved"` for the pill (the page is not broken) and the toast the server triggered says "Couldn't save that. <first error>". Nothing in that form is saved until the errors clear; the next edit retries.

**Deleting a saved row**: the row's Delete button flips the hidden `DELETE` field and posts through the queue (through the confirm first for an hours row, see below). On 200 the row node is removed and the remaining rows of that formset are renumbered in document order (every `name`, `id` and `for` whose `<prefix>-<n>-` segment is wrong is rewritten) and `TOTAL_FORMS` / `INITIAL_FORMS` reset to the counts. Delete buttons therefore find their `DELETE` input inside their own row (`this.closest('[data-formset-row]').querySelector('input[name$="-DELETE"]')`), never by a baked id. Removing an unsaved row just drops the node and decrements `TOTAL_FORMS` as today.

**Rich text**: `static/js/rich-editor-init.js` keeps the hidden textarea in sync on every edit; make that sync dispatch a bubbling `input` event on the textarea (if it does not already) so the autosave hears it. Check `templates/hub/wiki_edit.html` and the meeting workspace still behave (they have their own autosave and ignore a bubbling event they do not listen for).

**The pill**: the leadership markup, `<span class="pl-meeting-savestate" data-save-pill ...>` next to the page title, with `label()` returning "Saving…", "Saved" or "Couldn't save. Check your connection."

**Leaving**: `beforeunload` holds (`e.preventDefault(); e.returnValue = ""`) while a debounce timer is pending, a request is in flight, or `state === "error"`. A boosted navigation (the sidebar) does not fire it, so bind `htmx:confirm` on `document` once (`window.plGuildAutosaveGuardBound`, the user settings pattern at `templates/hub/user_settings.html`): when a save is pending or failed, hold the request and open `components/confirm_modal.html` `leave-while-saving` ("Changes are still saving. Leave anyway?", Leave / Stay, `confirm_button_style="primary"`); Leave resumes the held request. A pending timer is flushed (its save fires at once) before the hold is decided, so the common case, click a link half a second after typing, just saves and goes.

### Server: `hub/autosave.py`

```python
def wants_autosave(request) -> bool: return request.headers.get("X-Autosave") == "1"
def autosave_saved(rows: dict[str, list[int | None]] | None = None) -> JsonResponse
def autosave_refused(*forms_and_formsets) -> JsonResponse   # 422, errors keyed by html field name, first error toasted
def formset_rows(formset) -> list[int | None]               # pk per form in order, None for deleted or skipped
```

`autosave_refused` flattens `form.errors` (keys are `form.add_prefix(name)`, so formset rows come out as `faq-2-question`; `__all__` becomes `<prefix>-<i>-__all__` for a row, `__all__` for a plain form) and `formset.non_form_errors()` as `<prefix>-__all__`, and triggers the error toast with `hub.toast.trigger_toast` as `_refused` in `hub/leadership_views.py` does.

Every save view below gains the same two lines in each branch: on valid, `if wants_autosave(request): return autosave_saved(...)` after the side effects and before the redirect; on invalid, `if wants_autosave(request): return autosave_refused(...)` before the re-render or the flash. The non autosave path is unchanged, so every existing spec and the JS off case keep working.

| Form (template) | View | Notes |
|---|---|---|
| Main (Basic / Meetings / Images) | `guild_edit` | `rows` empty. The Save Changes button and Cancel go; a "View guild page" link stays. `banner_image` posts on file change (the `image_field.html` preview already swaps). Gallery already saves instantly. |
| Visibility | `guild_visibility_save` | Single toggle, at once. |
| Orientation settings | `guild_orientation_edit` | Regenerates slots on each save, as today. |
| Orientation types | `guild_orientation_types_save` | `rows["otypes"]`. |
| Thank-you email, Welcome email | `guild_emails_save` | `form_id` stays as the hidden field. Preview and Send test keep their buttons. |
| Studio hours | `guild_studio_hours_save` | `data-autosave="change"`; `rows["studio_hours"]`. Delete gains a confirm (below). |
| Guild hours (legacy shared) | `guild_orientation_hours_save` (page path, `guild_rules`) | `data-autosave="change"`; `rows["guild_rules"]`. Its confirm stays. The modal personal hours editor is not touched. |
| FAQ | `guild_faq_save` | `rows["faq"]`; invalid now returns 422 inline instead of the flash. |
| Links | `guild_links_save` | `rows["links"]`; same. |
| Mailing list | `guild_mailing_list_save` | `rows["mailing_list"]`. Import keeps its button. |
| Announcement settings | `guild_announcement_settings_save` | Single toggle. |

**Hours row removal asks once**: the Studio Hours Delete opens `components/confirm_modal.html` ("Delete these hours? Upcoming open times from this window come off the calendar. Booked times stay until you cancel them.") with `confirm_js` that flips the row's `DELETE` and calls the component's `saveNow(form)`; the Guild Hours confirm does the same instead of `requestSubmit()`. Every other row's Delete posts straight away.

### Copy and CSS

Each tab's lead line that said "no need to hit Save" or implied a Save is reread; the page header gains one line under the title: "Every change saves as you make it." Section headings and hints keep their words. No dashes in anything new. New CSS goes in `hub.css` under a `pl-guild-autosave` comment (the pill already exists; a `.pl-formset-row--saving` dim is optional).

## Specs

- `tests/hub/guild_autosave_spec.py`: for each view in the table, an `X-Autosave` POST answers 200 with `saved` and the right `rows` (a FAQ post with one existing and one new row returns both pks; a post with `DELETE` on a row returns `null` for it and the row is gone), an invalid post answers 422 with errors keyed by html field name and an `HX-Trigger` toast, and the same posts without the header keep today's redirect or re-render. A member who cannot edit the guild still gets 403. The orientation settings autosave still regenerates slots; the studio hours autosave still pushes to Google (patch and assert).
- `tests/hub/base_scripts_spec.py` gains the new head script.
- `tests/hub/guild_edit_spec.py` and `tests/hub/guild_pages_spec.py`: no Save button in any section listed above, the pill present, every form carries `data-autosave`.
- `tests/e2e/guild_settings_autosave_spec.py`, marked `e2e`, on Postgres: type into About and see the pill go Saving then Saved and the value survive a reload; enter a bad URL in Links and see the inline error with the typed value kept and nothing saved; add a FAQ question, fill it, see it saved with an id, edit it again and reload to one row, not two; delete a link row and reload to find it gone; flip the member suggestions toggle and reload to find it on; type into About and immediately click a sidebar link, and land on the new page with the About saved.
- `tests/e2e/guild_studio_hours_spec.py` currently clicks "Save Studio Hours": rewrite it to the autosave flow (change the row, wait for the Saved pill, reload).

Run the hub specs that render the page: `tests/hub/guild_edit_spec.py tests/hub/guild_orientation_tab_spec.py tests/hub/guild_emails_spec.py tests/hub/guild_mailing_list_views_spec.py tests/hub/guild_welcome_editor_spec.py tests/hub/orientation_settings_spec.py tests/hub/orientation_open_windows_editor_spec.py tests/hub/guild_announcement_settings_spec.py tests/hub/guild_visibility_spec.py tests/hub/guild_studio_hours_spec.py`.

## Out of scope

Create actions keep their buttons: Add a one off, mailing list CSV import, add staff, set lead, events, meeting notes, Preview and Send test, the gallery uploader, the banner Delete. Other edit pages (classes, equipment, member) are not changed. No undo history. The personal hours modal keeps its own Save.
