# Home: a featured class and a featured event in Upcoming at Past Lives

Issue: #888

Source: Felix, 2026-10-23. "On the Homepage, I'd like for there to always be a featured Class and a Featured Event that has a flyer. This would be in the Upcoming at Past Lives, and above the list of events. I basically want this section redesigned so these feature classes/events are always rotating and different on a refresh. It will only show classes and events that are upcoming. It should show images if they have them." Felix chose on 2026-10-23: the event card shows the event's photo, and when it has none, a small live preview of its flyer (#887).

### User story

As a member opening Home, I want one class and one event put in front of me with a picture, different each time I look, so I find things to sign up for without hunting through the calendar.

### Summary

The Upcoming at Past Lives card on Home opens with two featured cards, a class and an event, picked at random on every load from what is upcoming, each with its picture.

### Goal

Members discover classes and events from Home. Each refresh can surface something new.

### Current behavior

`templates/hub/home.html` "Upcoming at Past Lives" is one `hub-card` holding a text list (`pl-upcoming`) built by `hub/home.py::_upcoming_items` (the member's orientation bookings, the next date of each event they can see, and class sessions from `CalendarEvent` with source CLASSES). No pictures and nothing featured.

### Expected behavior

Inside the same card, above the existing list, a Featured row: a Featured Class card and a Featured Event card, side by side on wide screens and stacked on a phone. Each load of Home picks again at random, so a refresh usually shows a different pair. The list below is unchanged.

### Acceptance criteria

1. **Featured Class.** Picked at random on each request from classes a member could book today: the same set the public class list (`classes:public_list`) shows as upcoming and bookable, with at least one session that has not started. Only when the Classes feature switch (`features.catalog`) is on. The card shows the class picture when it has one (`listing_image`, or whatever the public class list cards use; reuse their image choice, never a second rule), the class title, the next session's date and time in the house date format, the instructor name(s), and links to the class page. A class with no picture still gets a card, laid out text only with a quiet accent block where the image would be (no broken image, no grey box with an icon). Eyebrow label: "Featured Class".
2. **Featured Event.** Picked at random on each request from published, upcoming `CommunityEvent`s the member can see (`published().upcoming().for_member(member)`), type `COMMUNITY` or `GUILD_MEETING` (no studio hours, no Guild Lead Meeting). The picture is the event's photo (its cropped hero, the one the event page uses) when it has one; otherwise a small scaled live preview of its flyer (`hub_event_flyer`), built the same way the HQ Flyer card previews it (#887, CSS container units, no iframe; reuse that partial if it can be reused). The card shows title, next date and time (the next occurrence for a repeating event), place, and two links: the event page and "Flyer". Eyebrow label: "Featured Event".
3. **Empty states.** No class to feature: the class card is left out and the event card takes the row (and the reverse). Neither: no Featured row, and the card is exactly today's.
4. **Randomness is per request on the server** (Home is already `no-store`, so no cache pins one pick). A pick from the database, not loading every row into Python. Home's query count grows by a small fixed number regardless of how many classes or events exist; pin it with an `assertNumQueries` (or the repo's query budget helper) spec.
5. **Images:** `loading="lazy"`, `width`/`height` or `aspect-ratio` set so the layout does not jump, `alt` from the title, object fit cover with the same crop the source page uses. Light and dark mode both read well.
6. **Layout:** follows `FRONTEND.md` (tokens, card styles, no new bare tab rows, the house button classes). New CSS goes in the stylesheet Home already loads or a new one added to `FRONTEND.md`'s stylesheet table. 375px phone: stacked, full width, no horizontal scroll. Desktop: two equal columns, the pictures the same height.
7. **Tests:** random pick covered without flakiness (seed or patch the chooser so the spec names which row wins); only upcoming items (a past class and a past event never appear); a class with no future session never appears; the catalog switch off hides the class card; studio hours and lead meetings never featured; an event the member cannot see never featured; photo vs flyer preview fallback; both empty states; the query budget. An e2e or Playwright screenshot of Home at 1440px and 375px for the PR, light and dark.
8. Copy has no dashes. Docs: `hub/AGENTS.md` Home section and `FRONTEND.md` stylesheet table updated.

### Area

hub (Home), classes, events

### Constraints

- Home's existing blocks, order and the door code are untouched apart from this card.
- No new model or migration.
- No client side randomising or carousel for this card: a refresh is the rotation.

### Out of scope

- Admin pinning or choosing which class or event is featured.
- Featuring on any page other than Home.
- Announcements (a sibling PR in this round).
