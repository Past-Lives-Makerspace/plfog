# 371 item 1: the Send Email button stops lying

Parent issue: https://github.com/Past-Lives-Makerspace/plfog/issues/371 (item 1)

## The bug

`templates/classes/_components/class_screen_base.html:47` and
`templates/classes/teach/class_registrations.html:14` both render:

    <a href="{% url 'hub_compose' %}?audience=class:{{ offering.pk }}&lock=1">Send Email</a>

The roster page that shows that button is gated by `classes_admin_access_required`
(`classes/views.py:1043`), which authorizes on `view_as.has_actual("admin")` and is
therefore preview independent by design (its docstring says so).

The composer behind the button is gated by `_can_enter_compose` (`hub/views.py:3454`)
which falls through to `_can_compose` (`hub/views.py:3435`) and
`_can_announce_to_class` (`hub/views.py:3427`). Both call `_viewing_as_admin`
(`hub/views.py:789`), which IS preview dependent.

So an admin previewing the site as a member or as a guest is shown the button and is
then refused by the composer. `hub/views.py:3762` responds with a bare `redirect` to
`hub_guild_announcement_propose`. Because `templates/hub/base.html` puts
`hx-boost="true"` on the hub body, that redirect is an AJAX body swap: the page
silently becomes an unrelated "propose an announcement" form. No error, no
explanation. "The button does not work" is a fair description.

Confirmed matrix from the parent issue:

| Viewer | Roster page | Send Email link |
|---|---|---|
| the class's own instructor | 200 | 200, locked banner |
| admin, no preview | 200 | 200, locked banner |
| admin previewing as instructor | 200, button shown | 200, locked banner |
| admin previewing as member | 200, button shown | 302 to /announcements/propose/ |
| admin previewing as guest | 200, button shown | 302 to /announcements/propose/ |

## The decision, and why

The parent issue offered two fixes: (a) widen `_can_announce_to_class` to admit
`view_as.has_actual("admin")`, or (b) hide the button when the viewer cannot use it
and have the composer give a reason.

**We are doing (b), and we are NOT widening any permission.**

`_can_use_admin_tools` (`hub/views.py:3471`) documents the existing intent
explicitly: "For an ACTUAL admin it is view-as-aware: an admin previewing the site as
a plain member does NOT see it". The codebase deliberately strips admin *actions* in
preview mode while leaving admin *pages* navigable. Widening `_can_announce_to_class`
would contradict that on the one surface that happens to have been reported. Hiding
the button instead makes the two agree in the direction the codebase already chose.

The silent redirect is a separate defect and is fixed for every caller, not just this
one: a viewer who cannot enter the composer should be told so.

## Acceptance criteria

1. On both roster templates, the Send Email link renders only when the current
   request would actually be admitted to the composer for that class. An admin
   previewing as member or as guest does not see it.
2. An admin not in preview, an admin previewing as instructor, and the class's own
   instructor all still see the link and still reach the composer with the class
   audience locked.
3. `hub_compose` no longer silently redirects a refused viewer. It returns a visible
   reason. The three POST paths at `hub/views.py:3796`, `:3852` and `:3895` keep
   refusing, and their refusal is also not silent.
4. A spec parametrizes over the view-as roles (none, admin, instructor, member,
   guest) and asserts both the button's presence in the rendered roster and the
   composer's response for each.
5. Both roster templates are covered, not just one.

## Out of scope

- Widening any permission. If an admin wants to email a roster, they exit preview.
- The other `_viewing_as_admin` callers reached from `has_actual` gated pages. The
  parent issue suggests auditing them; that is a separate ticket.
- Anything in issue #370.
- The composer's own audience handling, `split_audience`, or the locked banner.

## Files expected to change

- `hub/views.py` (the compose entry refusal; a template-visible helper for the gate)
- `templates/classes/_components/class_screen_base.html`
- `templates/classes/teach/class_registrations.html`
- a spec under `tests/hub/` or `classes/spec/`

Touching anything outside this list is a question for the orchestrator, not a
decision to make alone.
