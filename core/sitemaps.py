"""One ``/sitemap.xml`` per host (#1259), built on Django's sitemaps framework.

Each surface lists only its own indexable pages, on the host the request came in on, so the
classes, tours, guilds and members hosts each hand a search engine their own map:

- classes: the catalog, every Class Type page, each class still to come
  (``ClassOffering.objects.indexable()``) and each active instructor's page;
- tours: the booking page at the root;
- guilds: the directory and every guild it lists;
- members: upcoming published events and the published, listed Help guides.

``lastmod`` is the row's ``updated_at`` where the row has one (classes and events).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

if TYPE_CHECKING:
    from datetime import datetime

    from django.contrib.sites.models import Site
    from django.contrib.sites.requests import RequestSite
    from django.db.models import QuerySet

    from classes.models import Category, ClassOffering
    from membership.models import CommunityEvent, Guild, Member, WikiArticle


class HostSitemap(Sitemap):
    """A sitemap whose URLs sit on the host the request came in on, not the ``Site`` row's domain."""

    def __init__(self, host: str) -> None:
        self.host = host

    def get_domain(self, site: Site | RequestSite | None = None) -> str:
        """The request's host, so each surface's map names its own host."""
        return self.host


class PathsSitemap(HostSitemap):
    """Fixed pages, given as paths."""

    def __init__(self, host: str, paths: list[str]) -> None:
        super().__init__(host)
        self.paths = paths

    def items(self) -> list[str]:
        return self.paths

    def location(self, item: str) -> str:
        return item


class ClassTypeSitemap(HostSitemap):
    """Every Class Type page the catalog lists, demo ones only while demo classes show."""

    def items(self) -> QuerySet[Category]:
        from classes.models import DEMO_SLUG_PREFIX, Category
        from core.models import SiteConfiguration

        qs = Category.objects.order_by("pk")
        if not SiteConfiguration.load().display_demo_classes:
            qs = qs.exclude(slug__startswith=DEMO_SLUG_PREFIX)
        return qs

    def location(self, item: Category) -> str:
        return reverse("classes:public_category", kwargs={"slug": item.slug})


class ClassSitemap(HostSitemap):
    """Every class page still to come: upcoming, undated and open flexible classes.

    Several runs share one class page (#1260), so each address is listed once, by the run
    edited most recently, whose ``updated_at`` is then the page's ``lastmod``.
    """

    def items(self) -> list[ClassOffering]:
        from classes.models import ClassOffering

        runs = ClassOffering.objects.indexable().listed().select_related("class_record").order_by("-updated_at", "-pk")
        by_address: dict[str, ClassOffering] = {}
        for run in runs:
            by_address.setdefault(run.public_path, run)
        return sorted(by_address.values(), key=lambda run: run.pk)

    def location(self, item: ClassOffering) -> str:
        return item.public_path

    def lastmod(self, item: ClassOffering) -> datetime:
        return item.updated_at


class InstructorSitemap(HostSitemap):
    """Every active member with a public instructor page."""

    def items(self) -> QuerySet[Member]:
        from membership.models import Member

        return Member.objects.filter(status=Member.Status.ACTIVE, instructor_slug__gt="").order_by("pk")

    def location(self, item: Member) -> str:
        return reverse("classes:public_instructor", kwargs={"slug": item.instructor_slug})


class GuildSitemap(HostSitemap):
    """Every guild the public directory lists."""

    def items(self) -> QuerySet[Guild]:
        from membership.models import Guild

        return Guild.objects.directory().exclude(slug="").order_by("pk")

    def location(self, item: Guild) -> str:
        return reverse("hub_guild_detail", kwargs={"slug": item.slug})


class EventSitemap(HostSitemap):
    """Every published event that has not ended, a repeating series included."""

    def items(self) -> QuerySet[CommunityEvent]:
        from membership.models import CommunityEvent

        return CommunityEvent.objects.published().upcoming().order_by("pk")

    def location(self, item: CommunityEvent) -> str:
        return reverse("hub_event_detail", kwargs={"pk": item.pk})

    def lastmod(self, item: CommunityEvent) -> datetime:
        return item.updated_at


class HelpSitemap(HostSitemap):
    """Every published Help guide that is listed, while the Help page is on."""

    def items(self) -> QuerySet[WikiArticle]:
        from core.models import SiteConfiguration
        from membership.help_content import UNLISTED_SLUGS
        from membership.models import WikiArticle

        if not SiteConfiguration.load().help_page_enabled:
            return WikiArticle.objects.none()
        return (
            WikiArticle.objects.published().exclude(slug__in=UNLISTED_SLUGS).select_related("category").order_by("pk")
        )

    def location(self, item: WikiArticle) -> str:
        return item.get_absolute_url()


def sitemaps_for(surface: str, host: str) -> dict[str, Any]:
    """The sections of ``surface``'s sitemap, every URL on ``host``.

    Raises:
        KeyError: for a surface with no sitemap (signage never reaches here: its allowlist
            does not include the view).
    """
    sections: dict[str, dict[str, Any]] = {
        "public": {
            "catalog": PathsSitemap(host, [reverse("classes:public_list")]),
            "class_types": ClassTypeSitemap(host),
            "classes": ClassSitemap(host),
            "instructors": InstructorSitemap(host),
        },
        "tours": {"tours": PathsSitemap(host, ["/"])},
        "guilds": {
            "directory": PathsSitemap(host, [reverse("hub_guild_directory")]),
            "guilds": GuildSitemap(host),
        },
        "members": {"events": EventSitemap(host), "help": HelpSitemap(host)},
    }
    return sections[surface]
