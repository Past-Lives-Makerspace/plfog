"""Make the Leadership Directory's first two tabs from its old section wording (#564).

Forward: a People tab titled and introduced from ``LeadershipPage.team_heading`` and
``team_intro``, then the Guild Leads tab from ``guilds_heading`` and ``guilds_intro``, in that
order, and every existing listing (listed or not) onto the People tab. A site that never
saved the page has no ``LeadershipPage`` row, so the wording falls back to the field defaults,
which is exactly what that site was showing.

Reverse: the first People tab's wording and the Guild Leads tab's go back onto the page,
each member keeps one listing (the release before tabs allows only one, and 0189's reverse
puts that uniqueness back), and the tabs go. The kept listing is the member's first listed
one in tab order, else their first in tab order; the others, and their role lines, are
deleted, because the old single roster has no place for a second card.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

_WORDING = ("team_heading", "team_intro", "guilds_heading", "guilds_intro")


def _page_wording(page_model: Any) -> dict[str, str]:
    """The page's four section strings, or the field defaults when the page row was never made."""
    page = page_model.objects.filter(pk=1).first()
    if page is None:
        return {name: page_model._meta.get_field(name).default for name in _WORDING}
    return {name: getattr(page, name) for name in _WORDING}


def make_tabs(apps: Any, schema_editor: Any) -> None:
    page_model = apps.get_model("membership", "LeadershipPage")
    tab_model = apps.get_model("membership", "LeadershipTab")
    listing_model = apps.get_model("membership", "LeadershipListing")
    wording = _page_wording(page_model)
    people = tab_model.objects.create(
        title=wording["team_heading"], intro=wording["team_intro"], kind="people", sort_order=0
    )
    tab_model.objects.create(
        title=wording["guilds_heading"], intro=wording["guilds_intro"], kind="guild_leads", sort_order=1
    )
    listing_model.objects.filter(tab__isnull=True).update(tab=people)


def fold_tabs(apps: Any, schema_editor: Any) -> None:
    page_model = apps.get_model("membership", "LeadershipPage")
    tab_model = apps.get_model("membership", "LeadershipTab")
    listing_model = apps.get_model("membership", "LeadershipListing")
    page, _created = page_model.objects.get_or_create(pk=1)
    people = tab_model.objects.filter(kind="people").order_by("sort_order", "id").first()
    if people is not None:
        page.team_heading, page.team_intro = people.title, people.intro
    guild_leads = tab_model.objects.filter(kind="guild_leads").first()
    if guild_leads is not None:
        page.guilds_heading, page.guilds_intro = guild_leads.title, guild_leads.intro
    page.save()

    kept: set[int] = set()
    extra: list[int] = []
    in_keep_order = listing_model.objects.order_by(
        "member_id", "-is_listed", "tab__sort_order", "tab_id", "sort_order", "id"
    ).values_list("pk", "member_id")
    for pk, member_id in in_keep_order:
        if member_id in kept:
            extra.append(pk)
        else:
            kept.add(member_id)
    listing_model.objects.filter(pk__in=extra).delete()
    # Detach before deleting the tabs: the historical tab FK still cascades.
    listing_model.objects.update(tab=None)
    tab_model.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("membership", "0189_leadership_tabs"),
    ]

    operations = [
        migrations.RunPython(make_tabs, fold_tabs),
    ]
