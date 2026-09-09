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

from membership.models import Member, WikiError, WikiPage, WikiRevision
from tests.hub.wiki_mod_helpers import enable_wiki, login, login_lead, member_user
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory, WikiPageFactory

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

    def it_publishes_immediately_for_a_moderator(client: Client):
        login(client, "gate_admin_create", fog_role=Member.FogRole.ADMIN)
        response = _create(client, "safety", title="Admin Written Rules")
        assert response.status_code == 302
        page = WikiPage.objects.get(title="Admin Written Rules")
        assert page.is_published is True
        assert page.status == WikiPage.Status.OFFICIAL

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
        assert WikiRevision is not None
