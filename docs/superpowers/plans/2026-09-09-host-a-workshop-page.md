# Host a Workshop — the Teach page, retoned and admin editable

**Date:** 2026-09-09
**Branch:** `fog/class-cms-round`
**Builds on:** commit `d4ae6dce` (the Teach at Past Lives page and the apply flow) and `968627bb`
(the admin side reframed as interest). Read §A of
`2026-09-09-class-cms-composer-and-instructor-marketing.md` for the page's layout; this spec changes
its words and where the words live, not its shape.

## 1. The two asks

1. **Tone.** The page reads today like "become a teacher at Past Lives": apply, application,
   approval, instructor. The user wants it to read like an invitation to **host a workshop or a
   class**: a way to get people into workshops and to share what they love. Low key, warm, a shop
   inviting its own members to run something. Not a job posting.
2. **Editable.** Admins must be able to change the page's marketing copy themselves, easily,
   without a deploy.

Both land together so the new copy is written once, straight into the fields admins will edit.

## 2. Where the copy lives

**On `ClassSettings`** (`classes/models.py`, the classes app's settings singleton), edited on the
classes admin Settings page (`/classes/admin/settings/`, `ClassSettingsForm` at
`classes/forms.py:1135`, view at `classes/views.py:4310`). That singleton already holds admin
authored prose (`liability_waiver_text`, `confirmation_email_footer`) and the page's example class
pointer (`example_class`, added in `d4ae6dce`), so the whole Host a Workshop page is configured in one
place. Not `SiteConfiguration`: the page is a classes surface and its one existing setting is
already here.

Seven fields, one migration, **every field carrying its default copy in the migration default** so
no data migration is needed and no site ever renders a blank section:

| Field | Type | Default (see §4) |
|---|---|---|
| `teach_page_title` | `CharField(max_length=120)` | the hero headline |
| `teach_page_lead` | `TextField` | the hero lead paragraph |
| `teach_page_features` | `TextField` | one card per line, `Title: description` |
| `teach_page_how_it_works` | `TextField` (Markdown) | an ordered list of three steps |
| `teach_page_expectations` | `TextField` (Markdown) | a bullet list |
| `teach_page_faq` | `TextField` (Markdown) | `### Question` then a paragraph, repeated |
| `teach_page_cta_title` | `CharField(max_length=120)` | the footer card headline |
| `teach_page_cta_line` | `TextField` | the footer card line |

Every field has `help_text` telling an admin exactly what it feeds and, for Markdown fields, that
Markdown works. `blank=True` on all of them: a deliberately blanked section is hidden on the page,
not rendered as an empty card.

### Rendering (fat model)

- Markdown fields render through the existing `membership.markdown.render_markdown(source,
  profile="member")` — the sanitizer already strips scripts, inline styles and unknown tags, so the
  output is safe to mark safe. Expose them as model properties (`teach_page_how_it_works_html`,
  `teach_page_expectations_html`, `teach_page_faq_html`) returning `SafeString` via `mark_safe`, and
  a spec proves a `<script>` in the field comes out stripped. No `|safe` on raw text anywhere in the
  template.
- Feature cards parse in the model: `ClassSettings.teach_page_feature_cards() -> list[FeatureCard]`
  where `FeatureCard` is a small frozen dataclass `(title, description, icon)`. Rules: split on
  lines, drop blank lines, split each line on the **first** `:`; a line with no `:` is a title-only
  card. Icons are decoration and are assigned **by position** from a fixed tuple of eight icon keys
  (the seven feather icons already in `why_teach.html` plus `mail`), wrapping past eight. An admin
  who reorders cards changes which icon sits where; that is accepted and stated in the help text.
- The `<details>` collapsibles in Common Questions go away: Markdown renders headings and paragraphs
  and that is what an admin can edit. The section is open, readable, and styled as
  `.pl-md .pl-teach-faq-md` with `h3` as the question. A collapsible FAQ is not worth a custom
  line format nobody will remember.
- How It Works keeps its gold numbered circles: style `.pl-teach-steps-md ol` with CSS counters
  (`counter-reset` / `::before`) so a plain Markdown `1. 2. 3.` list renders exactly like today's
  hand built `.pl-teach-steps`. The old markup and its classes are removed.

### The admin surface

A **Host a Workshop Page** section on the classes admin Settings page, above the existing sections'
Save button (Rule 21: Save is last and just says Save). Every field through
`components/form_field.html`, textareas sized for prose (`rows` 4 to 12 by field), a `.pl-help`
bubble on the section heading reading `This is the page a member sees under Host a Workshop in the
sidebar until an admin says yes to them.`, and a `View the Page` ghost link beside the heading that
opens `classes:teach_why` in a new tab. The Markdown fields get a one line hint under the label:
`Markdown works here: **bold**, lists, links.` (`example_class` moves into this section too; it is
the same page's setting.)

### Not editable (code owned, retoned in §4)

Button labels, the interest modal, the three state banners, the sidebar label, the Django messages,
the guide link line, and the empty states. These are structural and their wording carries logic
(state names, actions); they change in this PR to the new tone but stay in the template.

## 3. Naming

URLs, view names and model names **do not change** (`teach_why`, `teach_apply`,
`apply_to_teach`, `teaching_application_*`). Only words a member or admin reads change. Renaming
the internals would touch builder 2's files for no user visible gain.

The sidebar entry for a member who cannot teach becomes **Host a Workshop** (destination unchanged:
`teach_overview`). Instructors keep **Teaching**. `_teach_nav()` in `hub/context_processors.py:60`
and `tests/hub/teach_sidebar_spec.py`.

## 4. The copy

No em dashes or standard dashes in any of it. Title Case on headings (Rule 22). These are the
migration defaults and the template strings; the builder writes them verbatim.

### Hero (fields)

`teach_page_title`: `Share What You Love`

`teach_page_lead`: `Run a workshop or a class for the people already in the shop. Show a technique, teach a skill, or just get folks making things together. You get a page for it, a sign up list, and the tools to run the day.`

### Hero buttons (template)

- none / declined: **`I'm Interested`** (declined: `I'm Still Interested`)
- pending: badge `Note Sent` + `See an Example Class Page`
- approved: `Go to the Teaching Portal` + `See an Example Class Page`

### What You Get (field `teach_page_features`, six cards so the grid is two even rows)

```
A Page Worth Sharing: Your workshop gets its own page with a wide banner photo, a gallery, the schedule, your bio, and a sign up panel that follows the reader down the page.
Your Words, Your Photos: Write it the way you would say it. Add a banner and as many gallery shots as you like, and choose which part of each photo shows.
Sign Ups That Run Themselves: When it fills up, people join a waitlist. The moment a seat opens, the next person is offered it and held for three days.
Everyone On One Screen: See who is coming, mark someone as paid, move a person to another date, and email the whole group without leaving the page.
Free, Paid, Or On Sale: Run it free, set a price with a member discount, or put it on sale and the new price shows up everywhere on its own.
Run It Again In One Click: Went well? Make a copy with new dates and keep everything else exactly as it was.
```

Section heading stays `What You Get` (template).

### See It For Yourself (template, unchanged in structure)

`This is a real workshop page, built with the same editor you would use. Open it and scroll the whole thing.` / `The card on the right is exactly how it looks in the catalog. Everything on it comes from what the host typed in.` / button `Open the Example Page` / caption `This is a live catalog card, not a picture of one.`

### How It Works (field `teach_page_how_it_works`)

```
1. **Say you're interested.** Tell us what you'd like to host. A sentence is plenty. An admin reads every note.
2. **Build your page.** Once you're in, the editor walks you through it in five short steps. Save a draft any time and come back.
3. **Open sign ups.** Send it for a quick look. Your guild lead and an admin check it over, then it goes into the catalog and out to members.
```

### What We Ask Of You (field `teach_page_expectations`)

```
- Know your material and know the tools you're using.
- Show up on time and leave the space the way you found it.
- Answer people when they message you through the app.
- Tell an admin as early as you can if you need to move or cancel a date.
```

### Common Questions (field `teach_page_faq`)

```
### Do I Need to Be an Expert?
No. You need to be safe and clear. Plenty of great workshops are run by people two steps ahead of everyone else in the room.

### How Long Until I Hear Back?
An admin usually gets to it within a week. You can check this page any time to see where things stand.

### Can I Charge for It?
Yes. You set the price and an optional member discount when you build the page. You can also run it free.

### What If Nobody Signs Up?
You can cancel from your dashboard and everyone who signed up is told automatically. Nothing is stuck.
```

### Guide (template)

`Read the Hosting Guide` (the linked article is unchanged).

### Footer card (fields + template)

`teach_page_cta_title`: `Got Something to Share?`
`teach_page_cta_line`: `Tell us what you have in mind and an admin will take it from there.`
- none: button `I'm Interested`; declined: `I'm Still Interested`
- pending (template): heading `Waiting to Hear Back`, line `Nothing else to do right now. Have a look around the example page while you wait.`, footnote `Want to add something to your note? Ask an admin and they can update it for you.`
- approved (template): heading `You Are Already In`, line `The teaching portal is open. Start a workshop whenever you are ready.`

### State banners (template)

- pending: **`Thanks, We Got Your Note`** / `An admin will get back to you. You will get an email either way, and you can check this page any time.` / meta `Sent on {date}.`
- declined: **`Not Right Now`** / `An admin had a look and it is not the right time yet. Here is what they said.` / note label `Note from the admin` / meta `You are welcome to say you're interested again whenever you like.`
- approved: **`You Can Host Workshops`** / meta `Since {date}.` / buttons `Go to the Teaching Portal`, `Create a Workshop`

### The interest modal (template + `apply_form.html`)

Modal title `What Would You Host?`; field label `What Would You Like to Host?`; hint `A sentence or two is plenty. Tell us the subject, roughly how long it would run, and anything you have taught before.`; submit `Send It`; cancel `Cancel`.

### Django messages (views)

- after a successful note: `Thanks. An admin will get back to you.`
- guards from `apply_to_teach` keep their wording except `You have already applied.` → `We already have your note.`

### Member emails (`core/events/copy.py`, the two member facing events only)

- approved: subject `You can host workshops at Past Lives`, body `Good news. An admin said yes. The teaching portal is open and you can start building your first workshop page.` CTA `Build Your First Page`.
- declined: subject `About hosting a workshop`, body `An admin had a look at your note and it is not the right time yet.` then the note, then `You are welcome to say you're interested again whenever you like.` CTA `Read the Hosting Guide`.
- The admin notification is already reframed (`968627bb`); do not touch it.

### Help Center

`membership/help_content.py` and `core/help_registry.py` were updated in `d4ae6dce` to describe applying to teach. Sweep them for the new tone: `Apply to Teach` → `I'm Interested`, `application` → `note`, `Teach at Past Lives` → `Host a Workshop`. Keep every fact and every permission caveat.

## 5. Tests

BDD, `describe_*` / `it_*`, factory-boy. Cover:
- Every field's default renders on a fresh `ClassSettings` (the six cards, three steps, four bullets, four questions).
- `teach_page_feature_cards()`: six from the default; a line without a colon; blank lines skipped; the icon wraps past eight cards; an empty field yields an empty list and the section is hidden.
- The three Markdown properties: a `<script>` and an inline `style=` in the source are stripped; a link is hardened per the member profile.
- A blanked section (any of the seven) hides its card; the page still renders every other section.
- The admin Settings page shows the new section, saving round trips every field, and a non admin cannot reach it (the existing gate).
- Sidebar label `Host a Workshop` for a locked active member and `Teaching` for an instructor.
- `tests/e2e/instructor_orientation_spec.py` constants (`HERO`, `APPLIED_BANNER`, `SUCCESS_MESSAGE`, the button names) updated and the spec run on Postgres 5433; grep `tests/e2e/` for every string this spec changes.
- `classes/spec/views/teach_why_spec.py` assertions updated to the new strings.
- `tests/template_comment_lint_spec.py`, `ruff`, `manage.py check`.
