"""BDD specs for the Host a Workshop page and the I'm Interested POST (spec §A, retoned).

Teaching stopped being self-service: a member says they are interested, an admin says
yes. These cover the page in all four states, the POST's guards, the branch that sends
a member who CAN teach to their dashboard instead of the marketing page, and the
admin-edited copy: every section renders its default, a blanked field hides its section,
and nothing an admin types reaches the page unescaped or unsanitized.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, UserFactory
from classes.models import ClassOffering, ClassSettings
from core.models import SiteActivity
from membership.models import Member, OrgInfoPage, WikiArticle

HERO_TITLE = "Share What You Love"
APPLY_BUTTON = "I'm Interested"
APPLY_AGAIN_BUTTON = "I'm Still Interested"
SEND_BUTTON = "Send It"
PENDING_BANNER = "Thanks, We Got Your Note"
DECLINED_BANNER = "Not Right Now"
APPROVED_BANNER = "You Can Host Workshops"
# Template-literal copy is NOT autoescaped (only variables are), so these match verbatim.
PLACEHOLDER = "The guide has not been loaded yet."
BLANK_NOTE_ERROR = "Tell us a little about what you want to host."
OPEN_MODAL = "$dispatch('open-modal', 'apply-to-teach')"

# One marker per admin-edited field: text that is on the page only while that field
# is filled. Used to prove each blanked field hides exactly its own section.
SECTION_MARKERS = {
    "teach_page_title": HERO_TITLE,
    "teach_page_lead": "Run a workshop or a class for the people already in the shop.",
    "teach_page_features": "What You Get",
    "teach_page_how_it_works": "How It Works",
    "teach_page_expectations": "What We Ask Of You",
    "teach_page_faq": "Common Questions",
    "teach_page_cta_title": "Got Something to Share?",
    "teach_page_cta_line": "Tell us what you have in mind and an admin will take it from there.",
}


def _active_member_user(username: str) -> tuple[object, Member]:
    user = UserFactory(username=username)
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return user, member


def _seed_orientation_article() -> WikiArticle:
    return WikiArticle.objects.create(
        page=OrgInfoPage.load(),
        slug="instructor-orientation",
        title="Instructor orientation",
        body="## What we expect from instructors {#what-we-expect}\n\nShow up prepared.",
    )


def _published_example() -> ClassOffering:
    offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
    settings_obj = ClassSettings.load()
    settings_obj.example_class = offering
    settings_obj.save()
    return offering


def describe_teach_why():
    def it_renders_the_hero_and_the_apply_button_for_a_member_who_never_applied(db, client):
        _seed_orientation_article()
        user, _ = _active_member_user("never-applied@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:teach_why"))
        assert response.status_code == 200
        content = response.content.decode()
        assert HERO_TITLE in content
        assert APPLY_BUTTON in content
        assert "What we expect from instructors" in content
        assert PENDING_BANNER not in content
        assert DECLINED_BANNER not in content

    def it_renders_the_pending_banner_and_no_apply_button_while_waiting(db, client):
        user, member = _active_member_user("pending-page@example.com")
        member.apply_to_teach("I would like to run a two hour intro to wheel throwing.")
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert PENDING_BANNER in content
        assert "Note Sent" in content
        assert "Waiting to Hear Back" in content
        # The modal (and therefore its submit button) is not rendered while pending.
        assert SEND_BUTTON not in content
        assert OPEN_MODAL not in content

    def it_renders_the_decline_reason_and_an_apply_again_button(db, client):
        user, member = _active_member_user("declined-page@example.com")
        member.apply_to_teach("Wheel throwing.")
        member.decline_teaching(decided_by=None, reason="Finish the wheel orientation first.")
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert DECLINED_BANNER in content
        assert "Finish the wheel orientation first." in content
        assert APPLY_AGAIN_BUTTON in content
        assert "You are welcome to say you're interested again whenever you like." in content

    def it_renders_the_approved_banner_for_an_instructor(db, client):
        user, member = _active_member_user("approved-page@example.com")
        member.grant_teaching(granted_by=None)
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert APPROVED_BANNER in content
        assert "Go to the Teaching Portal" in content
        assert "Create a Workshop" in content
        assert "You Are Already In" in content
        assert APPLY_BUTTON not in content
        assert OPEN_MODAL not in content

    def it_reads_approved_for_a_grandfathered_instructor_who_never_applied(db, client):
        user, member = _active_member_user("grandfathered-page@example.com")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert APPROVED_BANNER in content

    def it_403s_an_inactive_member(db, client):
        user = UserFactory(username="inactive-why@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(user)
        assert client.get(reverse("classes:teach_why")).status_code == 403

    def it_fails_soft_when_the_help_center_seed_is_missing(db, client):
        user, _ = _active_member_user("noseed-why@example.com")
        client.force_login(user)
        content = client.get(reverse("classes:teach_why")).content.decode()
        assert PLACEHOLDER in content
        assert "Read the Hosting Guide" in content
        assert APPLY_BUTTON in content  # the interest path is never blocked by a missing seed

    def describe_the_admin_edited_copy():
        def it_renders_every_default_section(db, client):
            user, _ = _active_member_user("defaults-page@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            for marker in SECTION_MARKERS.values():
                assert marker in content
            assert content.count('class="hub-card pl-feature-card"') == 6
            assert "A Page Worth Sharing" in content
            assert "Run It Again In One Click" in content
            assert "<h3>Do I Need to Be an Expert?</h3>" in content
            assert "Show up on time and leave the space the way you found it." in content
            assert "<strong>Build your page.</strong>" in content
            assert 'class="pl-md pl-teach-steps-md"' in content
            assert 'class="pl-md pl-teach-faq-md"' in content
            # The accordions are gone: questions are open headings now. The only
            # disclosure left on the page is the guide's own.
            faq_block = content.split('class="pl-md pl-teach-faq-md"')[1].split('<details class="pl-teach-guide">')[0]
            assert "<details" not in faq_block
            assert "<summary" not in faq_block

        def it_renders_what_an_admin_typed(db, client):
            settings_obj = ClassSettings.load()
            settings_obj.teach_page_title = "Teach Us Something"
            settings_obj.teach_page_features = "Only One Card: With one line."
            settings_obj.teach_page_faq = "### Is It Free?\n\nYes."
            settings_obj.save()
            user, _ = _active_member_user("typed-page@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "Teach Us Something" in content
            assert HERO_TITLE not in content
            assert content.count('class="hub-card pl-feature-card"') == 1
            assert "Only One Card" in content
            assert "<h3>Is It Free?</h3>" in content

        @pytest.mark.parametrize("field", sorted(SECTION_MARKERS))
        def it_hides_a_blanked_section_and_keeps_the_rest(db, client, field):
            settings_obj = ClassSettings.load()
            setattr(settings_obj, field, "")
            settings_obj.save()
            user, _ = _active_member_user(f"blank-{field}@example.com")
            client.force_login(user)
            response = client.get(reverse("classes:teach_why"))
            assert response.status_code == 200
            content = response.content.decode()
            assert SECTION_MARKERS[field] not in content
            for other, marker in SECTION_MARKERS.items():
                if other != field:
                    assert marker in content
            assert APPLY_BUTTON in content  # the buttons are structural and never hide

        def it_escapes_a_plain_text_field_an_admin_typed(db, client):
            settings_obj = ClassSettings.load()
            settings_obj.teach_page_title = "<b>Bold</b> & Co"
            settings_obj.teach_page_cta_line = "<i>Line</i>"
            settings_obj.save()
            user, _ = _active_member_user("escaped-page@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "&lt;b&gt;Bold&lt;/b&gt; &amp; Co" in content
            assert "<b>Bold</b>" not in content
            assert "&lt;i&gt;Line&lt;/i&gt;" in content

        def it_strips_a_script_from_a_markdown_field_before_it_reaches_the_page(db, client):
            settings_obj = ClassSettings.load()
            settings_obj.teach_page_faq = '### Q\n\n<script>alert("faq")</script><p style="color:red">A</p>'
            settings_obj.save()
            user, _ = _active_member_user("script-page@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert '<script>alert("faq")</script>' not in content
            assert 'alert("faq")' in content
            assert 'style="color:red"' not in content
            assert "<h3>Q</h3>" in content

    def describe_the_example_class():
        def it_renders_the_real_catalog_card_when_one_is_configured(db, client):
            example = _published_example()
            user, _ = _active_member_user("example-set@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "pl-teach-showcase__grid" in content
            assert "This is a live catalog card, not a picture of one." in content
            assert "Open the Example Page" in content
            assert example.title in content
            assert example.public_url in content

        def it_falls_back_to_a_catalog_link_when_none_is_configured(db, client):
            user, _ = _active_member_user("example-none@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "Have a Look at the Catalog" in content
            assert "pl-teach-showcase__grid" not in content

        def it_falls_back_when_the_configured_example_is_not_published(db, client):
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            settings_obj = ClassSettings.load()
            settings_obj.example_class = offering
            settings_obj.save()
            user, _ = _active_member_user("example-draft@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "Have a Look at the Catalog" in content
            assert "pl-teach-showcase__grid" not in content


def describe_teach_overview_branching():
    def it_shows_the_marketing_page_to_a_member_who_cannot_teach(db, client):
        user, _ = _active_member_user("locked-overview@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:teach_overview"))
        assert response.status_code == 200
        assert HERO_TITLE in response.content.decode()

    def it_shows_the_dashboard_to_a_member_who_can_teach(db, client):
        user, member = _active_member_user("unlocked-overview@example.com")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        content = client.get(reverse("classes:teach_overview")).content.decode()
        assert HERO_TITLE not in content

    def it_403s_an_inactive_member(db, client):
        user = UserFactory(username="inactive-overview@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(user)
        assert client.get(reverse("classes:teach_overview")).status_code == 403


def describe_teach_apply():
    def it_files_the_application_and_redirects_to_the_portal(db, client):
        user, member = _active_member_user("apply-ok@example.com")
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "Intro to wheel throwing, two hours."})
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_overview")
        member.refresh_from_db()
        assert member.teaching_applied_at is not None
        assert member.teaching_application_note == "Intro to wheel throwing, two hours."
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_APPLIED).count() == 1

    def it_refuses_an_instructor_and_says_the_portal_is_already_open(db, client):
        from django.contrib.messages import get_messages

        user, member = _active_member_user("apply-instructor@example.com")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "Let me apply again."})
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_why")
        member.refresh_from_db()
        assert member.teaching_applied_at is None
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.TEACHING_APPLIED).exists()
        assert [m.message for m in get_messages(response.wsgi_request)] == [
            "You can already host workshops. The teaching portal is open."
        ]

    def it_rerenders_with_the_field_error_on_a_blank_note(db, client):
        user, member = _active_member_user("apply-blank@example.com")
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "   "})
        assert response.status_code == 200
        assert BLANK_NOTE_ERROR in response.content.decode()
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_reopens_the_modal_on_an_invalid_submit(db, client):
        user, _ = _active_member_user("apply-reopen@example.com")
        client.force_login(user)
        content = client.post(reverse("classes:teach_apply"), {"note": ""}).content.decode()
        assert OPEN_MODAL in content

    def it_thanks_the_member_on_the_page_it_lands_on(db, client):
        user, _ = _active_member_user("apply-thanks@example.com")
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "Wheel throwing."}, follow=True)
        content = response.content.decode()
        assert "Thanks. An admin will get back to you." in content
        assert PENDING_BANNER in content

    def it_says_it_already_has_the_note_on_a_duplicate(db, client):
        user, member = _active_member_user("apply-dup-message@example.com")
        member.apply_to_teach("First ask.")
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "Second ask."}, follow=True)
        assert "We already have your note." in response.content.decode()

    def it_refuses_a_second_application_while_one_is_pending(db, client):
        user, member = _active_member_user("apply-twice@example.com")
        member.apply_to_teach("First ask.")
        first_stamp = Member.objects.get(pk=member.pk).teaching_applied_at
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "Second ask."})
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_why")
        member.refresh_from_db()
        assert member.teaching_applied_at == first_stamp
        assert member.teaching_application_note == "First ask."

    def it_403s_an_inactive_member_posting_directly(db, client):
        user = UserFactory(username="apply-inactive@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(user)
        response = client.post(reverse("classes:teach_apply"), {"note": "Let me in."})
        assert response.status_code == 403
        member.refresh_from_db()
        assert member.teaching_applied_at is None

    def it_405s_a_get(db, client):
        user, _ = _active_member_user("apply-get@example.com")
        client.force_login(user)
        assert client.get(reverse("classes:teach_apply")).status_code == 405

    def it_redirects_an_anonymous_visitor_to_login(db, client):
        response = client.post(reverse("classes:teach_apply"), {"note": "Let me in."})
        assert response.status_code == 302
        assert "login" in response["Location"]


def describe_retired_orientation_route():
    def it_permanently_redirects_to_the_marketing_page(db, client):
        user, _ = _active_member_user("old-orientation@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:teach_orientation"))
        assert response.status_code == 301
        assert response["Location"] == reverse("classes:teach_why")
