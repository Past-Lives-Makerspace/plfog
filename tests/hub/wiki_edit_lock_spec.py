"""BDD specs for the soft advisory edit lock (spec D §5.5, D5, D6).

Advisory only: it never blocks a save. It exists so the second person sees "Dana Kim
started editing this 3 minutes ago" BEFORE they spend twenty minutes on text that will
collide.

Expiry is asserted by writing ``refreshed_at`` with ``queryset.update`` — the field is
``auto_now``, so a plain save would bump it right back — and never by sleeping.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import WikiEditLock
from tests.hub.wiki_mod_helpers import enable_wiki, login
from tests.membership.factories import MemberFactory, WikiPageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


def _age(page, *, hours: int) -> None:
    """Push both clocks back. ``update`` bypasses ``auto_now`` on ``refreshed_at``."""
    stale = timezone.now() - timezone.timedelta(hours=hours)
    WikiEditLock.objects.filter(page=page).update(started_at=stale, refreshed_at=stale)


def describe_claiming():
    def it_takes_the_lock_when_nobody_holds_it(db):
        page = WikiPageFactory()
        member = MemberFactory()
        assert WikiEditLock.claim(page, member) is None
        assert WikiEditLock.objects.get(page=page).holder == member

    def it_returns_the_other_persons_live_lock_to_warn_about(db):
        page = WikiPageFactory()
        dana = MemberFactory(full_legal_name="Dana Kim")
        WikiEditLock.claim(page, dana)
        warning = WikiEditLock.claim(page, MemberFactory())
        assert warning is not None
        assert warning.holder == dana

    def it_leaves_that_lock_alone_so_a_third_person_sees_the_same_name(db):
        page = WikiPageFactory()
        dana = MemberFactory(full_legal_name="Dana Kim")
        WikiEditLock.claim(page, dana)
        WikiEditLock.claim(page, MemberFactory())
        third = WikiEditLock.claim(page, MemberFactory())
        assert third is not None
        assert third.holder == dana

    def it_never_warns_the_holder_about_themselves(db):
        page = WikiPageFactory()
        member = MemberFactory()
        WikiEditLock.claim(page, member)
        assert WikiEditLock.claim(page, member) is None

    def describe_a_stale_lock():
        def it_is_claimed_by_the_next_opener_with_no_warning(db):
            page = WikiPageFactory()
            WikiEditLock.claim(page, MemberFactory())
            _age(page, hours=3)
            sam = MemberFactory(full_legal_name="Sam Ruiz")
            assert WikiEditLock.claim(page, sam) is None
            assert WikiEditLock.objects.get(page=page).holder == sam

        def it_restarts_started_at_for_the_new_holder(db):
            # THE auto_now_add bug this test exists for: a write-once started_at would keep
            # the previous holder's timestamp under the new holder's name, and the warning
            # would read "Sam started editing this three hours ago" about somebody who
            # opened the editor a minute ago.
            page = WikiPageFactory()
            WikiEditLock.claim(page, MemberFactory(full_legal_name="Dana Kim"))
            _age(page, hours=3)
            sam = MemberFactory(full_legal_name="Sam Ruiz")
            WikiEditLock.claim(page, sam)
            warning = WikiEditLock.claim(page, MemberFactory())
            assert warning is not None
            assert warning.holder == sam
            assert warning.started_at > timezone.now() - timezone.timedelta(minutes=1)


def describe_refreshing():
    def it_bumps_the_holders_own_row(db):
        page = WikiPageFactory()
        member = MemberFactory()
        WikiEditLock.claim(page, member)
        _age(page, hours=3)
        WikiEditLock.refresh(page, member)
        assert WikiEditLock.objects.get(page=page).is_live is True

    def it_is_a_no_op_for_somebody_who_does_not_hold_it(db):
        page = WikiPageFactory()
        WikiEditLock.claim(page, MemberFactory())
        _age(page, hours=3)
        WikiEditLock.refresh(page, MemberFactory())
        assert WikiEditLock.objects.get(page=page).is_live is False

    def it_is_a_no_op_when_there_is_no_lock_at_all(db):
        WikiEditLock.refresh(WikiPageFactory(), MemberFactory())
        assert not WikiEditLock.objects.exists()


def describe_the_editor():
    def it_claims_the_lock_on_open(client: Client):
        user = login(client, "lock_open")
        page = WikiPageFactory()
        client.get(reverse("hub_wiki_edit", args=[page.slug]))
        assert WikiEditLock.objects.get(page=page).holder == user.member

    def it_warns_the_second_person_by_name(client: Client):
        page = WikiPageFactory()
        WikiEditLock.claim(page, MemberFactory(full_legal_name="Dana Kim"))
        login(client, "lock_second")
        body = client.get(reverse("hub_wiki_edit", args=[page.slug])).content
        assert b"Dana Kim started editing this" in body
        assert b"nothing is lost" in body

    def it_shows_the_holder_no_warning(client: Client):
        user = login(client, "lock_holder")
        page = WikiPageFactory()
        WikiEditLock.claim(page, user.member)
        body = client.get(reverse("hub_wiki_edit", args=[page.slug])).content
        assert b"started editing this" not in body

    def it_never_blocks_the_second_person_from_saving(client: Client):
        page = WikiPageFactory()
        WikiEditLock.claim(page, MemberFactory(full_legal_name="Dana Kim"))
        login(client, "lock_not_a_gate")
        response = client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {
                "title": page.title,
                "body": "<p>Saved anyway.</p>",
                "base_revision": "",
                "facts-TOTAL_FORMS": "0",
                "facts-INITIAL_FORMS": "0",
                "facts-MIN_NUM_FORMS": "0",
                "facts-MAX_NUM_FORMS": "1000",
                "attachments-TOTAL_FORMS": "0",
                "attachments-INITIAL_FORMS": "0",
                "attachments-MIN_NUM_FORMS": "0",
                "attachments-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        page.refresh_from_db()
        assert page.body == "<p>Saved anyway.</p>"

    def it_releases_the_lock_on_a_successful_save(client: Client):
        user = login(client, "lock_release")
        page = WikiPageFactory()
        WikiEditLock.claim(page, user.member)
        client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {
                "title": page.title,
                "body": "<p>Done editing.</p>",
                "base_revision": "",
                "facts-TOTAL_FORMS": "0",
                "facts-INITIAL_FORMS": "0",
                "facts-MIN_NUM_FORMS": "0",
                "facts-MAX_NUM_FORMS": "1000",
                "attachments-TOTAL_FORMS": "0",
                "attachments-INITIAL_FORMS": "0",
                "attachments-MIN_NUM_FORMS": "0",
                "attachments-MAX_NUM_FORMS": "1000",
            },
        )
        assert not WikiEditLock.objects.filter(page=page).exists()

    def it_is_kept_warm_by_the_autosave_post(client: Client):
        # No heartbeat endpoint, no polling timer, no JS: spec A's autosave is the only
        # thing keeping this alive.
        user = login(client, "lock_autosave")
        page = WikiPageFactory()
        WikiEditLock.claim(page, user.member)
        _age(page, hours=3)
        client.post(reverse("hub_wiki_autosave", args=[page.slug]), {"field": "title", "value": "Still typing"})
        assert WikiEditLock.objects.get(page=page).is_live is True


def describe_the_row_itself():
    def it_names_the_holder_and_the_page(db):
        page = WikiPageFactory()
        member = MemberFactory(full_legal_name="Dana Kim")
        WikiEditLock.claim(page, member)
        assert str(WikiEditLock.objects.get(page=page)) == f"Dana Kim editing {page.slug}"

    def it_is_dropped_with_its_page(db):
        page = WikiPageFactory()
        WikiEditLock.claim(page, MemberFactory())
        page.delete()
        assert not WikiEditLock.objects.exists()
