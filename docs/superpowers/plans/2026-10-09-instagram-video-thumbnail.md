# Instagram video links show the post's picture

Felix, 2026-10-09: "For the Instagram link on a class post like this, could we show a thumbnail image instead of the link?" (class 675, /classes/admin/675/preview/).

## Today
- `ClassOffering.video_url` accepts YouTube, Instagram and Facebook links (`classes/video_providers.py`). YouTube embeds; Instagram and Facebook render `templates/components/video_embed.html`'s text card ("Watch this video on Instagram"). No third party script runs on a member page, by design.
- Prod (2026-10-09): 11 classes hold an Instagram `/p/<id>/` link in `video_url` (675, 669, 643, 638, 610, 603, 602, 601, 600, 599, 598), 6 distinct posts.
- A plain server request for an Instagram post page returns an `og:image` meta tag pointing at the post's picture on `*.cdninstagram.com` (checked from Polaris; verified with a `facebookexternalhit/1.1` user agent and a browser one). Those image URLs are signed and expire, so the picture must be copied into our own storage, not hotlinked. Whether Instagram answers the same from Render's servers is unproven until it runs there.

## Acceptance criteria
1. A class whose `video_url` is an Instagram link gets the post's picture stored on the class (`video_thumbnail` image field, nullable; plus the URL it was taken from, so a changed link is refetched and a removed or non Instagram link clears it).
2. Fetching never happens inside a page request. A scheduled job (registered like the other `run_scheduled_tasks` jobs; update `core/spec/scheduled_jobs_spec.py`'s parity tuples) fetches for classes whose Instagram link has no picture yet or changed since; a failed fetch is retried at most once a day per class (a checked at timestamp), and failures are logged on the job run, never raised. A management command runs the same work on demand for a backfill.
3. The fetch is SSRF safe: only the recognised Instagram post URL is requested (rebuilt from the provider match, not the raw string), the `og:image` must be https on a host ending `.cdninstagram.com` or `.fbcdn.net`, the image must answer `image/*` and stay under 5 MB, short timeouts, and the stored copy goes through the repo's normal image normalization.
4. The class page's Watch section shows the picture filling the card, with a play mark and "Watch on Instagram", linking to the post in a new tab exactly as the card does today. No picture yet: today's text card, unchanged. The preview page shares the template.
5. YouTube embeds and Facebook cards are unchanged; guild and help pages that include `video_embed.html` are unchanged.
6. Cloning a class copies the picture reference too (or leaves it for the job to refetch; either is fine, state which).

## Out of scope
- Facebook thumbnails.
- Embedding Instagram's player or script.
- Links pasted inside the description text.
