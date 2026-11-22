"""BDD specs for the Host a Class page, its tutorial and Become an Instructor (#1295).

Becoming an instructor is self serve, like Become a Tour Guide (#1278): the page keeps its pitch
and ends in a three step tutorial written from the Instructor Guide, whose last step's Become an
Instructor turns teaching on for an active member and lands on the Teaching Overview with a toast.
The application, its pending and declined states, the admin approve and decline and Instructor
Inquiries are gone, and their addresses 404. These also cover the branch that sends a member who
CAN teach to their dashboard instead of the page, and the admin-edited copy: every section renders
its default, a blanked field hides its section, and nothing an admin types reaches the page
unescaped or unsanitized.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, UserFactory
from classes.models import ClassOffering, ClassSettings
from classes.views import ALREADY_INSTRUCTOR_MESSAGE, BECAME_INSTRUCTOR_MESSAGE, HOST_A_CLASS_TUTORIAL
from core.models import Notification, SiteActivity
from membership.models import AdminCapability, Member, OrgInfoPage, WikiArticle

HERO_TITLE = "Share What You Love"
START_BUTTON = "Start the Tutorial</button>"
CONFIRM_BUTTON = "Become an Instructor</button>"
APPROVED_BANNER = "You Can Host Classes"
# Template-literal copy is NOT autoescaped (only variables are), so these match verbatim.
PLACEHOLDER = "The guide has not been loaded yet."
BECOME = reverse("classes:teach_become_instructor")
OVERVIEW = reverse("classes:teach_overview")
WHY = reverse("classes:teach_why")
STEP_TITLES = ("Your Instructor Page", "Creating a Class", "How Review Goes")
# The heading itself: the release notes on every hub page also say "Where the Money Goes".
MONEY_TITLE = '<h2 class="pl-teach-section__title">Where the Money Goes</h2>'
FAQ_ITEM = '<details class="pl-disclosure pl-teach-faq__item"'
GUIDE_ITEM = '<details class="pl-disclosure pl-teach-guide">'
#: Every address the application and Instructor Inquiries answered, now gone (#1295).
REMOVED_PATHS = (
    "/classes/teach/apply/",
    "/classes/admin/instructor-inquiries/",
    "/classes/admin/instructor-inquiries/export/",
    "/classes/admin/instructor-inquiries/add/",
    "/classes/admin/teaching-applications/1/approve/",
    "/classes/admin/teaching-applications/1/decline/",
)
REMOVED_NAMES = (
    "teach_apply",
    "admin_instructor_inquiries",
    "admin_instructor_inquiries_export",
    "admin_instructor_inquiry_add",
    "admin_teaching_approve",
    "admin_teaching_decline",
)

# One marker per admin-edited field: text that is on the page only while that field
# is filled. Used to prove each blanked field hides exactly its own section.
SECTION_MARKERS = {
    "teach_page_title": HERO_TITLE,
    "teach_page_lead": "Run a class for the people already in the shop.",
    "teach_page_features": "What You Get",
    "teach_page_how_it_works": "How It Works",
    "teach_page_expectations": "What We Ask Of You",
    "teach_page_faq": "Common Questions",
    "teach_page_cta_title": "Got Something to Share?",
    "teach_page_cta_line": "Three short steps on how hosting works here.",
}


def _active_member_user(username: str) -> tuple[object, Member]:
    user = UserFactory(username=username)
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return user, member


def _signed_in(username: str) -> tuple[Client, Member]:
    user, member = _active_member_user(username)
    client = Client()
    client.force_login(user)
    return client, member


def _granted_emails() -> list:
    return [email for email in mail.outbox if email.subject.startswith("You have been granted")]


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
    def it_renders_the_pitch_and_a_three_step_tutorial_for_a_member_who_cannot_teach(db, client):
        _seed_orientation_article()
        user, _ = _active_member_user("never-applied@example.com")
        client.force_login(user)
        response = client.get(WHY)
        assert response.status_code == 200
        content = response.content.decode()
        assert HERO_TITLE in content
        assert "What we expect from instructors" in content
        assert re.findall(r'data-tutorial-step="(\d)"', content) == ["1", "2", "3"]
        for title in STEP_TITLES:
            assert f'<h3 class="pl-tutorial-step__title">{title}</h3>' in content
        assert content.count(START_BUTTON) == 2  # the hero's and the bottom card's
        assert content.count("data-teach-start") == 2
        assert APPROVED_BANNER not in content

    def it_writes_its_steps_from_the_instructor_guide():
        # Prod WikiPage 25 (guide_for=instructor): Your Instructor Page, Creating a Class, How Review Goes.
        assert tuple(step.title for step in HOST_A_CLASS_TUTORIAL) == STEP_TITLES
        points = " ".join(point for step in HOST_A_CLASS_TUTORIAL for point in step.points)
        assert "Instructor Profile tab" in points
        assert "+ New Class on the Overview tab" in points
        assert "Submit for Review" in points
        assert " - " not in points and "\u2014" not in points and "\u2013" not in points

    def it_puts_become_an_instructor_on_the_last_step_alone(db, client):
        user, _ = _active_member_user("last-step@example.com")
        client.force_login(user)
        content = client.get(WHY).content.decode()
        assert content.count(CONFIRM_BUTTON) == 1
        assert re.search(
            rf'action="{BECOME}" class="pl-tutorial__confirm" x-show="step === 3" data-tutorial-confirm', content
        )
        assert 'data-guidance-target="classes.become-instructor">Become an Instructor</button>' in content
        assert content.index("data-tutorial-back") < content.index("data-tutorial-confirm")
        assert "Every class you make is reviewed before it goes live." in content
        assert "data-tutorial-holder" not in content
        # Release notes on every hub page still quote the old buttons, so look for the controls themselves.
        for gone in ("/classes/teach/apply/", "data-teach-apply", "Application Sent", "Waiting to Hear Back"):
            assert gone not in content

    def it_gives_an_instructor_the_portal_in_place_of_the_tutorial(db, client):
        user, member = _active_member_user("approved-page@example.com")
        member.grant_teaching(granted_by=None)
        client.force_login(user)
        content = client.get(WHY).content.decode()
        assert APPROVED_BANNER in content
        assert "Go to the Teaching Portal" in content
        assert "Create a Class" in content
        assert "You Are Already In" in content
        assert "data-tutorial" not in content
        assert f'action="{BECOME}"' not in content
        assert START_BUTTON not in content

    def it_dates_the_banner_from_when_teaching_was_turned_on(db, client):
        user, member = _active_member_user("grandfathered-page@example.com")
        member.instructor_oriented_at = timezone.make_aware(timezone.datetime(2026, 3, 4, 12))
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        content = client.get(WHY).content.decode()
        assert APPROVED_BANNER in content
        assert "Since March 4, 2026." in content

    def it_403s_an_inactive_member(db, client):
        user = UserFactory(username="inactive-why@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.INVITED
        member.save(update_fields=["status"])
        client.force_login(user)
        assert client.get(WHY).status_code == 403

    def it_fails_soft_when_the_help_center_seed_is_missing(db, client):
        user, _ = _active_member_user("noseed-why@example.com")
        client.force_login(user)
        content = client.get(WHY).content.decode()
        assert PLACEHOLDER in content
        assert "Read the Hosting Guide" in content
        assert CONFIRM_BUTTON in content  # the tutorial is never blocked by a missing seed

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
            assert '<span class="pl-disclosure__title">Do I Need to Be an Expert?</span>' in content
            assert "Show up on time and leave the space the way you found it." in content
            assert "<strong>Build your page.</strong>" in content
            assert 'class="pl-md pl-teach-steps-md"' in content
            assert 'class="pl-md pl-teach-asks-md"' in content
            # Common Questions is an accordion of the shared disclosure, split on the
            # headings server side, the first one open so the section never looks empty.
            assert content.count(FAQ_ITEM) == 4
            assert content.count(FAQ_ITEM + " open>") == 1
            assert content.index(FAQ_ITEM + " open>") == content.index(FAQ_ITEM)
            assert content.count('<span class="pl-teach-faq__badge" aria-hidden="true">Q</span>') == 4
            assert 'class="pl-md pl-teach-faq-md"' not in content
            assert "pl-teach-faq__intro" not in content
            # The guide sits on the same disclosure, with its hint and chevron.
            assert GUIDE_ITEM in content
            assert "Read the Hosting Guide" in content
            assert "The full walkthrough, from your first draft to the day of the class. Tap to open." in content
            assert content.count('<span class="pl-disclosure__chevron" aria-hidden="true"></span>') == 5
            # Section order: steps, money, asks, questions, guide.
            assert (
                content.index("How It Works")
                < content.index(MONEY_TITLE)
                < content.index("What We Ask Of You")
                < content.index("Common Questions")
                < content.index(GUIDE_ITEM)
            )

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
            assert '<span class="pl-disclosure__title">Is It Free?</span>' in content
            assert "<p>Yes.</p>" in content
            assert content.count(FAQ_ITEM) == 1

        def it_renders_an_older_markdown_value_in_a_prose_field(db, client):
            """Dual mode: a value that does not start with < still renders as Markdown."""
            settings_obj = ClassSettings.load()
            settings_obj.teach_page_how_it_works = "1. **Ask.** Say hi.\n2. **Build.** Make it."
            settings_obj.teach_page_expectations = "- Be kind.\n- Be safe."
            settings_obj.save()
            user, _ = _active_member_user("markdown-page@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "<strong>Ask.</strong>" in content
            assert "<li>Be safe.</li>" in content

        def it_renders_the_whole_field_when_the_questions_have_no_headings(db, client):
            settings_obj = ClassSettings.load()
            settings_obj.teach_page_faq = "<p>Ask an admin anything.</p>"
            settings_obj.save()
            user, _ = _active_member_user("noheadings-page@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert "Common Questions" in content
            assert FAQ_ITEM not in content
            assert '<div class="pl-md pl-teach-faq-md"><p>Ask an admin anything.</p></div>' in content

        def it_renders_an_intro_above_the_first_question(db, client):
            settings_obj = ClassSettings.load()
            settings_obj.teach_page_faq = "<p>Ask away.</p><h3>Q1</h3><p>A1</p>"
            settings_obj.save()
            user, _ = _active_member_user("intro-page@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert '<div class="pl-md pl-teach-faq__intro"><p>Ask away.</p></div>' in content
            assert content.index("pl-teach-faq__intro") < content.index(FAQ_ITEM)

        def it_escapes_a_question_exactly_once(db, client):
            settings_obj = ClassSettings.load()
            settings_obj.teach_page_faq = "<h3>Tools &amp; Safety</h3><p>A</p>"
            settings_obj.save()
            user, _ = _active_member_user("amp-page@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert '<span class="pl-disclosure__title">Tools &amp; Safety</span>' in content
            assert "&amp;amp;" not in content

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
            assert START_BUTTON in content  # the buttons are structural and never hide

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
            assert '<span class="pl-disclosure__title">Q</span>' in content

    def describe_the_money_section():
        def it_renders_the_default_split_as_a_bar_and_three_tiles(db, client):
            user, _ = _active_member_user("money-default@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert MONEY_TITLE in content
            assert 'data-help-key="teach.money-split"' in content
            assert "Every class splits the same way." in content
            assert 'aria-label="70 percent to you, 20 percent to Past Lives, 10 percent to the guild"' in content
            assert 'class="pl-teach-split__seg pl-teach-split__seg--you" style="flex-basis: 70%">70%</span>' in content
            assert (
                'class="pl-teach-split__seg pl-teach-split__seg--space" style="flex-basis: 20%">20%</span>' in content
            )
            assert (
                'class="pl-teach-split__seg pl-teach-split__seg--guild" style="flex-basis: 10%">10%</span>' in content
            )
            assert "pl-teach-split__seg--slim" not in content
            assert content.count('class="pl-teach-split__tile ') == 3
            assert "Your share of every seat sold." in content
            assert "Keeps the shop open and the tools running." in content
            assert "Funds the guild whose space and tools you use." in content

        def it_takes_the_numbers_from_the_settings_and_slims_a_narrow_segment(db, client):
            settings_obj = ClassSettings.load()
            settings_obj.teach_page_split_instructor_pct = 80
            settings_obj.teach_page_split_space_pct = 15
            settings_obj.teach_page_split_guild_pct = 5
            settings_obj.teach_page_split_note = ""
            settings_obj.save()
            user, _ = _active_member_user("money-custom@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert 'aria-label="80 percent to you, 15 percent to Past Lives, 5 percent to the guild"' in content
            assert 'pl-teach-split__seg--guild pl-teach-split__seg--slim" style="flex-basis: 5%">5%</span>' in content
            assert 'pl-teach-split__seg--you" style="flex-basis: 80%">80%</span>' in content
            assert "pl-teach-split__note" not in content

        def it_hides_the_section_when_disabled_and_keeps_the_rest(db, client):
            settings_obj = ClassSettings.load()
            settings_obj.teach_page_split_enabled = False
            settings_obj.save()
            user, _ = _active_member_user("money-off@example.com")
            client.force_login(user)
            content = client.get(reverse("classes:teach_why")).content.decode()
            assert MONEY_TITLE not in content
            assert 'class="hub-card pl-teach-split"' not in content
            assert "percent to Past Lives" not in content
            for marker in SECTION_MARKERS.values():
                assert marker in content

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

        def it_builds_the_context_and_renders_the_card_in_a_fixed_number_of_queries(db, django_assert_num_queries):
            """The example is fetched with its category, its instructors and sessions in the same
            lookup, so the card partial reads them from the cache instead of querying per
            field. The count covers ``_why_teach_context`` (two guide lookups, the settings
            row, the example plus its sessions and its instructor rows (#686); the contact
            prefill moved to the application page with #793) and the card render (one seat count).
            """
            from django.template.loader import render_to_string

            from classes.factories import ClassSessionFactory
            from classes.views import _why_teach_context

            example = _published_example()
            ClassSessionFactory(class_offering=example)
            _, member = _active_member_user("query-count@example.com")
            with django_assert_num_queries(7):
                context = _why_teach_context(member)
                html = render_to_string("classes/public/_class_card.html", {"group": context["example_group"]})
            assert example.title in html

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
        member.status = Member.Status.INVITED
        member.save(update_fields=["status"])
        client.force_login(user)
        assert client.get(reverse("classes:teach_overview")).status_code == 403


def describe_teach_become_instructor():
    def it_turns_teaching_on_and_lands_on_the_teaching_overview_with_a_toast(db, django_capture_on_commit_callbacks):
        client, member = _signed_in("become-view@example.com")
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(BECOME)
        assert response.status_code == 302
        assert response["Location"] == OVERVIEW
        landed = client.get(OVERVIEW)
        assert [str(message) for message in landed.context["messages"]] == [BECAME_INSTRUCTOR_MESSAGE]
        content = landed.content.decode()
        assert HERO_TITLE not in content  # the dashboard, not the page
        assert 'data-permission-setup="instructor"' in content  # Get Set Up to Teach
        member.refresh_from_db()
        assert member.can_create_classes
        assert client.get(reverse("classes:teach_class_create")).status_code == 200

    def it_grants_once_and_says_so_on_a_second_press(db, django_capture_on_commit_callbacks):
        client, member = _signed_in("become-twice@example.com")
        with django_capture_on_commit_callbacks(execute=True):
            client.post(BECOME)
        client.get(OVERVIEW)  # the first toast is read
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(BECOME)
        assert response["Location"] == OVERVIEW
        assert [str(message) for message in client.get(OVERVIEW).context["messages"]] == [ALREADY_INSTRUCTOR_MESSAGE]
        assert ALREADY_INSTRUCTOR_MESSAGE == "You can already host classes."
        assert len(_granted_emails()) == 1
        assert (
            SiteActivity.objects.filter(kind=SiteActivity.Kind.INSTRUCTOR_SELF_SERVE, target_id=member.pk).count() == 1
        )

    def it_logs_the_activity_row_and_rings_no_admin_bell(db, django_capture_on_commit_callbacks):
        admin_client, admin = _signed_in("become-watcher@example.com")
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        admin.sync_user_permissions()
        AdminCapability.objects.create(member=admin, capability=AdminCapability.Capability.CLASS_APPROVER)
        client, member = _signed_in("become-logged@example.com")
        with django_capture_on_commit_callbacks(execute=True):
            client.post(BECOME)
        [row] = SiteActivity.objects.filter(kind=SiteActivity.Kind.INSTRUCTOR_SELF_SERVE)
        assert (row.actor, row.target) == (member.user, member)
        feed = admin_client.get(reverse("manage_activity")).content.decode()
        assert '<div class="hub-member-name">Made themselves an Instructor</div>' in feed
        assert not Notification.objects.filter(user=admin.user).exists()
        assert [email.to for email in mail.outbox] == [[member.primary_email]]

    @pytest.mark.parametrize("status", [Member.Status.GUEST, Member.Status.FORMER])
    def it_refuses_a_guest_and_a_lapsed_member(db, status, django_capture_on_commit_callbacks):
        client, member = _signed_in(f"become-{status}@example.com")
        member.status = status
        member.save(update_fields=["status"])
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(BECOME)
        # Locked out before the view (MemberLockoutMiddleware); active_member_required's 403 backs it.
        assert response.status_code in (302, 403)
        assert response.status_code == 403 or response["Location"].startswith("/accounts/locked/")
        member.refresh_from_db()
        assert not member.can_create_classes
        assert _granted_emails() == []

    def it_refuses_an_invited_member_with_a_403(db):
        client, member = _signed_in("become-invited@example.com")
        member.status = Member.Status.INVITED
        member.save(update_fields=["status"])
        assert client.post(BECOME).status_code == 403
        member.refresh_from_db()
        assert not member.can_create_classes

    def it_refuses_an_account_with_no_member_record(db):
        user = User.objects.create_user("unlinked-becomer", "unlinked-becomer@example.com", "pw")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)
        assert client.post(BECOME).status_code == 403

    def it_answers_only_a_post(db):
        client, _ = _signed_in("become-get@example.com")
        assert client.get(BECOME).status_code == 405

    def it_sends_an_anonymous_visitor_to_login(db, client):
        response = client.post(BECOME)
        assert response.status_code == 302
        assert "/login" in response["Location"] or "/accounts/" in response["Location"]

    def it_lets_a_member_whose_teaching_was_turned_off_turn_it_on_again(db):
        admin = _active_member_user("become-revoker@example.com")[1]
        client, member = _signed_in("become-again@example.com")
        client.post(BECOME)
        member.refresh_from_db()
        member.revoke_instructor(revoked_by=admin)
        assert client.get(reverse("classes:teach_class_create"))["Location"] == OVERVIEW
        assert CONFIRM_BUTTON in client.get(WHY).content.decode()
        assert client.post(BECOME)["Location"] == OVERVIEW
        member.refresh_from_db()
        assert member.can_create_classes


def describe_the_removed_application():
    @pytest.mark.parametrize("path", REMOVED_PATHS)
    def it_404s_every_old_address_even_for_an_admin(db, path):
        client, admin = _signed_in(f"removed-{REMOVED_PATHS.index(path)}@example.com")
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        admin.sync_user_permissions()
        assert client.get(path).status_code == 404
        assert client.post(path, {"reason": "x"}).status_code == 404

    @pytest.mark.parametrize("name", REMOVED_NAMES)
    def it_has_no_url_name_left(name):
        with pytest.raises(NoReverseMatch):
            reverse(f"classes:{name}", kwargs={"pk": 1} if "teaching" in name else None)

    def it_is_linked_from_no_template():
        templates = Path(__file__).resolve().parents[3] / "templates"
        needles = ("teach/apply", "instructor-inquiries", "teaching-applications", "teach_apply", "instructor_inquir")
        found = [
            f"{path.relative_to(templates)}: {needle}"
            for path in templates.rglob("*.html")
            for needle in needles
            if needle in path.read_text()
        ]
        assert found == []

    def it_leaves_no_interested_in_teaching_queue_on_the_admin_overview(db):
        client, admin = _signed_in("overview-admin@example.com")
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        admin.sync_user_permissions()
        applicant = _active_member_user("old-applicant@example.com")[1]
        applicant.teaching_applied_at = timezone.now()  # a stamp the retired application left
        applicant.save(update_fields=["teaching_applied_at"])
        content = client.get(reverse("classes:admin_overview")).content.decode()
        assert "Interested in Teaching" not in content
        assert "teaching-applications" not in content


def describe_retired_orientation_route():
    def it_permanently_redirects_to_the_marketing_page(db, client):
        user, _ = _active_member_user("old-orientation@example.com")
        client.force_login(user)
        response = client.get(reverse("classes:teach_orientation"))
        assert response.status_code == 301
        assert response["Location"] == reverse("classes:teach_why")
