# #574 Cancel several Upcoming Times at once

Closes #574. One PR: the Upcoming Times card in `templates/hub/guild_edit.html`, one bulk cancel view in `hub/views.py`, its URL, CSS, specs and one e2e.

## Today

The Upcoming Times card (`guild_edit.html`, the block after the `{# Upcoming Times ... #}` comment) lists fixed slots and open windows from `_upcoming_times(guild)` (`hub/views.py`), five per page through a client side pager in the card's `x-data`. Each row has a Cancel button opening its own `components/confirm_modal.html`. A fixed slot cancels through `guild_orientation_slot_cancel` → `orientations.cancel_slot(slot)` (booked members are emailed, checkout holds released); an open window through `orientation_block_cancel` → `OrientationAvailabilityBlock.cancel()` (bookings inside it stay). Both redirect to `?tab=orientations` with a message.

## What changes

### The card

The card's `x-data` grows from `{ page, size, total }` to also hold the selection:

- `selected: []` of keys, one per row, `"slot:<pk>"` or `"window:<pk>"`.
- Every row carries `data-time-key="slot:<pk>"` (or `window:`), `data-time-index="{{ forloop.counter0 }}"` and `data-emails="<n>"`: a slot's `active_booking_count`, a window's `0` (its bookings stay, nobody is emailed).
- Each row starts with a selection checkbox `.pl-slot-admin__pick` (`type="checkbox"`, `aria-label="Select this time"`, `:checked="selected.includes(key)"`, `@change="toggle(key)"`). It is a selection control, not a form field, so it is not a toggle switch. The per row Cancel button and its confirm stay exactly as they are.
- Methods on the same object: `toggle(key)`, `selectPage()` (every `[data-time-key]` whose index is within the current page), `selectAll()` (every `[data-time-key]`), `clear()`, `emails()` (sum of `data-emails` over the selected keys), `bulkMessage()`.

**The selection bar** (`.pl-slot-admin__bar`), rendered between the card's lead line and the rows, `x-show="selected.length > 0"` `x-cloak`: "<N> selected" (`x-text`), then buttons `Select all on this page`, `Select all` (every page), `Clear` (all `hub-btn hub-btn--sm hub-btn--ghost`), and `Cancel selected` (`pl-btn pl-btn--danger pl-btn--sm`) which dispatches `open-confirm` for `times-bulk-cancel`. The bar wraps at phone width (flex, `flex-wrap: wrap`, gap `0.5rem`); tokens only.

**One confirm** for the bulk action, included once in the card: `components/confirm_modal.html` with `confirm_id="times-bulk-cancel"`, `confirm_title="Cancel the selected times?"`, `confirm_body_include="hub/partials/_times_bulk_cancel_body.html"`, `confirm_button_text="Cancel selected"` and `confirm_js` that submits the hidden bulk form: `document.getElementById('times-bulk-form').requestSubmit();` (the `confirm_js` mode is the one the Guild Hours delete already uses, because the confirm's own form is teleported outside the card and cannot carry a list). The body partial renders the dynamic sentence with `x-text="bulkMessage()"`: "Cancel <N> time(s)? <M> booked member(s) will be emailed that their time is off. Open windows stop taking new bookings; anything already booked inside them stays." With `M` of 0: "Cancel <N> time(s)? Nobody is booked on them." Grammar handled in `bulkMessage()` (time / times, member / members). The include is rendered inside the card's Alpine scope (the modal's `x-teleport` keeps it), so `selected` is in reach.

**The hidden bulk form** `#times-bulk-form`: `method="post"`, `action="{% url 'hub_guild_orientation_times_bulk_cancel' guild.pk %}"`, `{% csrf_token %}`, and `<template x-for="key in selected"><input type="hidden" name="selected" :value="key"></template>`. It sits inside the card's `x-data` so the inputs follow the selection. A boosted submit is fine (it redirects back to the tab).

### The view

`guild_orientation_times_bulk_cancel(request, pk)` in `hub/views.py`, `@login_required` `@require_POST`, URL `guilds/<int:pk>/orientation/times/bulk-cancel/` named `hub_guild_orientation_times_bulk_cancel` in `hub/urls.py` next to the slot cancel route. Gate: `_require_can_manage_orientations(request, guild)` (the same gate as both single cancels).

Parse `request.POST.getlist("selected")`: a value is `slot:<digits>` or `window:<digits>`; anything else is ignored. Then:

- `slots = guild.orientation_slots.upcoming().filter(pk__in=slot_pks, is_cancelled=False)` with `with_active_booking_count()` (or count `bookings.active()` per slot before cancelling; the list is short).
- `windows = guild.orientation_blocks.upcoming().filter(pk__in=window_pks, is_cancelled=False)`.
- For each slot: `emailed += active count`, then `orientations.cancel_slot(slot)`. For each window: `window.cancel()`.
- A foreign pk (another guild's), an unknown pk, a past time or an already cancelled one simply is not in either queryset: skipped, never an error page.
- Message: `Cancelled <N> time(s). <M> booked member(s) were emailed.` (omit the second sentence at 0 emailed). When nothing matched: `messages.info(request, "Nothing to cancel. Those times were already cancelled or are not this guild's.")`.
- Redirect to `?tab=orientations`, as the single cancels do.

No transaction wrapper around the loop (the single cancel has none either; `cancel_slot` talks to Stripe for held checkouts and must not run inside an outer atomic block). Reuse the existing services only; no new email, no new Discord post, no migration.

### CSS (`static/css/hub.css`, next to `.pl-slot-admin__*`)

`.pl-slot-admin__pick` (a 1.1rem checkbox with `accent-color: var(--color-primary)` or the hub's checkbox token), `.pl-slot-admin__bar` (flex, wrap, `gap: 0.5rem`, `align-items: center`, `margin-bottom: 0.75rem`, a muted top border or the elevated background token), `.pl-slot-admin__bar-count` (font weight 600). Both themes, phone width.

## Specs

`tests/hub/orientation_times_bulk_cancel_spec.py`:

1. A lead posts two slots and one window: both slots are cancelled through `cancel_slot` (patch it and assert the calls, or assert the rows and the cancel emails), the window through `cancel()`, and the message names 3 times and the booked member count.
2. A foreign slot pk, an already cancelled slot and a nonsense value in the same POST are skipped, the valid ones still cancel, no error.
3. Nothing valid: the info message, no cancel runs.
4. A member who cannot manage orientations gets 403; GET is 405.
5. The card renders a checkbox per row with the right `data-time-key` and `data-emails`, the bar markup and the hidden bulk form.

`tests/e2e/orientation_bulk_cancel_spec.py`, marked `e2e`: a lead with three upcoming slots ticks two, the bar reads "2 selected", Cancel selected opens the confirm reading "Cancel 2 times?", confirming lands back on the Orientations tab with the success message and only one slot left. Follow `tests/e2e/orientation_booking_spec.py` for the login and seeding helpers. Judge it on Postgres: `DATABASE_URL="postgres://plfog:plfog@localhost:5433/plfog" .venv/bin/pytest tests/e2e/orientation_bulk_cancel_spec.py -m e2e --no-cov -o addopts="" -q`.

`tests/hub/orientation_open_windows_editor_spec.py` and `tests/hub/orientation_settings_spec.py` still pass (they render this card).

## Out of scope

Bulk cancel on the equipment manage tab and the orientations dashboard; bulk edit or reschedule; a reason field.
