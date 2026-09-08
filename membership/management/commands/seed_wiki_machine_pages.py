"""Seed one wiki stub page per active piece of equipment, so the wiki is never empty.

The brief's answer to "nobody writes the first page": every machine already has a page
waiting, so a member's first contribution is an *edit* rather than a *creation*, which is
psychologically an order of magnitude cheaper.

Follows ``seed_help_center``: module data in ``membership/wiki_starters.py``, keyed
syncs, a plain-text report, no ``self.style.SUCCESS``, no transaction wrapper.

Three rules make it safe to re-run against production:

1. **It refuses an empty register.** The reference app's ``sync_docs`` once removed 50
   documents on a bare run; an empty ``Equipment`` table here means a broken import, and
   exiting 0 having created nothing would hide it.
2. **It adopts rather than duplicates.** The wiki has been writable since PR A3 and this
   command does not run until A4, so by launch day a member may well have written
   "Table Saw" by hand. A matching unlinked page is *claimed* — the equipment link is set
   and not one word of content is touched. Creating a twin would split the knowledge
   across two URLs and make the member's page look like the wrong one; raising on the
   clash would turn one member's good page into a red deploy job.
3. **It never writes content a person has touched.** ``body_edited_at is None`` means the
   body is still exactly what this command wrote, and that is the only state in which it
   writes ``title``, ``body``, or the starter Quick Answers rows again.

Deliberately NOT in ``render.yaml``'s ``buildCommand``. ``seed_help_center`` runs on every
deploy because it owns its content; this one hands its rows to members on the first run
and must never race a deploy against a member's edit. It is a one-off Render job before
launch, re-run by hand when new equipment is added.

Usage::

    manage.py seed_wiki_machine_pages [--dry-run]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from django.utils.text import slugify

from membership.wiki_starters import STARTERS

if TYPE_CHECKING:
    from membership.models import Equipment, WikiPage

# The note on the revision a page gets when the seeder claims a member's page. It is the
# only trace adoption leaves, and spec D's history list is where a lead reads it.
ADOPTION_NOTE = "Linked to the equipment register"
CREATION_NOTE = "Created from the equipment register"


class Command(BaseCommand):
    help = (
        "Seed a wiki stub page for every active piece of equipment. Idempotent: adopts a "
        "matching member-written page rather than duplicating it, and never overwrites a "
        "body a person has edited. Use --dry-run to preview."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing any rows.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.models import Equipment

        dry_run = bool(options["dry_run"])
        if not Equipment.objects.exists():
            raise CommandError(
                "No equipment rows found. Refusing to run: an empty register would create nothing "
                "and hide a broken import behind a clean exit."
            )

        counts = {"added": 0, "adopted": 0, "refreshed": 0, "skipped": 0, "clashed": 0}
        tools = Equipment.objects.active().select_related("guild").order_by("name")
        for tool in tools:
            outcome = self._sync_tool(tool, dry_run=dry_run)
            counts[outcome] += 1
        self._report(len(tools), counts, dry_run=dry_run)

    def _sync_tool(self, tool: Equipment, *, dry_run: bool) -> str:
        """Bring one tool's page into line, returning the count bucket it belongs in."""
        from membership.models import WikiError, WikiPage

        page = WikiPage.objects.filter(equipment=tool).first()
        if page is not None:
            # An archived page is a deliberate act by a moderator. The seeder does not
            # resurrect it, re-wire it, or rewrite it — it just leaves.
            if page.archived_at is not None:
                return "skipped"
            return self._refresh(page, tool, adopted=False, dry_run=dry_run)

        candidate = self._adoptable(tool)
        if candidate is not None:
            return self._refresh(candidate, tool, adopted=True, dry_run=dry_run)

        try:
            self._create(tool, dry_run=dry_run)
        except (WikiError, IntegrityError) as exc:
            # A member page with this title in this scope that could not be adopted (it is
            # already linked to a different tool). One tool's name clash must never take
            # the whole run down, so it is reported by name and the run continues.
            self.stdout.write(f"  Could not seed '{tool.name}': {exc}")
            return "clashed"
        return "added"

    @staticmethod
    def _adoptable(tool: Equipment) -> WikiPage | None:
        """An unlinked, live page that is plainly already about this tool, or None.

        Matched on the slug first and the title second, both of which a member typing the
        tool's name would land on. Archived pages are excluded (adopting one would put a
        machine's stickered page somewhere nobody can find it), and so is any page already
        pointing at a *different* tool — that page is somebody's deliberate filing.
        """
        from membership.models import WikiPage

        unlinked = WikiPage.objects.filter(equipment__isnull=True).not_archived()
        by_slug = unlinked.filter(slug=slugify(tool.name)).order_by("pk").first()
        if by_slug is not None:
            return by_slug
        return unlinked.filter(title__iexact=tool.name.strip()).order_by("pk").first()

    def _refresh(self, page: WikiPage, tool: Equipment, *, adopted: bool, dry_run: bool) -> str:
        """Re-wire an existing page to the tool, and refresh its content only if seed-owned."""
        from membership.models import WikiPageFact

        # An adopted page keeps every word the member wrote, whatever body_edited_at says:
        # the point of adoption is that the member's page IS the machine's page.
        content_is_seed_owned = page.body_edited_at is None and not adopted
        changed = self._rewire(page, tool, refresh_content=content_is_seed_owned)
        if dry_run:
            return "adopted" if adopted else ("refreshed" if changed else "skipped")

        if changed:
            page.save()
        if content_is_seed_owned:
            self._reseed_facts(page, WikiPageFact)
        if adopted:
            self._write_adoption_revision(page)
            return "adopted"
        return "refreshed" if changed else "skipped"

    @staticmethod
    def _rewire(page: WikiPage, tool: Equipment, *, refresh_content: bool) -> list[str]:
        """Set the fields this run owns on ``page``, returning the names it changed.

        The split between the two halves is the whole guard. Structure — kind, the
        equipment link, a blank guild — is wiring no member can edit on the page, so
        writing it is always safe. Content is the member's, and once ``body_edited_at``
        says a person has written here it is never written again. ``body_edited_at`` beats
        the alternatives because a revision count moves on the seeder's own first revision
        and on any revert, and an ``is_seed_owned`` flag answers "who created this row"
        rather than "has a person written here", which is the question that matters.

        Nothing is saved here; the caller decides, so ``--dry-run`` can ask what would
        change without a write.
        """
        from membership.models import WikiPage

        changed: list[str] = []
        if page.kind != WikiPage.Kind.MACHINE:
            page.kind = WikiPage.Kind.MACHINE
            changed.append("kind")
        if page.equipment_id != tool.pk:
            page.equipment = tool
            changed.append("equipment")
        # Spec §5.11: the guild is filled only when the page's is blank. A page that
        # already carries a scope carries somebody's filing decision, and this command is
        # not the place to overrule it.
        if page.guild_id is None and tool.guild_id is not None:
            page.guild = tool.guild
            changed.append("guild")
        if not refresh_content:
            return changed
        starter = STARTERS[WikiPage.Kind.MACHINE.value]
        if page.title != tool.name:
            page.title = tool.name
            changed.append("title")
        if page.body != starter["body"]:
            page.body = starter["body"]
            changed.append("body")
        return changed

    @staticmethod
    def _write_adoption_revision(page: WikiPage) -> None:
        """The one trace adoption leaves, for the lead reading spec D's history list.

        Written even when nothing else changed, so the history can say when the register
        claimed the page. Authorless, like every row this command writes.
        """
        from membership.models import WikiRevision

        WikiRevision.objects.create(
            page=page,
            title=page.title,
            body=page.body,
            facts=page.fact_snapshot(),
            status=page.status,
            author=None,
            note=ADOPTION_NOTE,
        )

    @staticmethod
    def _reseed_facts(page: WikiPage, fact_model: Any) -> None:
        """Put the starter Quick Answers prompts back on a page nobody has written yet.

        Blank answers on purpose: the labels are the questions the page is asking for, and
        the edit form renders saved rows with ``extra=0``, so a stub with no rows would
        offer a member no prompts at all. A member who leaves a prompt blank has it
        dropped on save, which is how an untouched prompt disappears for good.
        """
        from membership.models import WikiPage as _WikiPage

        prompts = STARTERS[_WikiPage.Kind.MACHINE.value]["fact_prompts"]
        existing = {fact.label for fact in page.facts.all()}
        # Only ADD missing prompts: a member may have answered one already through the
        # quick paths, and rebuilding the set wholesale would delete their answer.
        created = False
        for index, prompt in enumerate(prompts):
            if prompt in existing:
                continue
            fact_model.objects.create(page=page, label=prompt, value="", sort_order=index)
            created = True
        if created:
            page.rebuild_search_text()
            page.save(update_fields=["search_text"])

    @staticmethod
    def _create(tool: Equipment, *, dry_run: bool) -> None:
        """Create the stub through the one creation path, so it has a revision behind it."""
        from membership.models import WikiPage

        if dry_run:
            return
        starter = STARTERS[WikiPage.Kind.MACHINE.value]
        WikiPage.objects.create_page(
            title=tool.name,
            kind=WikiPage.Kind.MACHINE,
            # No author: create_page leaves body_edited_at blank for an authorless page,
            # which is exactly the "still what the seeder wrote" state a later run reads.
            author=None,
            guild=tool.guild,
            equipment=tool,
            body=starter["body"],
            facts=[(prompt, "") for prompt in starter["fact_prompts"]],
            note=CREATION_NOTE,
        )

    def _report(self, total: int, counts: dict[str, int], *, dry_run: bool) -> None:
        prefix = "Would seed" if dry_run else "Seeded"
        line = (
            f"{prefix} {total} machine pages: {counts['added']} added, {counts['adopted']} adopted, "
            f"{counts['refreshed']} refreshed, {counts['skipped']} skipped (edited by members)."
        )
        if counts["clashed"]:
            line += f" {counts['clashed']} could not be seeded because of a name clash."
        self.stdout.write(line)
