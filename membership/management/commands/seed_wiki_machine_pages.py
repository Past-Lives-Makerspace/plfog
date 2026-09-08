"""Seed one wiki stub page per active piece of equipment, so the wiki is never empty.

The brief's answer to "nobody writes the first page": every machine already has a page
waiting, so a member's first contribution is an *edit* rather than a *creation*, which is
psychologically an order of magnitude cheaper.

Follows ``seed_help_center``: module data in ``membership/wiki_starters.py``, keyed
syncs, a plain-text report, no ``self.style.SUCCESS``, no transaction wrapper.

Four rules make it safe to re-run against production:

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
4. **It only ever claims a MACHINE page.** ``uq_wikipage_machine_equip`` is a *partial*
   constraint, so a How-To may legally carry the same equipment link — and the moderator
   Equipment select this spec asks for is exactly how a lead creates one. Matching without
   the ``kind`` filter would find that How-To, flip it to ``MACHINE``, and break the
   constraint, ending the whole run partway through a production job.

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
from django.db import IntegrityError, transaction
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

        # A real run claims a page by writing its equipment link, so a second tool cannot
        # find it again. A dry run writes nothing, so it has to remember by hand — without
        # these two sets the preview an operator runs before touching production reports
        # one page adopted twice and never predicts a name clash at all.
        self._claimed: set[int] = set()
        self._planned: set[tuple[str, int | None]] = set()

        counts = {"added": 0, "adopted": 0, "refreshed": 0, "skipped": 0, "archived": 0, "clashed": 0}
        # ("name", "pk"): two tools may share a name, and which of them adopts the one
        # matching page must not vary between the dry run, the real run, and a re-run.
        tools = list(Equipment.objects.active().select_related("guild").order_by("name", "pk"))
        for tool in tools:
            counts[self._sync_tool(tool, dry_run=dry_run)] += 1
        self._report(len(tools), counts, dry_run=dry_run)

    def _sync_tool(self, tool: Equipment, *, dry_run: bool) -> str:
        """Bring one tool's page into line, returning the count bucket it belongs in."""
        from membership.models import WikiError, WikiPage

        # The kind filter is load bearing. uq_wikipage_machine_equip is a PARTIAL
        # constraint, so a How-To may legally carry this same equipment link — and the
        # moderator Equipment select is how a lead makes one. Without the filter this
        # picks up that How-To, _rewire flips it to MACHINE, and the save violates the
        # constraint, ending a production run partway through.
        page = WikiPage.objects.filter(equipment=tool, kind=WikiPage.Kind.MACHINE).order_by("pk").first()
        if page is not None:
            # An archived page is a deliberate act by a moderator. The seeder does not
            # resurrect it, re-wire it, or rewrite it — it just leaves.
            if page.archived_at is not None:
                return "archived"
            return self._refresh(page, tool, adopted=False, dry_run=dry_run)

        candidate = self._adoptable(tool)
        if candidate is not None:
            self._claimed.add(candidate.pk)
            return self._refresh(candidate, tool, adopted=True, dry_run=dry_run)

        try:
            self._create(tool, dry_run=dry_run)
        except (WikiError, IntegrityError) as exc:
            # A member page with this title in this scope that could not be adopted. One
            # tool's name clash must never take the whole run down, so it is reported by
            # name and the run continues.
            self.stdout.write(f"  Could not seed '{tool.name}': {exc}")
            return "clashed"
        return "added"

    def _adoptable(self, tool: Equipment) -> WikiPage | None:
        """An unlinked, live page that is plainly already about this tool, or None.

        Matched on the slug first and the title second, both of which a member typing the
        tool's name would land on, and **within the tool's own scope first**. Scope matters:
        a page filed under Woodworking must not become the Metal guild's tool page, wearing
        its sticker while listed in the wrong shop's Wiki tab. Archived pages are excluded
        (adopting one would put a stickered page where nobody can find it), and so is any
        page already pointing at a different tool — that is somebody's deliberate filing.
        """
        from membership.models import WikiPage

        unlinked = WikiPage.objects.filter(equipment__isnull=True).not_archived().exclude(pk__in=self._claimed)
        # The tool's own guild first, then space wide. A guild-less tool only ever adopts
        # a space-wide page, which is the same query.
        scopes = [tool.guild] if tool.guild_id is None else [tool.guild, None]
        for scope in scopes:
            in_scope = unlinked.filter(guild=scope)
            by_slug = in_scope.filter(slug=slugify(tool.name)).order_by("pk").first()
            if by_slug is not None:
                return by_slug
            by_title = in_scope.filter(title__iexact=tool.name.strip()).order_by("pk").first()
            if by_title is not None:
                return by_title
        return None

    def _refresh(self, page: WikiPage, tool: Equipment, *, adopted: bool, dry_run: bool) -> str:
        """Re-wire an existing page to the tool, and refresh its content only if seed-owned.

        The bucket a page lands in is decided by *who owns its content*, not by whether any
        column happened to differ: a seed-owned page the run re-synced is **refreshed** even
        when nothing changed, and only a page a person has written is **skipped**. Reporting
        an untouched stub as "skipped (edited by members)" was a false statement in the job
        log on every re-run.
        """
        from membership.models import WikiPageFact

        # An adopted page keeps every word the member wrote, whatever body_edited_at says:
        # the point of adoption is that the member's page IS the machine's page.
        content_is_seed_owned = page.body_edited_at is None and not adopted
        changed = self._rewire(page, tool, refresh_content=content_is_seed_owned)
        if dry_run:
            return "adopted" if adopted else ("refreshed" if content_is_seed_owned else "skipped")

        if changed:
            page.save()
        if content_is_seed_owned:
            self._reseed_facts(page, WikiPageFact)
        if adopted:
            self._write_adoption_revision(page)
            return "adopted"
        return "refreshed" if content_is_seed_owned else "skipped"

    @staticmethod
    def _rewire(page: WikiPage, tool: Equipment, *, refresh_content: bool) -> list[str]:
        """Set the fields this run owns on ``page``, returning the names it changed.

        The split between the two halves is the whole guard. Structure — kind, the
        equipment link, the guild — is wiring no member can edit on the page (the edit form
        carries title, body and a moderator-only equipment select, and no guild), so writing
        it is safe. Content is the member's, and once ``body_edited_at`` says a person has
        written here it is never written again. ``body_edited_at`` beats the alternatives
        because a revision count moves on the seeder's own first revision and on any revert,
        and an ``is_seed_owned`` flag answers "who created this row" rather than "has a
        person written here", which is the question that matters.

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
        # A blank scope is always filled. Beyond that, a page whose content this command
        # still owns follows the tool when the tool moves shops — its guild was chosen by
        # the seeder, not by a person, and nothing in the UI could correct it later. A page
        # a member has written stays where they filed it.
        follows_tool = page.guild_id is None or refresh_content
        if follows_tool and page.guild_id != tool.guild_id:
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

    def _create(self, tool: Equipment, *, dry_run: bool) -> None:
        """Create the stub through the one creation path, so it has a revision behind it."""
        from membership.models import WikiPage

        title = tool.name.strip()
        if dry_run:
            # Ask the same question create_page would, so the preview an operator trusts
            # before a production run reports the clashes the real run will hit.
            self._predict_clash(title, tool.guild)
            self._planned.add((title.lower(), tool.guild_id))
            return
        starter = STARTERS[WikiPage.Kind.MACHINE.value]
        # A savepoint, so a losing race against a concurrent writer rolls back this one
        # create instead of poisoning the transaction and taking the rest of the run with
        # it. In autocommit the command survives either way; inside an atomic block it
        # would not.
        with transaction.atomic():
            WikiPage.objects.create_page(
                title=title,
                kind=WikiPage.Kind.MACHINE,
                # No author: create_page leaves body_edited_at blank for an authorless
                # page, which is exactly the "still what the seeder wrote" state a later
                # run reads.
                author=None,
                guild=tool.guild,
                equipment=tool,
                body=starter["body"],
                facts=[(prompt, "") for prompt in starter["fact_prompts"]],
                note=CREATION_NOTE,
            )

    def _predict_clash(self, title: str, guild: Any) -> None:
        """Raise the WikiError a real run would raise, so --dry-run can be trusted."""
        from membership.models import WikiError, WikiPage

        guild_id = guild.pk if guild is not None else None
        planned = (title.lower(), guild_id) in self._planned
        if planned or WikiPage.objects.filter(title__iexact=title, guild=guild).not_archived().exists():
            scope = f"in {guild.name}" if guild is not None else "space wide"
            raise WikiError(f"A page called '{title}' already exists {scope}. Add to that one instead.")

    def _report(self, total: int, counts: dict[str, int], *, dry_run: bool) -> None:
        prefix = "Would seed" if dry_run else "Seeded"
        line = (
            f"{prefix} {total} machine pages: {counts['added']} added, {counts['adopted']} adopted, "
            f"{counts['refreshed']} refreshed, {counts['skipped']} left alone (a member has written there)."
        )
        if counts["archived"]:
            line += f" {counts['archived']} archived and left untouched."
        if counts["clashed"]:
            line += f" {counts['clashed']} could not be seeded because of a name clash."
        self.stdout.write(line)
