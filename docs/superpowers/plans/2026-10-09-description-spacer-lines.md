# Class descriptions without doubled blank lines

Felix, 2026-10-09, on class 675 (/classes/admin/675/preview/): "The class post has extra lines of spacing."

Cause: the editor (Quill) stores an Enter-twice gap as an empty paragraph, `<p><br></p>`. On the page every paragraph already has a bottom margin, so each spacer adds a whole blank line on top of it. 15 of 666 prod classes carry them.

## Acceptance criteria
- A body rendered with `rich_body` drops paragraphs holding only spaces, `&nbsp;` or `<br>`; a `<br>` inside a paragraph with words stays.
- The stored body is unchanged, so the editor still shows what the instructor typed.
- Drupal import keeps stripping the same spacers, from one shared pattern.

## Out of scope
- The Eventbrite rendering of the description. (HTML emails built from a body, class welcome and reminder, guild and announcement emails, drop spacers too, because `render_rich_email_body` builds on `render_rich_body`; their paragraphs carry the same margin.)
