"""Site Settings demo-visibility flags gate demo content on member-facing surfaces.

Both flags default OFF, so seeded demo data (``demo-`` slug classes and the example
guild) stays hidden from members on production until an admin turns them on for a live
demo. Admin and teaching surfaces are never gated, so staff always manage demo content.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def _set(**flags) -> None:
    config = SiteConfiguration.load()
    for key, value in flags.items():
        setattr(config, key, value)
    config.save(update_fields=list(flags))


def _make_demo_and_real():
    from classes.factories import ClassOfferingFactory, ClassSessionFactory
    from classes.models import ClassOffering

    demo = ClassOfferingFactory(slug="demo-showcase", status=ClassOffering.Status.PUBLISHED, is_private=False)
    real = ClassOfferingFactory(slug="real-class", status=ClassOffering.Status.PUBLISHED, is_private=False)
    for offering in (demo, real):
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )
    return demo, real


def describe_display_demo_classes():
    def it_hides_demo_slug_classes_from_public_by_default():
        from classes.models import ClassOffering

        _make_demo_and_real()
        public_slugs = set(ClassOffering.objects.public().values_list("slug", flat=True))
        assert "real-class" in public_slugs
        assert "demo-showcase" not in public_slugs
        # bookable() calls public(), so the gate covers it (and thus the catalog/calendar) too.
        bookable_slugs = set(ClassOffering.objects.bookable().values_list("slug", flat=True))
        assert "demo-showcase" not in bookable_slugs

    def it_shows_demo_slug_classes_when_on():
        from classes.models import ClassOffering

        _make_demo_and_real()
        _set(display_demo_classes=True)
        public_slugs = set(ClassOffering.objects.public().values_list("slug", flat=True))
        assert {"real-class", "demo-showcase"} <= public_slugs

    def it_never_gates_the_base_queryset_so_admins_keep_managing_demo_classes():
        # Admin/teach querysets use .all()/editable_by/for_instructor, not public(),
        # so demo classes stay visible to staff even with the flag off (default).
        from classes.models import ClassOffering

        _make_demo_and_real()
        assert ClassOffering.objects.filter(slug__startswith="demo-").count() == 1


def _example_guild():
    from membership.example_guild import EXAMPLE_GUILD_SLUG
    from membership.models import Guild

    # Seeded inactive, like the real example guild (out of voting/funding).
    return Guild.objects.create(name="Cartographers Guild", slug=EXAMPLE_GUILD_SLUG, is_active=False)


def describe_display_demo_guild():
    def it_hides_the_example_guild_from_directory_and_visible_by_default():
        from membership.models import Guild
        from tests.membership.factories import GuildFactory

        active = GuildFactory(name="Real Guild", is_active=True)
        example = _example_guild()
        directory_slugs = set(Guild.objects.directory().values_list("slug", flat=True))
        assert active.slug in directory_slugs
        assert example.slug not in directory_slugs
        assert example.slug not in set(Guild.objects.visible().values_list("slug", flat=True))

    def it_shows_the_example_guild_when_on():
        from membership.models import Guild

        example = _example_guild()
        _set(display_demo_guild=True)
        assert example.slug in set(Guild.objects.directory().values_list("slug", flat=True))
        assert example.slug in set(Guild.objects.visible().values_list("slug", flat=True))

    def it_keeps_the_example_guild_out_of_active_only_lists_even_when_on():
        # Safety contract: the flag reveals the example guild only on display surfaces,
        # never in the is_active-filtered lists that drive voting and funding.
        from membership.models import Guild

        example = _example_guild()
        _set(display_demo_guild=True)
        active_slugs = set(Guild.objects.filter(is_active=True).values_list("slug", flat=True))
        assert example.slug not in active_slugs
