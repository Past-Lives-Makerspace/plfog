# #386 — A composer POST to a class published while the page was open

**Issue:** https://github.com/Past-Lives-Makerspace/plfog/issues/386
**Family:** the same race PR #385 fixed for cancelled and archived, landing somewhere else.

## The bug

An instructor has the full composer open on a **draft**. An admin publishes the class from the
admin side. The instructor presses Save or Submit.

`_instructor_composer` (`classes/views.py:2086`) re-reads the status on the way in. The class is
now `PUBLISHED`, so the POST falls through to `_teach_published_class_edit`
(`classes/views.py:2165`) — the *light* form for a live class. That form binds
`TeachPublishedClassForm`, which knows only the few fields a live class may still change. It saves
those, silently discards the title, price and dates the instructor typed, and reports
**"Class updated."**

Unsaved work under a success message. The instructor has no way to know anything was dropped.

> The issue says "around 1686". The function is at **2165** on current main; the file moved under
> it. Verify positions before trusting any line number here either.

## The sibling that already works

The cancelled and archived case was fixed and is the shape to mirror:

```python
if offering.status in {ClassOffering.Status.CANCELLED, ClassOffering.Status.ARCHIVED}:
    if request.method == "POST":
        return _render_closed_class_post(request, offering, teaching_member)
```

`_render_closed_class_post` (`classes/views.py:2144`) rebinds the **composer** forms from
`request.POST`, saves nothing, and re-renders the composer through `_render_teach_class_form` with
a `notice=`. Every typed value comes back in its field so the work can be copied out.
`_CLOSED_WHILE_EDITING` (`classes/views.py:2138`) is the notice text.

## The trap: one branch, two kinds of POST

`_teach_published_class_edit` is **not** only the loser of this race. It is the legitimate handler
for the published light-edit form, which a guild staffer or instructor POSTs to on purpose every
time they edit a live class. A fix that re-renders the composer for *every* POST on a published
class breaks light editing outright, and every spec for it.

So the branch has to tell the two apart. It can:

| POST | carries |
|---|---|
| composer (`_components/class_composer.html:186-188`) | hidden `action` (`save`/`submit`) **and** hidden `step` |
| published light form (`teach/class_form_published.html:44-68`) | neither; no hidden inputs at all |

`step` is the composer's Alpine-bound phase and exists nowhere else. **Treat a POST carrying
`step` as a composer POST.** If the builder finds a better marker while reading, say so before
using it — do not add a new hidden field to a template if an existing one already separates them.

## What done looks like

1. A composer POST arriving for a class whose status has become `PUBLISHED` re-renders the
   composer with the typed data intact and an explanation that the class was published while the
   page was open. **Nothing is saved.**
2. A published light-edit POST still saves and still redirects with "Class updated." Unchanged.
3. The notice names what happened and what to do, in the register of `_CLOSED_WHILE_EDITING`.
   A published class is still editable by its instructor, so the wording must not imply the work
   is unreachable: the light form is where those fields now live.
4. A spec mirroring `describe_a_composer_post_to_a_class_cancelled_since_the_page_was_rendered`
   (`classes/spec/views/class_composer_spec.py:1466`), covering both rows of the table above.

## Out of scope

- The reverse race (a light-form POST landing on a class cancelled since render, which currently
  binds `TeachClassOfferingForm` from a light-form POST). Real, pre-existing, not this issue.
- Any change to what a published class may edit.
- Auto-saving or recovering the dropped fields into the light form. The fix is to stop lying
  about having saved them, not to save them.
