"""BDD specs for the seed_wiki_machine_pages management command (spec A §5.11).

The command runs as a one-off Render job against production Equipment data, so the
guards that matter here are the ones that keep it from destroying member writing:
it refuses an empty register, it adopts rather than duplicates, and it never rewrites
a body a person has touched.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction

from membership.management.commands.seed_wiki_machine_pages import MACHINE_FACT_PROMPTS
from membership.models import WikiPage, WikiPageFact, WikiRevision
from membership.wiki_starters import STARTERS
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    MemberFactory,
    WikiPageFactFactory,
    WikiPageFactory,
)

pytestmark = pytest.mark.django_db

MACHINE_STARTER = STARTERS["machine"]


def _run(**options: object) -> str:
    out = StringIO()
    call_command("seed_wiki_machine_pages", stdout=out, **options)
    return out.getvalue()


def describe_seed_wiki_machine_pages():
    def describe_an_empty_register():
        def it_refuses_to_run(db):
            with pytest.raises(CommandError, match="No equipment rows found"):
                _run()

        def it_writes_nothing(db):
            with pytest.raises(CommandError):
                _run()
            assert WikiPage.objects.count() == 0

    def describe_a_first_run():
        def it_creates_one_page_per_active_tool(db):
            EquipmentFactory(name="Table Saw")
            EquipmentFactory(name="Kiln")
            _run()
            assert set(WikiPage.objects.values_list("title", flat=True)) == {"Table Saw", "Kiln"}

        def it_writes_the_machine_starter_body(db):
            EquipmentFactory(name="Table Saw")
            _run()
            assert WikiPage.objects.get().body == MACHINE_STARTER["body"]

        def it_files_the_page_as_a_machine_linked_to_the_tool(db):
            tool = EquipmentFactory(name="Table Saw")
            _run()
            page = WikiPage.objects.get()
            assert page.kind == WikiPage.Kind.MACHINE
            assert page.equipment_id == tool.pk

        def it_takes_the_guild_from_the_tool(db):
            guild = GuildFactory(name="Woodworking")
            EquipmentFactory(name="Table Saw", guild=guild)
            _run()
            assert WikiPage.objects.get().guild_id == guild.pk

        def it_fills_a_sticker_code(db):
            EquipmentFactory(name="Table Saw")
            _run()
            assert len(WikiPage.objects.get().qr_code) == 6

        def it_seeds_the_starter_quick_answer_prompts(db):
            EquipmentFactory(name="Table Saw")
            _run()
            page = WikiPage.objects.get()
            assert list(page.facts.values_list("label", flat=True)) == MACHINE_FACT_PROMPTS
            assert {fact.value for fact in page.facts.all()} == {""}

        def it_leaves_the_page_community_and_published(db):
            EquipmentFactory(name="Table Saw")
            _run()
            page = WikiPage.objects.get()
            assert page.status == WikiPage.Status.COMMUNITY
            assert page.is_published is True

        def it_writes_a_first_revision_with_no_author(db):
            EquipmentFactory(name="Table Saw")
            _run()
            revision = WikiRevision.objects.get()
            assert revision.author is None
            assert revision.note == "Created from the equipment register"

        def it_leaves_the_body_marked_as_still_seed_owned(db):
            EquipmentFactory(name="Table Saw")
            _run()
            assert WikiPage.objects.get().body_edited_at is None

        def it_skips_retired_equipment(db):
            EquipmentFactory(name="Table Saw")
            EquipmentFactory(name="Old Lathe", is_active=False)
            _run()
            assert list(WikiPage.objects.values_list("title", flat=True)) == ["Table Saw"]

        def it_reports_what_it_did(db):
            EquipmentFactory(name="Table Saw")
            assert "Seeded 1 machine pages: 1 added, 0 adopted, 0 refreshed, 0 left alone" in _run()

    def describe_a_second_run():
        def it_creates_no_second_page(db):
            EquipmentFactory(name="Table Saw")
            _run()
            _run()
            assert WikiPage.objects.count() == 1

        def it_changes_nothing(db):
            EquipmentFactory(name="Table Saw")
            _run()
            before = WikiPage.objects.values().get()
            _run()
            assert WikiPage.objects.values().get() == before

        def it_writes_no_second_revision(db):
            EquipmentFactory(name="Table Saw")
            _run()
            _run()
            assert WikiRevision.objects.count() == 1

        def it_reports_the_page_as_refreshed_not_left_alone(db):
            # A stub nobody has written is still this command's to re-sync, so reporting it
            # as "left alone (a member has written there)" would be a false job log.
            EquipmentFactory(name="Table Saw")
            _run()
            assert "0 added, 0 adopted, 1 refreshed, 0 left alone" in _run()

    def describe_a_page_a_member_has_edited():
        @pytest.fixture
        def edited(db):
            tool = EquipmentFactory(name="Table Saw")
            _run()
            page = WikiPage.objects.get()
            member = MemberFactory()
            page.apply_edit(
                editor=member,
                editor_may_verify=False,
                title="The Big SawStop",
                body="<p>Push sticks live in the drawer.</p>",
            )
            return tool, WikiPage.objects.get(pk=page.pk)

        def it_leaves_the_title_alone(db, edited):
            _tool, page = edited
            _run()
            page.refresh_from_db()
            assert page.title == "The Big SawStop"

        def it_leaves_the_body_alone(db, edited):
            _tool, page = edited
            _run()
            page.refresh_from_db()
            assert page.body == "<p>Push sticks live in the drawer.</p>"

        def it_leaves_the_facts_alone(db, edited):
            _tool, page = edited
            page.facts.all().delete()
            WikiPageFactFactory(page=page, label="Blade", value="40 tooth")
            _run()
            assert list(page.facts.values_list("label", "value")) == [("Blade", "40 tooth")]

        def it_reports_the_page_as_left_alone(db, edited):
            assert "0 added, 0 adopted, 0 refreshed, 1 left alone (a member has written there)" in _run()

        def it_does_not_follow_the_tool_to_a_new_guild(db, edited):
            # A page a member has written stays where it is filed. The seeder owns the
            # scope only while it still owns the content.
            tool, page = edited
            kept = GuildFactory(name="Print")
            WikiPage.objects.filter(pk=page.pk).update(guild=kept)
            tool.guild = GuildFactory(name="Woodworking")
            tool.save(update_fields=["guild"])
            _run()
            page.refresh_from_db()
            assert page.guild_id == kept.pk

        def it_still_fills_a_blank_guild_from_the_tool(db, edited):
            tool, page = edited
            guild = GuildFactory(name="Woodworking")
            tool.guild = guild
            tool.save(update_fields=["guild"])
            WikiPage.objects.filter(pk=page.pk).update(guild=None)
            _run()
            page.refresh_from_db()
            assert page.guild_id == guild.pk

    def describe_adoption():
        def it_claims_a_member_page_whose_slug_matches_the_tool(db):
            tool = EquipmentFactory(name="Table Saw")
            page = WikiPageFactory(title="Table Saw", body="<p>Everything I know.</p>")
            _run()
            page.refresh_from_db()
            assert page.equipment_id == tool.pk

        def it_claims_a_member_page_whose_title_matches_case_insensitively(db):
            tool = EquipmentFactory(name="Table Saw")
            page = WikiPageFactory(title="TABLE SAW", slug="the-big-one")
            _run()
            page.refresh_from_db()
            assert page.equipment_id == tool.pk

        def it_makes_no_second_page_for_that_tool(db):
            EquipmentFactory(name="Table Saw")
            WikiPageFactory(title="Table Saw")
            _run()
            assert WikiPage.objects.count() == 1

        def it_changes_not_one_word_of_the_title_or_body(db):
            EquipmentFactory(name="Table Saw")
            page = WikiPageFactory(title="Table Saw", body="<p>Everything I know.</p>")
            _run()
            page.refresh_from_db()
            assert page.title == "Table Saw"
            assert page.body == "<p>Everything I know.</p>"

        def it_leaves_a_differently_capitalised_title_exactly_as_written(db):
            EquipmentFactory(name="Table Saw")
            page = WikiPageFactory(title="TABLE SAW", slug="the-big-one")
            _run()
            page.refresh_from_db()
            assert page.title == "TABLE SAW"

        def it_leaves_the_members_facts_untouched(db):
            EquipmentFactory(name="Table Saw")
            page = WikiPageFactory(title="Table Saw")
            WikiPageFactFactory(page=page, label="Blade", value="40 tooth")
            _run()
            assert list(page.facts.values_list("label", "value")) == [("Blade", "40 tooth")]

        def it_files_the_adopted_page_as_a_machine(db):
            EquipmentFactory(name="Table Saw")
            page = WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.HOWTO)
            _run()
            page.refresh_from_db()
            assert page.kind == WikiPage.Kind.MACHINE

        def it_fills_a_blank_guild_from_the_tool(db):
            guild = GuildFactory(name="Woodworking")
            EquipmentFactory(name="Table Saw", guild=guild)
            page = WikiPageFactory(title="Table Saw", guild=None)
            _run()
            page.refresh_from_db()
            assert page.guild_id == guild.pk

        def it_keeps_a_guild_the_member_already_chose(db):
            EquipmentFactory(name="Table Saw", guild=GuildFactory(name="Woodworking"))
            chosen = GuildFactory(name="Print")
            page = WikiPageFactory(title="Table Saw", guild=chosen)
            _run()
            page.refresh_from_db()
            assert page.guild_id == chosen.pk

        def it_records_the_link_in_the_history(db):
            EquipmentFactory(name="Table Saw")
            page = WikiPageFactory(title="Table Saw")
            _run()
            assert page.revisions.filter(note="Linked to the equipment register").exists()

        def it_reports_the_page_as_adopted_and_not_added(db):
            EquipmentFactory(name="Table Saw")
            WikiPageFactory(title="Table Saw")
            assert "0 added, 1 adopted" in _run()

        def it_leaves_a_page_pointing_at_a_different_tool_alone(db):
            other = EquipmentFactory(name="Bandsaw")
            EquipmentFactory(name="Table Saw")
            page = WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=other)
            _run()
            page.refresh_from_db()
            assert page.equipment_id == other.pk

        def it_never_adopts_an_archived_page(db):
            tool = EquipmentFactory(name="Table Saw")
            archived = WikiPageFactory(title="Table Saw", archived=True)
            _run()
            archived.refresh_from_db()
            assert archived.equipment_id is None
            assert WikiPage.objects.filter(equipment=tool).count() == 1

        def it_does_not_raise_when_a_second_tool_shares_a_page_title(db):
            EquipmentFactory(name="Table Saw", slug="table-saw")
            EquipmentFactory(name="Table Saw", slug="table-saw-2")
            WikiPageFactory(title="Table Saw")
            # Two tools, one adoptable page: the second cannot adopt and cannot create a
            # same-title page in the same scope, and that must not end the run.
            assert "could not be seeded because of a name clash" in _run()
            assert WikiPage.objects.count() == 1

        def it_still_seeds_the_other_tools_after_a_clash(db):
            EquipmentFactory(name="Table Saw", slug="table-saw")
            EquipmentFactory(name="Table Saw", slug="table-saw-2")
            EquipmentFactory(name="Kiln")
            WikiPageFactory(title="Table Saw")
            _run()
            assert WikiPage.objects.filter(title="Kiln").exists()

    def describe_an_archived_page_for_a_live_tool():
        def it_is_left_entirely_alone(db):
            tool = EquipmentFactory(name="Table Saw", guild=GuildFactory(name="Woodworking"))
            page = WikiPageFactory(
                title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool, guild=None, archived=True
            )
            _run()
            page.refresh_from_db()
            assert page.guild_id is None
            assert page.body == "How this part of the space works."

        def it_is_reported_separately_from_a_member_edit(db):
            tool = EquipmentFactory(name="Table Saw")
            WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool, archived=True)
            assert "1 archived and left untouched" in _run()

    def describe_a_stub_a_member_has_only_partly_answered():
        def it_keeps_the_answer_and_does_not_re_add_that_prompt(db):
            EquipmentFactory(name="Table Saw")
            _run()
            page = WikiPage.objects.get()
            page.facts.filter(label="Blade or bit").update(value="40 tooth")
            _run()
            values = dict(page.facts.values_list("label", "value"))
            assert values["Blade or bit"] == "40 tooth"
            assert len(values) == len(MACHINE_FACT_PROMPTS)

        def it_puts_back_a_prompt_row_that_was_removed(db):
            EquipmentFactory(name="Table Saw")
            _run()
            page = WikiPage.objects.get()
            page.facts.filter(label="Max size").delete()
            _run()
            assert page.facts.filter(label="Max size").exists()

    def describe_dry_run():
        def it_writes_no_rows(db):
            EquipmentFactory(name="Table Saw")
            _run(dry_run=True)
            assert WikiPage.objects.count() == 0

        def it_reports_what_it_would_add(db):
            EquipmentFactory(name="Table Saw")
            assert "Would seed 1 machine pages: 1 added" in _run(dry_run=True)

        def it_reports_an_adoption_without_making_one(db):
            EquipmentFactory(name="Table Saw")
            page = WikiPageFactory(title="Table Saw")
            assert "0 added, 1 adopted" in _run(dry_run=True)
            page.refresh_from_db()
            assert page.equipment_id is None

        def it_reports_an_edited_page_as_left_alone_without_touching_it(db):
            EquipmentFactory(name="Table Saw")
            _run()
            page = WikiPage.objects.get()
            page.apply_edit(editor=MemberFactory(), editor_may_verify=False, title="Mine", body="<p>Mine.</p>")
            assert "0 added, 0 adopted, 0 refreshed, 1 left alone" in _run(dry_run=True)
            page.refresh_from_db()
            assert page.title == "Mine"

        def it_writes_no_facts(db):
            EquipmentFactory(name="Table Saw")
            _run(dry_run=True)
            assert WikiPageFact.objects.count() == 0


def describe_the_one_machine_page_per_tool_constraint():
    def it_refuses_a_second_machine_page_for_one_tool(db):
        tool = EquipmentFactory(name="Table Saw")
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool)
        with pytest.raises(IntegrityError), transaction.atomic():
            WikiPageFactory(title="Table Saw Again", kind=WikiPage.Kind.MACHINE, equipment=tool)

    def it_allows_a_second_non_machine_page_about_the_same_tool(db):
        tool = EquipmentFactory(name="Table Saw")
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool)
        howto = WikiPageFactory(title="Ripping Thin Stock", kind=WikiPage.Kind.HOWTO, equipment=tool)
        assert howto.pk is not None


def describe_a_non_machine_page_linked_to_the_tool():
    """The partial constraint lets a How-To carry the same equipment link, and the
    moderator Equipment select is how a lead makes one. Matching without the kind filter
    picked that page up, flipped it to MACHINE, and broke the constraint mid-run."""

    def it_does_not_end_the_run(db):
        tool = EquipmentFactory(name="Table Saw")
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool)
        WikiPageFactory(title="Ripping Thin Stock", kind=WikiPage.Kind.HOWTO, equipment=tool)
        EquipmentFactory(name="Kiln")
        _run()
        assert WikiPage.objects.filter(title="Kiln").exists()

    def it_leaves_the_how_to_alone(db):
        tool = EquipmentFactory(name="Table Saw")
        WikiPageFactory(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool)
        howto = WikiPageFactory(title="Ripping Thin Stock", kind=WikiPage.Kind.HOWTO, equipment=tool)
        _run()
        howto.refresh_from_db()
        assert howto.kind == WikiPage.Kind.HOWTO
        assert howto.title == "Ripping Thin Stock"

    def it_still_seeds_a_tool_whose_only_page_is_a_how_to(db):
        # The How-To keeps its kind and its link; the tool gets the stub it was missing.
        tool = EquipmentFactory(name="Table Saw")
        howto = WikiPageFactory(title="Ripping Thin Stock", kind=WikiPage.Kind.HOWTO, equipment=tool)
        _run()
        howto.refresh_from_db()
        assert howto.kind == WikiPage.Kind.HOWTO
        assert WikiPage.objects.filter(title="Table Saw", kind=WikiPage.Kind.MACHINE, equipment=tool).exists()


def describe_adoption_across_guilds():
    """A page filed in one shop must not become another shop's tool page, wearing its
    sticker while listed under the wrong guild's Wiki tab."""

    def it_does_not_claim_another_guilds_page(db):
        woodworking = GuildFactory(name="Woodworking")
        EquipmentFactory(name="Table Saw", guild=GuildFactory(name="Metal"))
        theirs = WikiPageFactory(title="Table Saw", guild=woodworking)
        _run()
        theirs.refresh_from_db()
        assert theirs.equipment_id is None
        assert theirs.guild_id == woodworking.pk

    def it_creates_the_tools_own_page_instead(db):
        metal = GuildFactory(name="Metal")
        tool = EquipmentFactory(name="Table Saw", guild=metal)
        WikiPageFactory(title="Table Saw", guild=GuildFactory(name="Woodworking"))
        _run()
        assert WikiPage.objects.filter(equipment=tool, guild=metal).exists()

    def it_claims_a_page_in_the_tools_own_guild(db):
        guild = GuildFactory(name="Woodworking")
        tool = EquipmentFactory(name="Table Saw", guild=guild)
        theirs = WikiPageFactory(title="Table Saw", guild=guild)
        _run()
        theirs.refresh_from_db()
        assert theirs.equipment_id == tool.pk

    def it_claims_a_space_wide_page_for_a_guild_tool(db):
        guild = GuildFactory(name="Woodworking")
        tool = EquipmentFactory(name="Table Saw", guild=guild)
        theirs = WikiPageFactory(title="Table Saw", guild=None)
        _run()
        theirs.refresh_from_db()
        assert theirs.equipment_id == tool.pk
        assert theirs.guild_id == guild.pk

    def it_prefers_the_tools_own_guild_over_a_space_wide_page(db):
        guild = GuildFactory(name="Woodworking")
        tool = EquipmentFactory(name="Table Saw", guild=guild)
        space_wide = WikiPageFactory(title="Table Saw", guild=None, slug="table-saw-space")
        in_guild = WikiPageFactory(title="Table Saw", guild=guild, slug="table-saw")
        _run()
        space_wide.refresh_from_db()
        in_guild.refresh_from_db()
        assert in_guild.equipment_id == tool.pk
        assert space_wide.equipment_id is None

    def it_does_not_claim_a_guilds_page_for_a_standalone_tool(db):
        EquipmentFactory(name="Table Saw", guild=None)
        theirs = WikiPageFactory(title="Table Saw", guild=GuildFactory(name="Woodworking"))
        _run()
        theirs.refresh_from_db()
        assert theirs.equipment_id is None


def describe_a_seed_owned_page_whose_tool_moved_guilds():
    def it_follows_the_tool(db):
        # The page's guild was chosen by this command, not by a person, and no screen in
        # the app could correct it later.
        tool = EquipmentFactory(name="Table Saw", guild=GuildFactory(name="Woodworking"))
        _run()
        metal = GuildFactory(name="Metal")
        tool.guild = metal
        tool.save(update_fields=["guild"])
        _run()
        assert WikiPage.objects.get().guild_id == metal.pk

    def it_reports_the_move_as_a_refresh(db):
        tool = EquipmentFactory(name="Table Saw", guild=GuildFactory(name="Woodworking"))
        _run()
        tool.guild = GuildFactory(name="Metal")
        tool.save(update_fields=["guild"])
        assert "0 added, 0 adopted, 1 refreshed" in _run()


def describe_dry_run_accuracy():
    """The preview an operator runs before a production job has to match the real run."""

    def it_does_not_adopt_one_page_for_two_tools(db):
        EquipmentFactory(name="Table Saw", slug="table-saw")
        EquipmentFactory(name="Table Saw", slug="table-saw-2")
        WikiPageFactory(title="Table Saw")
        assert "0 added, 1 adopted" in _run(dry_run=True)

    def it_predicts_the_name_clash_the_real_run_hits(db):
        EquipmentFactory(name="Table Saw", slug="table-saw")
        EquipmentFactory(name="Table Saw", slug="table-saw-2")
        WikiPageFactory(title="Table Saw")
        assert "could not be seeded because of a name clash" in _run(dry_run=True)

    def it_predicts_a_clash_between_two_tools_it_would_create(db):
        EquipmentFactory(name="Table Saw", slug="table-saw")
        EquipmentFactory(name="Table Saw", slug="table-saw-2")
        assert "1 added" in _run(dry_run=True)
        assert "1 could not be seeded because of a name clash" in _run(dry_run=True)

    def it_matches_the_real_runs_counts(db):
        EquipmentFactory(name="Table Saw", slug="table-saw")
        EquipmentFactory(name="Table Saw", slug="table-saw-2")
        EquipmentFactory(name="Kiln")
        WikiPageFactory(title="Table Saw")
        preview = _run(dry_run=True).replace("Would seed", "Seeded")
        assert preview == _run()

    def it_writes_nothing_while_predicting(db):
        EquipmentFactory(name="Table Saw", slug="table-saw")
        EquipmentFactory(name="Table Saw", slug="table-saw-2")
        page = WikiPageFactory(title="Table Saw")
        _run(dry_run=True)
        page.refresh_from_db()
        assert page.equipment_id is None
        assert WikiPage.objects.count() == 1


def describe_two_tools_with_the_same_name():
    def it_always_gives_the_page_to_the_same_one(db):
        # No tiebreak on the name meant Postgres could hand the shared page to either tool,
        # so the dry run and the real run could disagree about which one clashed.
        first = EquipmentFactory(name="Table Saw", slug="table-saw")
        EquipmentFactory(name="Table Saw", slug="table-saw-2")
        page = WikiPageFactory(title="Table Saw")
        _run()
        page.refresh_from_db()
        assert page.equipment_id == first.pk


def describe_a_stub_that_has_drifted_from_the_register():
    """The left-hand column of the seeder's contract: while a page is still exactly what
    this command wrote, the command keeps it in step with the register."""

    def it_follows_a_renamed_tool(db):
        tool = EquipmentFactory(name="Table Saw")
        _run()
        tool.name = "SawStop Table Saw"
        tool.save(update_fields=["name"])
        _run()
        assert WikiPage.objects.get().title == "SawStop Table Saw"

    def it_keeps_the_slug_a_sticker_already_points_at(db):
        # The slug and the QR code are fill-once, so a rename must not break a printed
        # sticker or a deep link.
        tool = EquipmentFactory(name="Table Saw")
        _run()
        before = WikiPage.objects.values("slug", "qr_code").get()
        tool.name = "SawStop Table Saw"
        tool.save(update_fields=["name"])
        _run()
        assert WikiPage.objects.values("slug", "qr_code").get() == before

    def it_puts_back_a_starter_body_that_drifted(db):
        EquipmentFactory(name="Table Saw")
        _run()
        # update() and not apply_edit: this is drift on a page no person has written, which
        # is the only state in which the seeder owns the body.
        WikiPage.objects.update(body="<p>Something else entirely.</p>")
        _run()
        assert WikiPage.objects.get().body == MACHINE_STARTER["body"]

    def it_reports_the_repair_as_a_refresh(db):
        tool = EquipmentFactory(name="Table Saw")
        _run()
        tool.name = "SawStop Table Saw"
        tool.save(update_fields=["name"])
        assert "0 added, 0 adopted, 1 refreshed" in _run()

    def it_leaves_both_alone_once_a_person_has_written(db):
        tool = EquipmentFactory(name="Table Saw")
        _run()
        page = WikiPage.objects.get()
        page.apply_edit(editor=MemberFactory(), editor_may_verify=False, title="Big Saw", body="<p>Mine.</p>")
        tool.name = "SawStop Table Saw"
        tool.save(update_fields=["name"])
        _run()
        page.refresh_from_db()
        assert page.title == "Big Saw"
        assert page.body == "<p>Mine.</p>"


def describe_dry_run_parity_when_adoption_moves_a_page():
    """A real run re-scopes a space-wide page as it claims it, so the NEXT tool's clash
    check meets a page that has already moved. Both directions of that divergence were
    reachable with two active tools sharing a name, which nothing forbids."""

    def it_does_not_promise_a_page_the_real_run_refuses(db):
        # Two tools in one guild, one space-wide member page. The real run adopts it INTO
        # the guild, so the second tool then clashes; the preview used to say "added".
        guild = GuildFactory(name="Woodworking")
        EquipmentFactory(name="Drill Press", slug="drill-press", guild=guild)
        EquipmentFactory(name="Drill Press", slug="drill-press-2", guild=guild)
        WikiPageFactory(title="Drill Press", guild=None)
        preview = _run(dry_run=True).replace("Would seed", "Seeded")
        assert preview == _run()

    def it_does_not_invent_a_clash_the_real_run_never_hits(db):
        # A guild tool and a space-wide tool. The real run moves the page into the guild,
        # leaving the space-wide scope free; the preview used to report a clash.
        EquipmentFactory(name="Drill Press", slug="drill-press", guild=GuildFactory(name="Woodworking"))
        EquipmentFactory(name="Drill Press", slug="drill-press-2", guild=None)
        WikiPageFactory(title="Drill Press", guild=None)
        preview = _run(dry_run=True).replace("Would seed", "Seeded")
        assert preview == _run()

    def it_still_writes_nothing(db):
        guild = GuildFactory(name="Woodworking")
        EquipmentFactory(name="Drill Press", slug="drill-press", guild=guild)
        EquipmentFactory(name="Drill Press", slug="drill-press-2", guild=guild)
        page = WikiPageFactory(title="Drill Press", guild=None)
        _run(dry_run=True)
        page.refresh_from_db()
        assert page.equipment_id is None
        assert page.guild_id is None
        assert WikiPage.objects.count() == 1
