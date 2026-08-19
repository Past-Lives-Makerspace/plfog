# Writing Help Center Guides

The standard for every article in the Help Center. Follow this and the drift
guards stay green, the screenshots regenerate themselves, and the guides keep
one voice. The Help Center overhaul plan
(`docs/superpowers/plans/2026-08-10-help-center-knowledge-base.md`) is the
origin story; this is the working reference.

## Where guides live

`membership/help_content.py` is the single source of truth — a Python module of
dicts, not a CMS. The database is only a projection: `manage.py seed_help_center`
runs on every deploy (render.yaml buildCommand) and refreshes seeded articles in
place. **To fix copy, edit the Python and merge.** Edits made in `/help/edit/`
to a seed-owned article are overwritten on the next deploy.

One article entry:

```python
{
    "slug": "guild-lead-quickstart",      # kebab-case, globally unique, stable forever
    "category": "running-a-guild",        # a CATEGORIES slug; None = unlisted-capable
    "title": "Guild Lead Quickstart: Everything You Can Do",
    "sort_order": 10,                      # within the category; step by 10
    "related": ["your-guild-page"],       # slugs → the Related links block
    "body": """...markdown...""",
    "screenshots": [ShotSpec, ...],        # may be []
}
```

## Voice and casing

- **ELI14.** Short sentences, plain words, no filler. Every claim code-verified.
  Keep every permission caveat ("only leads, staff, and admins see…").
- **Title Case** for the article `title`, category names, and every `##`/`###`
  heading — small words (a, an, the, of, to, in, for, and, or, on, by) stay
  lowercase unless first. Matches FRONTEND.md Rule 22.
- Say **"Member Portal"**, never "FOG", in anything a member reads.
- Audiences of guild lead + staff say **"lead or staff"** — never "Guild
  Administrator" (that word is reserved for the site-wide Admin Capabilities).
- GATED surfaces (Tab/payments, Buyables, slash commands) are **never
  mentioned** — not in prose, not in screenshots.

## Body markdown

Rendered by the `help` profile of `membership/markdown.py`. What works:

- `## Heading {#anchor-id}` — anchors make headings linkable and pair with
  `core/help_registry.py` keys (registry key `teach.create-class` ↔ anchor
  `{#teach-create-class}`). Anchor pattern: `^[a-z0-9-]{1,80}$`. Never rename an
  anchor without checking the registry.
- Callouts: `!!! note` / `!!! info` / `!!! tip` / `!!! warning` with an indented
  body. Anything else (e.g. `!!! danger`) is stripped.
- Images: `![Caption = alt text.](/static/help/<article-slug>/NN-name.png)` —
  **only `/static/help/…` sources survive**; external/media images are removed.
- Internal links (`/help/…`, `/guilds/…`) stay same-tab; external links open a
  new tab, hardened. Uncategorized articles live under `/help/more/<slug>/`.
- Tables, lists, bold, `code`, blockquotes: standard markdown.

## Video Walkthroughs (Loom / YouTube)

The help profile allows `<iframe>` embeds from exactly two hosts:
`https://www.loom.com/embed/…` and `https://www.youtube-nocookie.com/embed/…`.
Anything else is stripped entirely. Allowed attributes: `src`, `title`,
`allowfullscreen`, `loading`. CSS renders them responsive 16:9.

Guides carry an invisible placeholder where a video belongs:

```
<!-- Video slot: paste a Loom embed here — see docs/HELP_AUTHORING.md, Video Walkthroughs. -->
```

HTML comments are stripped at render, so members never see the slot. When a
Loom exists, replace the comment with:

```html
<iframe src="https://www.loom.com/embed/VIDEO_ID" title="What the video shows" allowfullscreen></iframe>
```

For Loom, use the **embed** URL (share URL's ID after `/share/` →
`/embed/<id>`). For YouTube, use the privacy host:
`https://www.youtube-nocookie.com/embed/<id>`.

## Screenshots

- Committed at `static/help/<article-slug>/`, folder name = article slug,
  filenames `NN-step-slug.png` (two digits, kebab-case).
- Every body image needs a matching `ShotSpec` in the article's `screenshots`
  list, and vice versa — `tests/membership/help_content_spec.py` is the drift
  guard (no orphan PNGs, no dangling refs).
- Regenerate with `scripts/capture-help-screenshots.sh` — it seeds one persona
  per `as_role` (member / guild_lead / instructor / admin), walks every
  ShotSpec, crops to `selector` (16px pad) or frames 1200×800, writes the PNGs,
  and builds a review contact sheet. Commit the changed PNGs.
- ShotSpec `page` is a URL name (`hub_guild_voting`) or a literal path
  (`/guilds/1/edit/`). The guild-lead specs assume Ceramics Guild is pk 1 in the
  capture DB; the harness asserts it. Avoid per-object paths whose pks the
  harness can't guarantee.

## Tours, unlisted articles, examples

- Guided tours live in `core/tours.py` (single-page, `[data-help-key]` targets,
  Title Case step titles). New tour targets need existing help keys; the
  template-walk drift test guards them.
- `UNLISTED_SLUGS` articles resolve at their URL but never appear on the
  landing, category pages, or search.
- The living examples the guides link to: the **Cartographers Guild**
  (`/guilds/cartographers-guild/`, seeded by `manage.py seed_example_guild`,
  content in `membership/example_guild.py`) and the example class
  **Shaker Side Table: Hand-Cut Joinery**
  (`/classes/shaker-side-table-hand-cut-joinery/`, past-dated, published,
  non-private — reachable by link, absent from the catalog). Keep both alive;
  the seed module's docstring records the safety contract for the fictional
  members.

## Checklist for a new article

1. Write the entry in `membership/help_content.py` (Title Case, anchors,
   caveats, a Video slot comment if a Loom might come).
2. Add its slug to the approved-categories dict in
   `tests/membership/help_content_spec.py`.
3. Add ShotSpecs; run `scripts/capture-help-screenshots.sh`; commit PNGs.
4. `pytest tests/membership/help_content_spec.py` (drift guard) and
   `manage.py seed_help_center --dry-run` locally.
5. If the article should back UI info-views or a tour, wire
   `core/help_registry.py` keys to its anchors.
