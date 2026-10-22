# A printable flyer for every community event, shown on the HQ Flyer card

Source: Lee's email "Holiday Bizzare HQ & CMS", 2026-10-22. Felix: the event flyer and sharing card says Coming soon; it should show the flyer the event system generates. Felix chose this on 2026-10-22: build an event flyer the way guilds, equipment and orientation types have one.

## Problem

`templates/hq/flyer.html` shows a "Coming soon" poster until someone uploads a 2026 flyer (`HubFlyer.flyer`). No flyer exists for a `CommunityEvent`. The event page has only the "Share & Print" QR card (`components/qr_share_card.html`). Guilds (`hub_guild_flyer`, `templates/hub/guild_flyer.html`), equipment and orientation types already have print-optimised one-page flyers with a QR code.

## Acceptance criteria

1. Every published community event gets a printable one-page flyer at `/events/<pk>/flyer/`. It sits next to the public event page and follows the same rules on who may see it: a published event's flyer is public, and an unpublished one only for those who may edit the event. It shows:
   - the event hero image (when there is one)
   - the title
   - the date and time (`when_display`)
   - the location
   - the pitch or short description
   - the RSVP goal line only if the event page shows one
   - a QR code to the event's public URL (`event.qr_svg`), with the short URL printed under it
   - the Past Lives name and address

   Use the guild flyer's print CSS approach: Letter, one page, no app chrome, a Print button hidden in print. It works in light and dark on screen and prints in light.
2. The event page's Share & Print card and the Manage Events row for the event both get a "Flyer" link to it.
3. The HQ Flyer card's page: when no 2026 flyer is uploaded and the hub has an event, the poster area shows the generated flyer instead of Coming soon. Use a scaled preview or an image of it; pick whichever renders reliably on a phone and say which in the PR. Show View full size (opens the flyer page), Print, and the share message and links as today. An uploaded flyer still wins. With no event linked, Coming soon stays. The upload controls are unchanged (the card sharing PR makes them admin only).
4. Tests: the flyer page for a published event (content and QR present), an unpublished one (404 for a stranger, 200 for an editor), a 404 for an unknown pk, the event page and Manage Events links, and all three states of the HQ Flyer page. Add a Playwright screenshot of the flyer for the PR.
5. No dashes in copy. Add the new download or print links to `tests/download_links_lint_spec.py` if they qualify.

## Out of scope

- A PDF render on the server (the browser's Print to PDF is the path, as with the guild flyer).
- Choosing a flyer template or colours per event.
- Changing the uploaded flyer flow.
