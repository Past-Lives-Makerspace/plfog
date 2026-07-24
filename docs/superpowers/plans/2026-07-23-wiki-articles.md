# Build spec: WikiArticle — TOC-and-anchor guides on the Wiki page

Status: ready to build. Decided model (b) from `wiki-content-plan.md`. Branch: `feat/interactive-space-map` (continues the Spaces/Wiki split at `067f546`).

## Goal

Add an ordered, deep-linkable set of "how it works" articles to `/wiki/`, rendered as a table of contents plus anchored `<section>`s, editable admin-only through a new tab in the existing Wiki editor. Seed the eight code-verified guides from `wiki-content-plan.md`. Leave the three real-world blocks (parking, who-to-contact, code of conduct) as Josh's to fill.

This is deliberately a clone of the existing `OrgFAQItem` inline-formset wiring. No new URL, no new nav, no new page — one more section on the page that already exists.

## Non-goals (YAGNI)

- Standalone per-article pages (`/wiki/<slug>/`). Anchors on the one page are enough until an article grows very long.
- Search/filter over articles.
- Draft/preview workflow beyond a simple `is_published` flag.
- Versioning or edit history.

## The model

New model in `membership/models.py`, next to `OrgFAQItem` / `OrgLink`:

```python
class WikiArticle(models.Model):
    """A titled Markdown guide on the Wiki page, deep-linkable by slug anchor.

    Ordered inline children of the OrgInfoPage singleton, exactly like OrgFAQItem —
    the editor reuses that formset wiring. Rendered as a table-of-contents entry plus
    an anchored <section id="{slug}"> so Discord/email/other pages can link to a guide
    by name.
    """

    page = models.ForeignKey(
        OrgInfoPage, on_delete=models.CASCADE, related_name="articles",
        help_text="Parent Wiki page (the singleton).",
    )
    title = models.CharField(max_length=200, help_text="Guide heading, e.g. 'Guild voting'.")
    slug = models.SlugField(
        max_length=220, blank=True,
        help_text="URL anchor for deep links. Auto-filled from the title when left blank.",
    )
    body = models.TextField(help_text="The guide body. Supports Markdown.")
    sort_order = models.PositiveIntegerField(default=0, help_text="Order on the page; lower shows first.")
    is_published = models.BooleanField(
        default=True, help_text="Uncheck to keep a draft off the public page while writing.",
    )

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["page", "slug"], name="uq_wikiarticle_page_slug"),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Slugify the title into an empty slug, de-duplicating within the page.

        Business logic on the model, per house style. Slug stays stable once set (so an
        existing deep link never breaks) because we only fill it when blank.
        """
        from django.utils.text import slugify

        if not self.slug:
            base = slugify(self.title) or "article"
            slug = base
            n = 2
            siblings = WikiArticle.objects.filter(page=self.page).exclude(pk=self.pk)
            while siblings.filter(slug=slug).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)
```

Manager note: a thin `published()` queryset method (`filter(is_published=True)`) keeps the view thin and matches `Floorplan.objects.published()`.

## Migration

One migration, `membership/migrations/0101_wikiarticle.py` (0100 is the latest). `CreateModel` only — no data migration, so no reverse function needed. The seed content is loaded by a management command (below), NOT a data migration, so re-seeding is idempotent and never fights a rollback.

## Form + formset

In `hub/forms.py`, next to `OrgFAQItemForm`:

```python
class WikiArticleForm(forms.ModelForm):
    """A single Wiki article row in the editor — mirrors OrgFAQItemForm."""

    class Meta:
        model = WikiArticle
        fields = ["title", "slug", "body", "sort_order", "is_published"]
        widgets = {"sort_order": forms.HiddenInput(), "body": forms.Textarea(attrs={"rows": 10})}

WikiArticleFormSet = forms.inlineformset_factory(
    OrgInfoPage, WikiArticle, form=WikiArticleForm, extra=0, can_delete=True
)
```

`slug` stays an optional visible field (blank auto-fills on save); expose it so an admin *can* pin a slug but never *must*.

## View wiring

`hub/views.py`:

1. `_org_info_edit_context` — add `"article_formset": WikiArticleFormSet(instance=page, prefix="articles")` to the returned dict (import alongside the other two formsets).
2. New endpoint `wiki_articles_save`, a near-copy of `org_info_faq_save`:
   ```python
   @login_required
   @require_POST
   def wiki_articles_save(request: HttpRequest) -> HttpResponse:
       """Save the Wiki articles from their own form on the Articles tab. Admin only."""
       from hub.forms import WikiArticleFormSet
       forbidden = _require_admin(request)
       if forbidden is not None:
           return forbidden
       page = OrgInfoPage.load()
       formset = WikiArticleFormSet(request.POST, instance=page, prefix="articles")
       if formset.is_valid():
           formset.save()
           messages.success(request, "Wiki guides saved.")
       else:
           messages.error(request, "Couldn't save the guides — check the highlighted fields.")
       return redirect(f"{reverse('hub_wiki_edit')}?tab=articles")
   ```
3. `wiki` (read view) — pass the published articles:
   `"articles": page.articles.filter(is_published=True).order_by("sort_order", "pk")`.
   (Guests never see drafts; the editor sees all rows.)

`hub/urls.py`: `path("wiki/articles/save/", views.wiki_articles_save, name="hub_wiki_articles_save")` in the Wiki block.

## Templates

**`templates/hub/wiki.html`** — between the intro card and the first fixed block, add (only when there are articles):

```django
{% if articles %}
<nav class="pl-wiki-toc" aria-label="On this page">
    <h2 class="hub-detail-label">On this page</h2>
    <ul>
        {% for a in articles %}<li><a href="#{{ a.slug }}">{{ a.title }}</a></li>{% endfor %}
    </ul>
</nav>
{% for a in articles %}
<section class="pl-guild-section pl-wiki-article" id="{{ a.slug }}">
    <h2 class="pl-guild-section__h2">{{ a.title }}</h2>
    <div class="pl-md">{{ a.body|guild_markdown }}</div>
</section>
{% endfor %}
{% endif %}
```

The TOC anchors and `id`s are the whole point of choosing (b) — verify a deep link (`/wiki/#guild-voting`) actually scrolls in the browser.

**`templates/hub/org_info_edit.html`** — add an "Articles" tab button next to "FAQ & Links", and an `x-show="section === 'articles'"` block holding a formset `<form action="{% url 'hub_wiki_articles_save' %}">`. Copy the FAQ formset block structure verbatim (management_form, row loop, `empty_form` template for the JS "add row" button); drop the video/document fields; add title, slug, body, is_published. Reuse whatever JS the FAQ "add row" uses — do not invent a new adder.

## CSS

`static/css/hub.css`: `.pl-wiki-toc` (a bordered card list) and `.pl-wiki-article { scroll-margin-top: 1rem; }` so an anchor jump doesn't hide the heading under the top bar. Theme-aware via existing `--hub-*` tokens. Small; no new color values.

## Seed content

Management command `membership/management/commands/seed_wiki_articles.py`, idempotent (`update_or_create` on `(page, slug)`), `--dry-run` supported, mirroring `seed_floor_geometry.py`. Loads the eight **code-verified** guides from `wiki-content-plan.md` §4.2–4.9:

1. Guilds and guild pages
2. Orientations
3. Guild voting
4. Taking a class
5. Teaching a class
6. The community calendar
7. Connecting Discord — **without** the slash-command list (held until Josh confirms the prod bot; see below)
8. Notifications and your settings

The intro (§4.1) goes into `OrgInfoPage.intro`, not an article. The three fixed blocks stay blank for Josh. The command prints what it wrote and what it skipped.

Keep it in the command as a module-level list of dicts so it reads as data.

**Tone — rewrite the plan copy before seeding, do not paste it verbatim.** Target an "explain it like I'm 14" register: concise and clear, short sentences, plain words, no filler. A curious teenager should get it on one read. Cut throat-clearing ("This is where...", "Good to know:"), collapse multi-clause sentences, prefer a 4-step numbered list over a paragraph of prose. The `wiki-content-plan.md` copy is accurate but too wordy for this register — treat it as the source of *facts and structure*, then tighten the wording. Still no em dashes or standalone hyphen-dashes (Josh pastes it). Keep every verified fact and permission caveat intact; brevity must not drop a "guild leads only" or "not confirmed until a lead approves." Aim roughly one third shorter than the plan drafts.

## Blocked-on-Josh, do NOT invent

These are carried as placeholders, not guesses:
- **Discord command list** — appended to guide 7 only after Josh confirms `DISCORD_INTERACTIONS_PUBLIC_KEY` is set on Render. Seed command leaves guide 7 without it and logs a note.
- **"Leave a guild" label** ("Settings > My Guilds") — plan flags it UNVERIFIED. Open `templates/hub/user_settings.html` during the build and confirm the exact label; correct the copy if it differs.
- **Teaching entry point** — if a "Teach" link exists in the member nav, replace "add /classes/teach/ to the site address" with the real click path. Check `base.html`.
- **Orientation gates tool use** — policy claim. Keep the wording but it is Josh's to confirm.
- **Org-level voting** — none exists in code; the Voting guide covers guild funding only.

## Tests (98% gate, BDD `*_spec.py`)

- `tests/membership/wiki_article_spec.py` (new): slug auto-fill from title; de-dup within a page; slug stability (existing slug not overwritten on re-save); `ordering`; unique constraint; `published()`.
- `tests/hub/wiki_spec.py` (extend): read view renders a published article's `<section id=slug>` and its TOC link; a draft (`is_published=False`) is absent for a guest; `articles` context is present.
- `tests/hub/wiki_spec.py` editor block (extend): the Articles tab renders for an admin; `wiki_articles_save` saves a valid formset (302) and rejects an invalid one; a member gets 403 from `wiki_articles_save`.
- `tests/membership/…` seed command spec: idempotent (running twice yields the same count), `--dry-run` writes nothing.
- Factory: `WikiArticleFactory` in `tests/membership/factories.py` (Sequence title, `skip_postgeneration_save = True` per repo convention).

## Version + changelog

Bump `VERSION` to `0.23.32`. This is intra-cycle refinement of the same unreleased 0.23 map/Spaces feature line, and the changelog already has the 0.23.31 "new Spaces page + Wiki page" entry. Per the changelog rule, **edit that entry** (re-stamp to 0.23.32, add a bullet that the Wiki now carries how-it-works guides) rather than adding a second entry. Members see one announcement.

## Verify before commit (do NOT trust, check)

- Full suite green to 98%; ruff + mypy + `makemigrations --check` clean.
- In the browser on the live `spacemap` stack: `/wiki/` shows the TOC and every seeded guide; `/wiki/#guild-voting` scrolls to the Voting section; a drafted article is invisible to a logged-out viewer; the Articles editor tab adds/saves/deletes a row. Screenshot light and dark.
- Confirm the three pre-existing environment-only failures are the ONLY failures (two `settings_spec` email backend, one `discord_class_posts` title length), by diffing against the base commit as we did for the split.

## Standing environment notes

Fresh worktree off `origin/feat/interactive-space-map`; symlink `.venv` AND `.env` (missing `.env` breaks `git push` via the mypy-django plugin). Local Postgres is the `spacemap` stack on host port **5433**, not 5432 (5432 is an unrelated automatiq stack). `.env` carries live R2 creds, so avoid ImageField saves against real storage. Never touch `/home/josh/Code/plfog`.
