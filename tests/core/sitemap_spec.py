"""One /sitemap.xml per host (#1259): each surface lists only its own indexable pages, on its own host."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import ClassOffering
from core.models import SiteConfiguration
from tests.membership.factories import CommunityEventFactory, GuildFactory, WikiArticleFactory

pytestmark = pytest.mark.django_db

CLASSES = "classes.pastlives.space"
MEMBERS = "members.pastlives.space"
TOURS = "tours.pastlives.space"
GUILDS = "guilds.pastlives.space"

HOSTS = dict(
    ALLOWED_HOSTS=[CLASSES, MEMBERS, TOURS, GUILDS, "testserver"],
    PUBLIC_HOSTS=[CLASSES],
    TOURS_HOSTS=[TOURS],
    GUILDS_HOSTS=[GUILDS],
    MEMBER_HOST=MEMBERS,
)


@pytest.fixture(autouse=True)
def _hosts(settings) -> None:
    for name, value in HOSTS.items():
        setattr(settings, name, value)


def _sitemap(client: Client, host: str) -> str:
    response = client.get("/sitemap.xml", HTTP_HOST=host, secure=True)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/xml")
    return response.content.decode()


def _class(slug: str, *, days: int, **kwargs) -> ClassOffering:
    kwargs.setdefault("status", ClassOffering.Status.PUBLISHED)
    # Titled after the slug so the class's address is the slug the spec expects (#1260).
    kwargs.setdefault("title", slug.replace("-", " ").title())
    offering = ClassOfferingFactory(slug=slug, **kwargs)
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_the_classes_host():
    def it_lists_the_catalog_class_types_upcoming_classes_and_instructors(client: Client):
        instructor = InstructorFactory(instructor_slug="ada-lovelace")
        _class("forge-basics", days=5, category=CategoryFactory(slug="metal"), instructor=instructor)
        ClassOfferingFactory(
            title="Flex Open",
            slug="flex-open",
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
        )

        body = _sitemap(client, CLASSES)

        assert f"<loc>https://{CLASSES}/classes/</loc>" in body
        assert f"<loc>https://{CLASSES}/classes/category/metal/</loc>" in body
        assert f"<loc>https://{CLASSES}/classes/forge-basics/</loc>" in body
        assert f"<loc>https://{CLASSES}/classes/flex-open/</loc>" in body
        assert f"<loc>https://{CLASSES}/classes/instructors/ada-lovelace/</loc>" in body

    def it_dates_a_class_by_its_last_update(client: Client):
        offering = _class("forge-basics", days=5)

        body = _sitemap(client, CLASSES)

        assert f"<lastmod>{timezone.localtime(offering.updated_at).date().isoformat()}</lastmod>" in body

    def it_lists_a_class_with_several_dates_once_at_its_own_address(client: Client):
        first = _class("forge-basics-2026-12-01", days=5, title="Forge Basics")
        _class("forge-basics-2027-01-05", days=40, title="Forge Basics", category=first.category)

        body = _sitemap(client, CLASSES)

        assert body.count(f"<loc>https://{CLASSES}/classes/forge-basics/</loc>") == 1
        assert "/classes/forge-basics-2026-12-01/" not in body

    def it_dates_a_class_by_its_public_runs_not_a_draft_of_it(client: Client):
        from datetime import datetime

        offering = _class("forge-basics", days=5, title="Forge Basics")
        draft = _class("forge-basics-draft", days=40, title="Forge Basics", status=ClassOffering.Status.DRAFT)
        ClassOffering.objects.filter(pk=offering.pk).update(updated_at=timezone.make_aware(datetime(2026, 1, 2)))
        ClassOffering.objects.filter(pk=draft.pk).update(updated_at=timezone.make_aware(datetime(2026, 3, 4)))

        body = _sitemap(client, CLASSES)

        assert "<lastmod>2026-01-02</lastmod>" in body
        assert "2026-03-04" not in body

    def it_drops_a_demo_class_once_demo_classes_are_hidden_without_waiting_for_the_nightly_job(client: Client):
        config = SiteConfiguration.load()
        config.display_demo_classes = True
        config.save()
        _class("demo-forge", days=5, title="Demo Forge")
        assert "/classes/demo-forge/" in _sitemap(client, CLASSES)

        config.display_demo_classes = False
        config.save()

        assert "/classes/demo-forge/" not in _sitemap(client, CLASSES)

    def it_lists_a_class_between_runs(client: Client):
        _class("lately", days=-30)

        assert f"<loc>https://{CLASSES}/classes/lately/</loc>" in _sitemap(client, CLASSES)

    def it_leaves_out_retired_no_longer_offered_draft_private_and_cancelled_classes(client: Client):
        _class("long-gone", days=-400)
        _class("set-aside", days=-30).class_record.mark_no_longer_offered(None)
        _class("still-a-draft", days=5, status=ClassOffering.Status.DRAFT)
        _class("by-invite", days=5, is_private=True)
        _class("called-off", days=5, status=ClassOffering.Status.CANCELLED)

        body = _sitemap(client, CLASSES)

        for slug in ("long-gone", "set-aside", "still-a-draft", "by-invite", "called-off"):
            assert f"/classes/{slug}/" not in body

    def it_leaves_out_demo_class_types_while_demo_classes_are_hidden(client: Client):
        CategoryFactory(slug="demo-pottery")

        assert "/classes/category/demo-pottery/" not in _sitemap(client, CLASSES)

    def it_lists_demo_class_types_while_demo_classes_show(client: Client):
        CategoryFactory(slug="demo-pottery")
        config = SiteConfiguration.load()
        config.display_demo_classes = True
        config.save()

        assert "/classes/category/demo-pottery/" in _sitemap(client, CLASSES)

    def it_leaves_out_a_former_instructor(client: Client):
        InstructorFactory(instructor_slug="moved-away", status="former")

        assert "/instructors/moved-away/" not in _sitemap(client, CLASSES)

    def it_lists_nothing_from_the_other_hosts(client: Client):
        CommunityEventFactory(community=True)

        assert "/events/" not in _sitemap(client, CLASSES)


def describe_the_tours_host():
    def it_lists_the_booking_page_at_the_root(client: Client):
        body = _sitemap(client, TOURS)

        assert f"<loc>https://{TOURS}/</loc>" in body
        assert "/tour/" not in body


def describe_the_guilds_host():
    def it_lists_the_directory_and_each_listed_guild(client: Client):
        GuildFactory(name="Wood Guild", slug="wood")
        GuildFactory(name="Gone Guild", slug="gone", is_active=False)

        body = _sitemap(client, GUILDS)

        assert f"<loc>https://{GUILDS}/guilds/</loc>" in body
        assert f"<loc>https://{GUILDS}/guilds/wood/</loc>" in body
        assert "/guilds/gone/" not in body


def describe_the_members_host():
    def it_lists_upcoming_published_events_and_published_help_guides(client: Client):
        upcoming = CommunityEventFactory(community=True)
        past = CommunityEventFactory(
            community=True, starts_at=timezone.now() - timedelta(days=9), ends_at=timezone.now() - timedelta(days=8)
        )
        pending = CommunityEventFactory(community=True, pending=True)
        guide = WikiArticleFactory(slug="book-a-tool")
        WikiArticleFactory(slug="half-written", is_published=False)
        WikiArticleFactory(slug="instructor-orientation")
        config = SiteConfiguration.load()
        config.help_page_enabled = True
        config.save()

        body = _sitemap(client, MEMBERS)

        assert f"<loc>https://{MEMBERS}/events/{upcoming.pk}/</loc>" in body
        assert f"<lastmod>{timezone.localtime(upcoming.updated_at).date().isoformat()}</lastmod>" in body
        assert f"/events/{past.pk}/" not in body
        assert f"/events/{pending.pk}/" not in body
        assert f"<loc>https://{MEMBERS}{guide.get_absolute_url()}</loc>" in body
        assert "half-written" not in body
        assert "instructor-orientation" not in body

    def it_lists_no_help_guides_while_the_help_page_is_off(client: Client):
        WikiArticleFactory(slug="book-a-tool")
        config = SiteConfiguration.load()
        config.help_page_enabled = False
        config.save()

        assert "book-a-tool" not in _sitemap(client, MEMBERS)
