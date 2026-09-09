"""BDD specs for the safety gate (spec D §5.6, §6.11, D8, D9, D21).

There is exactly ONE gate in the round and it lives in one place. Safety content IS
Official content, so there is no separate field and no box on any form for a member to
untick — the failure the old two-path arrangement invited was a member creating a Safety
page, opening Edit, unticking the box, and publishing straight past it.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from membership.models import GuildStaffMembership, Member, WikiError, WikiPage
from tests.hub.wiki_mod_helpers import enable_wiki, login, login_lead, member_user
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    WikiPageFactory,
    WikiRevisionFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


def _create(client: Client, kind: str, *, title: str, guild=None) -> object:
    data = {
        "title": title,
        "kind": WikiPage.Kind.GUILD_INFO if kind == "safety" else kind,
        "guild": str(guild.pk) if guild is not None else "",
        "body": "<p>Wear eye protection at all times.</p>",
        "facts-TOTAL_FORMS": "0",
        "facts-INITIAL_FORMS": "0",
        "facts-MIN_NUM_FORMS": "0",
        "facts-MAX_NUM_FORMS": "1000",
        "attachments-TOTAL_FORMS": "0",
        "attachments-INITIAL_FORMS": "0",
        "attachments-MIN_NUM_FORMS": "0",
        "attachments-MAX_NUM_FORMS": "1000",
    }
    return client.post(reverse("hub_wiki_create", args=[kind]), data)


def describe_the_starter_chooser():
    def it_offers_the_safety_card(client: Client):
        login(client, "gate_chooser")
        body = client.get(reverse("hub_wiki_new")).content
        assert b"Safety and rules" in body
        assert b"A guild lead reads these before they go live." in body


def describe_a_member_proposing_a_safety_page():
    def it_lands_an_unpublished_official_page(client: Client):
        user = login(client, "gate_member")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=user.member)
        response = _create(client, "safety", title="Bandsaw Rules", guild=guild)
        assert response.status_code == 200
        page = WikiPage.objects.get(title="Bandsaw Rules")
        assert page.status == WikiPage.Status.OFFICIAL
        assert page.is_published is False
        assert page.is_held_proposal is True

    def it_explains_what_happens_next_on_a_full_page(client: Client):
        user = login(client, "gate_member2")
        guild = GuildFactory(name="Woodworking")
        GuildMembershipFactory(guild=guild, member=user.member)
        response = _create(client, "safety", title="Woodshop Rules", guild=guild)
        assert b"Safety pages get a second read before they go live." in response.content
        assert b"The Woodworking leads have it, and you will hear back." in response.content
        assert b"View My Draft" in response.content

    def it_is_invisible_to_other_members(client: Client):
        author = login(client, "gate_author")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=author.member)
        _create(client, "safety", title="Hidden Rules", guild=guild)
        page = WikiPage.objects.get(title="Hidden Rules")
        client.logout()
        login(client, "gate_stranger")
        assert client.get(page.get_absolute_url()).status_code == 404

    def it_appears_in_its_own_scope_queue_and_no_other(client: Client):
        author = login(client, "gate_author2")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=author.member)
        _create(client, "safety", title="Scoped Rules", guild=guild)
        client.logout()
        _lead_user, _guild = login_lead(client, "gate_lead", guild)
        assert b"Scoped Rules" in client.get(reverse("hub_wiki_review")).content
        client.logout()
        login_lead(client, "gate_other_lead")
        assert b"Scoped Rules" not in client.get(reverse("hub_wiki_review")).content

    def it_publishes_official_for_effective_staff(client: Client):
        login(client, "gate_admin_create", fog_role=Member.FogRole.ADMIN)
        response = _create(client, "safety", title="Admin Written Rules")
        assert response.status_code == 302
        page = WikiPage.objects.get(title="Admin Written Rules")
        assert page.is_published is True
        assert page.status == WikiPage.Status.OFFICIAL

    def describe_a_guild_moderator_who_is_not_an_officer():
        def it_publishes_guild_verified_and_never_official(client: Client):
            # can_moderate_wiki_scope is true for ANY GuildStaffMembership row, so deciding
            # published-or-not on it alone let an orienter, secretary or treasurer create a
            # page wearing the Official chip. Brief §4 and §5.2 lock Official to admins and
            # officers, so the create path lands exactly where the queue's Publish does.
            user = login(client, "gate_orienter")
            guild = GuildFactory()
            # A guild's orienter is a member of it; the New-page scope picker offers the
            # guilds you have JOINED, so the join is what puts it on the form.
            GuildMembershipFactory(guild=guild, member=user.member)
            GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
            response = _create(client, "safety", title="Orienter Written Rules", guild=guild)
            assert response.status_code == 302
            page = WikiPage.objects.get(title="Orienter Written Rules")
            assert page.is_published is True
            assert page.status == WikiPage.Status.GUILD_VERIFIED
            assert page.verified_by == user.member

        def it_leaves_the_page_editable_by_its_own_author(client: Client):
            # The secondary bug the old arrangement had: an Official page answers
            # can_edit_wiki_page with is_effective_staff, so the orienter who wrote it was
            # locked out of it the instant it was created.
            user = login(client, "gate_orienter2")
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=user.member)
            GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.TREASURER)
            _create(client, "safety", title="Treasurer Written Rules", guild=guild)
            page = WikiPage.objects.get(title="Treasurer Written Rules")
            assert client.get(reverse("hub_wiki_edit", args=[page.slug])).status_code == 200

        def it_still_lands_official_when_they_are_also_an_officer(client: Client):
            user = login(client, "gate_officer", fog_role=Member.FogRole.GUILD_OFFICER)
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=user.member)
            GuildStaffMembershipFactory(guild=guild, member=user.member, role=GuildStaffMembership.Role.ORIENTER)
            _create(client, "safety", title="Officer Written Rules", guild=guild)
            assert WikiPage.objects.get(title="Officer Written Rules").status == WikiPage.Status.OFFICIAL

    def it_tells_the_scopes_moderators_that_a_proposal_is_waiting(client: Client):
        # The held screen promises "the leads have it, and you will hear back", so somebody
        # has to actually be told.
        from core.models import Notification

        lead = member_user("gate_notify_lead", email="gate_notify_lead@example.com")
        guild = GuildFactory(guild_lead=lead.member)
        user = login(client, "gate_notify_author")
        GuildMembershipFactory(guild=guild, member=user.member)
        _create(client, "safety", title="Notified Rules", guild=guild)
        assert Notification.objects.filter(trigger="wiki.page_proposed", user=lead).exists()

    def it_tells_nobody_when_the_page_publishes_straight_through(client: Client):
        from core.models import Notification

        login(client, "gate_no_notify", fog_role=Member.FogRole.ADMIN)
        _create(client, "safety", title="Straight Through Rules")
        assert not Notification.objects.filter(trigger="wiki.page_proposed").exists()

    def it_writes_one_activity_row_for_one_act(client: Client):
        # Creating a Safety page as a moderator is created-then-published in two calls, but
        # it is ONE act: "Felix edited Bandsaw Rules" a second after "Felix created Bandsaw
        # Rules", for a page nobody edited, is a permanent falsehood in the audit trail.
        from core.models import SiteActivity

        login(client, "gate_activity", fog_role=Member.FogRole.ADMIN)
        _create(client, "safety", title="Activity Rules")
        kinds = list(SiteActivity.objects.order_by("id").values_list("kind", flat=True))
        assert kinds == [SiteActivity.Kind.WIKI_PAGE_CREATED]

    def it_matches_what_an_ordinary_starter_writes(client: Client):
        from core.models import SiteActivity

        login(client, "gate_activity2", fog_role=Member.FogRole.ADMIN)
        _create(client, "howto", title="Activity HowTo")
        assert list(SiteActivity.objects.values_list("kind", flat=True)) == [SiteActivity.Kind.WIKI_PAGE_CREATED]

    def it_still_logs_the_edit_when_a_held_page_is_published_later(client: Client):
        # The suppression is scoped to the create path. Publishing from the queue days
        # later IS a second act and keeps its row.
        from core.models import SiteActivity

        _user, guild = login_lead(client, "gate_activity3")
        page = WikiPageFactory(guild=guild, official=True, is_published=False)
        SiteActivity.objects.all().delete()
        client.post(reverse("hub_wiki_publish_proposal", args=[page.slug]))
        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.WIKI_PAGE_EDITED)
        assert activity.payload["published_proposal"] is True

    def describe_when_the_second_half_of_the_create_fails():
        def it_rolls_the_whole_page_back_so_a_retry_works(client: Client, monkeypatch):
            # Four write groups with no ATOMIC_REQUESTS: without the transaction the page
            # was committed unpublished, the author saw a 500 instead of the held screen,
            # nobody was told, and the retry met "a page called that already exists".
            import membership.models as models_module

            def boom(self, **kwargs):
                raise RuntimeError("the notification backend fell over")

            monkeypatch.setattr(models_module.WikiPage, "notify_scope_of_proposal", boom)
            user = login(client, "gate_atomic")
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=user.member)
            with pytest.raises(RuntimeError):
                _create(client, "safety", title="Rolled Back Rules", guild=guild)
            assert not WikiPage.objects.filter(title="Rolled Back Rules").exists()

        def it_rolls_back_a_failed_publish_too(client: Client, monkeypatch):
            import membership.models as models_module

            def boom(self, **kwargs):
                raise RuntimeError("emit fell over")

            monkeypatch.setattr(models_module.WikiPage, "publish_proposal", boom)
            login(client, "gate_atomic2", fog_role=Member.FogRole.ADMIN)
            with pytest.raises(RuntimeError):
                _create(client, "safety", title="Rolled Back Admin Rules")
            assert not WikiPage.objects.filter(title="Rolled Back Admin Rules").exists()

    def it_names_the_admins_rather_than_a_scope_chip_on_a_space_wide_proposal(client: Client):
        login(client, "gate_space_wide")
        response = _create(client, "safety", title="Space Wide Rules")
        assert b"The makerspace admins have it, and you will hear back." in response.content
        assert b"The Space-wide leads have it" not in response.content

    def it_leaves_every_other_starter_publishing_live(client: Client):
        login(client, "gate_normal")
        response = _create(client, "howto", title="Sharpening A Chisel")
        assert response.status_code == 302
        page = WikiPage.objects.get(title="Sharpening A Chisel")
        assert page.is_published is True
        assert page.status == WikiPage.Status.COMMUNITY


def describe_publishing_a_proposal():
    def it_lands_guild_verified_for_a_lead(client: Client):
        user, guild = login_lead(client, "pub_lead")
        page = WikiPageFactory(guild=guild, official=True, is_published=False)
        response = client.post(reverse("hub_wiki_publish_proposal", args=[page.slug]))
        assert response.status_code == 200
        page.refresh_from_db()
        assert page.is_published is True
        assert page.status == WikiPage.Status.GUILD_VERIFIED
        assert page.verified_by == user.member

    def it_tells_the_pages_writers_that_it_was_verified(client: Client):
        # The brief calls this the round's retention mechanism, and this is the one path
        # where a member's own proposal becomes verified — it must not be the silent one
        # while declining emails them.
        from core.models import Notification

        author = member_user("pub_verified_author", email="pub_verified_author@example.com").member
        _user, guild = login_lead(client, "pub_verified_lead")
        page = WikiPageFactory(guild=guild, official=True, is_published=False, created_by=author)
        WikiRevisionFactory(page=page, author=author)
        client.post(reverse("hub_wiki_publish_proposal", args=[page.slug]))
        assert Notification.objects.filter(trigger="wiki.page_verified", user=author.user).exists()

    def it_stays_silent_when_an_admin_lands_it_official(client: Client):
        # Official is not a verification; nobody is told they were verified.
        from core.models import Notification

        author = member_user("pub_official_author", email="pub_official_author@example.com").member
        login(client, "pub_official_admin", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None, official=True, is_published=False, created_by=author)
        WikiRevisionFactory(page=page, author=author)
        client.post(reverse("hub_wiki_publish_proposal", args=[page.slug]))
        assert not Notification.objects.filter(trigger="wiki.page_verified").exists()

    def it_lands_official_for_an_admin(client: Client):
        login(client, "pub_admin", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None, official=True, is_published=False)
        client.post(reverse("hub_wiki_publish_proposal", args=[page.slug]))
        page.refresh_from_db()
        assert page.is_published is True
        assert page.status == WikiPage.Status.OFFICIAL

    def it_says_which_outcome_before_the_click(client: Client):
        _user, guild = login_lead(client, "pub_copy_lead")
        WikiPageFactory(guild=guild, official=True, is_published=False, title="Lead Copy Page")
        assert b"marks this Guild Verified" in client.get(reverse("hub_wiki_review")).content
        client.logout()
        login(client, "pub_copy_admin", fog_role=Member.FogRole.ADMIN)
        assert b"publishes as an Official page" in client.get(reverse("hub_wiki_review")).content

    def it_refuses_a_page_that_is_already_live(db):
        with pytest.raises(WikiError):
            WikiPageFactory(official=True).publish_proposal(by=MemberFactory(), as_official=True)

    def it_refuses_a_plain_member(client: Client):
        login(client, "pub_403")
        page = WikiPageFactory(guild=GuildFactory(), official=True, is_published=False)
        assert client.post(reverse("hub_wiki_publish_proposal", args=[page.slug])).status_code == 403


def describe_declining_a_proposal():
    def it_keeps_the_draft_and_records_the_note_on_the_history(client: Client):
        user, guild = login_lead(client, "dec_lead")
        page = WikiPageFactory(guild=guild, official=True, is_published=False)
        response = client.post(
            reverse("hub_wiki_decline_proposal", args=[page.slug]),
            {"note": "Add the dust-mask requirement and the arbor size."},
        )
        assert response.status_code == 200
        page.refresh_from_db()
        assert page.is_published is False
        revision = page.revisions.filter(author=user.member).first()
        assert revision is not None
        assert revision.note.startswith("Sent back: Add the dust-mask requirement")

    def it_emails_the_author_with_the_note_and_the_draft_link(client: Client):
        author = member_user("dec_author", email="dec_author@example.com").member
        _user, guild = login_lead(client, "dec_lead2")
        page = WikiPageFactory(guild=guild, official=True, is_published=False, created_by=author)
        client.post(
            reverse("hub_wiki_decline_proposal", args=[page.slug]),
            {"note": "Add the dust-mask requirement and the arbor size."},
        )
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["dec_author@example.com"]
        html = message.alternatives[0][0]
        for body in (message.body, html):
            assert "Add the dust-mask requirement and the arbor size." in body
            assert reverse("hub_wiki_edit", args=[page.slug]) in body

    def it_deletes_nothing(client: Client):
        _user, guild = login_lead(client, "dec_nothing")
        page = WikiPageFactory(guild=guild, official=True, is_published=False)
        before = page.revisions.count()
        client.post(
            reverse("hub_wiki_decline_proposal", args=[page.slug]),
            {"note": "Please add the required gear section."},
        )
        assert page.revisions.count() == before + 1
        assert WikiPage.objects.filter(pk=page.pk).exists()

    def it_rejects_a_note_under_ten_characters(client: Client):
        _user, guild = login_lead(client, "dec_short")
        page = WikiPageFactory(guild=guild, official=True, is_published=False)
        response = client.post(reverse("hub_wiki_decline_proposal", args=[page.slug]), {"note": "no"})
        assert response.status_code == 200
        assert mail.outbox == []
        assert b"Say what should change" in response.content

    def it_sends_nothing_when_the_page_has_no_author(client: Client):
        _user, guild = login_lead(client, "dec_no_author")
        page = WikiPageFactory(guild=guild, official=True, is_published=False, seeded=True)
        client.post(
            reverse("hub_wiki_decline_proposal", args=[page.slug]),
            {"note": "Please add the required gear section."},
        )
        assert mail.outbox == []

    def it_refuses_a_page_that_is_already_live(db):
        with pytest.raises(WikiError):
            WikiPageFactory(official=True).decline_proposal(by=MemberFactory(), note="Too late for this.")


def describe_the_authors_carve_out():
    def it_lets_the_author_edit_their_own_unpublished_proposal(client: Client):
        user = login(client, "carve_author")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=user.member)
        _create(client, "safety", title="Carve Out Rules", guild=guild)
        page = WikiPage.objects.get(title="Carve Out Rules")
        response = client.get(reverse("hub_wiki_edit", args=[page.slug]))
        assert response.status_code == 200
        saved = client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {
                "title": page.title,
                "body": "<p>Now with the dust mask requirement.</p>",
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
        assert saved.status_code == 302
        page.refresh_from_db()
        assert page.body == "<p>Now with the dust mask requirement.</p>"
        assert page.is_published is False

    def it_refuses_a_different_non_staff_member(client: Client):
        author = login(client, "carve_author2")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=author.member)
        _create(client, "safety", title="Someone Elses Rules", guild=guild)
        page = WikiPage.objects.get(title="Someone Elses Rules")
        client.logout()
        login(client, "carve_stranger")
        assert client.get(reverse("hub_wiki_edit", args=[page.slug])).status_code == 403

    def it_resumes_the_ordinary_official_rule_once_published(client: Client):
        author = login(client, "carve_author3")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=author.member)
        _create(client, "safety", title="Published Rules", guild=guild)
        page = WikiPage.objects.get(title="Published Rules")
        page.publish_proposal(by=MemberFactory(), as_official=True)
        assert client.get(reverse("hub_wiki_edit", args=[page.slug])).status_code == 403
        assert b"Edit" not in client.get(page.get_absolute_url()).content.split(b"pl-wp-actions")[-1][:400]


def describe_the_absence_of_a_second_path():
    def it_gives_the_edit_form_no_status_or_safety_control(client: Client):
        # A's create-form Safety toggle is struck, and apply_edit never writes
        # is_published — so there is nothing to untick and no way to publish past the gate.
        login(client, "gate_no_toggle")
        page = WikiPageFactory()
        body = client.get(reverse("hub_wiki_edit", args=[page.slug])).content
        assert b'name="status"' not in body
        assert b'name="is_published"' not in body
        assert b"This page tells someone how to stay safe" not in body

    def it_does_not_publish_a_held_page_through_an_ordinary_save(client: Client):
        user = login(client, "gate_no_publish")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=user.member)
        _create(client, "safety", title="Still Held Rules", guild=guild)
        page = WikiPage.objects.get(title="Still Held Rules")
        client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {
                "title": page.title,
                "body": "<p>Trying to sneak it live.</p>",
                "is_published": "on",
                "status": WikiPage.Status.COMMUNITY,
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
        page.refresh_from_db()
        assert page.is_published is False
        assert page.status == WikiPage.Status.OFFICIAL


def describe_the_drafts_page():
    def it_does_not_offer_a_draft_whose_page_the_member_can_no_longer_open(client: Client):
        # Once a proposal is published Official the author loses edit rights, and a stale
        # draft would otherwise list the title here and 403 on "Keep Writing".
        from membership.models import WikiDraft

        user = login(client, "drafts_stale")
        page = WikiPageFactory(guild=GuildFactory(), official=True)
        WikiDraft.objects.create(page=page, author=user.member, kind=page.kind, title="Half typed")
        body = client.get(reverse("hub_wiki_drafts")).content
        assert page.title.encode() not in body
        assert b"Half typed" not in body

    def it_still_offers_a_draft_on_a_page_they_can_open(client: Client):
        from membership.models import WikiDraft

        user = login(client, "drafts_live")
        page = WikiPageFactory()
        WikiDraft.objects.create(page=page, author=user.member, kind=page.kind, title="Half typed live")
        assert b"Half typed live" in client.get(reverse("hub_wiki_drafts")).content

    def it_labels_a_safety_starter_draft_readably(db):
        from membership.models import WikiDraft

        draft = WikiDraft.objects.create(author=MemberFactory(), kind="safety", title="Rules draft")
        assert draft.kind_label == "Safety and rules"
