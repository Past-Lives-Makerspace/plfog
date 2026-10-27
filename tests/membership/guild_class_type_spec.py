"""BDD specs for Guild.ensure_class_type: every active guild has a Class Type on the class composer."""

from __future__ import annotations

from django.utils import timezone

from classes.models import Category
from membership.models import Guild


def describe_guild_class_type_name():
    def it_drops_a_trailing_guild_from_the_name(db):
        assert Guild(name="Printmaking Guild").class_type_name == "Printmaking"
        assert Guild(name="Prison Outreach guild ").class_type_name == "Prison Outreach"

    def it_keeps_a_name_without_a_trailing_guild(db):
        assert Guild(name="Guild of Glass").class_type_name == "Guild of Glass"


def describe_saving_a_guild():
    def it_creates_a_class_type_for_a_new_active_guild(db):
        guild = Guild.objects.create(name="Printmaking Guild")

        category = Category.objects.get(guild=guild)
        assert (category.name, category.slug, category.sort_order) == ("Printmaking", "printmaking", 0)

    def it_creates_none_for_an_inactive_guild(db):
        guild = Guild.objects.create(name="Cartographers Guild", is_active=False)

        assert not Category.objects.filter(guild=guild).exists()

    def it_creates_one_when_an_inactive_guild_becomes_active(db):
        guild = Guild.objects.create(name="Cartographers Guild", is_active=False)
        guild.is_active = True
        guild.save()

        assert Category.objects.get(guild=guild).name == "Cartographers"

    def it_creates_none_for_a_soft_deleted_guild(db):
        guild = Guild.objects.create(name="Gone Guild", is_active=False)
        guild.deleted_at = timezone.now()
        guild.is_active = True
        guild.save()

        assert not Category.objects.filter(guild_id=guild.pk).exists()

    def it_keeps_the_class_type_on_deactivation(db):
        guild = Guild.objects.create(name="Printmaking Guild")
        guild.is_active = False
        guild.save()

        assert Category.objects.filter(guild=guild).count() == 1


def describe_ensure_class_type():
    def it_returns_the_linked_class_type_without_adding_another(db):
        guild = Guild.objects.create(name="Wood Guild")
        linked = Category.objects.get(guild=guild)
        linked.name = "Woodworking"
        linked.save()

        assert guild.ensure_class_type() == linked
        assert Category.objects.filter(guild=guild).count() == 1

    def it_links_an_unlinked_class_type_of_the_same_name(db):
        existing = Category.objects.create(name="printmaking", slug="print-making")

        guild = Guild.objects.create(name="Printmaking Guild")

        existing.refresh_from_db()
        assert existing.guild == guild
        assert Category.objects.filter(guild=guild).count() == 1

    def it_links_an_unlinked_class_type_with_the_same_slug(db):
        existing = Category.objects.create(name="Print Making", slug="printmaking")

        guild = Guild.objects.create(name="Printmaking Guild")

        existing.refresh_from_db()
        assert existing.guild == guild

    def it_never_takes_another_guilds_class_type(db):
        owner = Guild.objects.create(name="Printmaking")
        other = Guild.objects.create(name="Printmaking Guild")

        assert other.ensure_class_type() is None
        assert Category.objects.get(name="Printmaking").guild == owner
        assert not Category.objects.filter(guild=other).exists()

    def it_falls_back_to_the_guild_slug_when_the_name_has_no_slug(db):
        guild = Guild.objects.create(name="!!!")

        assert Category.objects.get(guild=guild).slug == guild.slug
