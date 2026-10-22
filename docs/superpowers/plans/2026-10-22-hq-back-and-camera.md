# HQ back buttons and a camera for the wine wall

Source: Lee's email "Holiday Bizzare HQ & CMS", 2026-10-22. Felix asked for it in the 2026-10-22 round.

## Problem

1. Some HQ doors have no way back to the HQ. Volunteers, Wine Wall, Vendor Tables, Market Check-in and the Ledger each carry a "← HQ" link at the top. Marketing (`templates/hq/marketing.html`), Sponsorship (`templates/hq/sponsors.html`) and Flyer (`templates/hq/flyer.html`) do not. The RSVP card ("Going to the Bizarre?") opens the public event page (`/events/<pk>/`, `templates/hub/event_detail.html`), which has no way back either. That is the door Lee reported.
2. On "Log a bottle" (`templates/hq/wine.html`), the photo field is `components/image_field.html`, a hidden `<input type=file accept="image/*">`. It opens a file chooser. Lee wants to take the picture with the phone's camera from inside the form.

## Acceptance criteria

1. Marketing, Sponsorship and the Flyer page each show "← HQ" at the top, linking to `hq:page`. Use the same placement and style as the doors that already have one, and follow each page's own CSS prefix.
2. The RSVP card's button carries a marker the event page can read. The suggested approach is `?from=hq` on `HubCard.url` for the `rsvp` kind. When the event page sees that marker, it shows "← Holiday Bizarre HQ" (the hub's title) at the top, linking to the hub page. It shows only when the event is linked to a hub the viewer may open (the open hub, or any hub for an admin). Without the marker, the page is unchanged. The marker must not leak into the canonical URL, the QR code, the share link or the Eventbrite listing.
3. The Log a bottle photo field offers two choices: "Take a photo" and "Choose a photo". "Take a photo" uses an input with `capture="environment"`. On a phone it opens the rear camera; on a desktop, browsers ignore `capture` and it acts as a chooser. `templates/hub/partials/_wiki_photo_form.html` and `templates/kiln/ticket_form.html` already do this. Both choices fill the same form field and show the same preview, and the 10 MB, HEIC and resize rules stay exactly as they are. The cleanest route is an optional `capture` mode on `components/image_field.html` that other pages could adopt later. Only the wine form switches it on in this PR.
4. Tests: a spec for each back link (present, and pointing at the hub). An event page spec covering marker present, marker absent, and marker present with no linked hub. A wine form spec showing both inputs render and a photo posted through either one saves. An e2e or Playwright check that both buttons work and the preview shows.

## Out of scope

- The Android app's manifest `<queries>` entry for IMAGE_CAPTURE (a separate app release; noted in the kiln memory).
- Any other image field switching to camera mode.
