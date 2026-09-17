"""BDD specs for the ONE per-class management screen.

``classes/spec/access_spec.py`` pins the capability matrix itself, leg by leg. These specs
pin what the SCREEN does with it: which tabs it draws, which actions it draws, which shell it
sits in, and — the half that matters most — that every endpoint behind it refuses the
populations the screen refuses to offer it to.

Every refusal here is a 404 rather than a 403, deliberately: ``templates/404.html`` extends
``hub/base.html`` and so carries the "Viewing as" switcher an admin previewing a lower role
needs to get back out, and a 404 declines to confirm the class exists at all.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassImageFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassOffering, Registration
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

Status = ClassOffering.Status


@pytest.fixture
def instructor(db) -> Member:
    """A member who has been granted teaching access and has a public instructor page."""
    MembershipPlanFactory()
    user = UserFactory(username="screen-teacher@example.com")
    member = InstructorFactory(user=user, full_legal_name="Ren Alvarez", instructor_slug="ren-alvarez")
    member.instructor_oriented_at = timezone.now()
    member.save(update_fields=["instructor_oriented_at"])
    return member


@pytest.fixture
def guild_lead(db) -> Member:
    """A member who leads a guild but teaches nothing."""
    MembershipPlanFactory()
    user = UserFactory(username="screen-lead@example.com")
    member = Member.objects.get(user=user)
    member.full_legal_name = "Wren Halloway"
    member.save(update_fields=["full_legal_name"])
    return member


@pytest.fixture
def reviewer(db) -> Member:
    """A CMS Administrator: the CLASS_APPROVER capability and nothing else."""
    MembershipPlanFactory()
    user = UserFactory(username="screen-reviewer@example.com")
    member = Member.objects.get(user=user)
    member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
    return member


@pytest.fixture
def guild_class(instructor, guild_lead) -> ClassOffering:
    """An upcoming published class the instructor teaches, inside the guild lead's guild."""
    guild = GuildFactory(name="Metalsmithing", guild_lead=guild_lead)
    offering = ClassOfferingFactory(
        title="Intro to Lost Wax Casting",
        slug="lost-wax",
        instructor=instructor,
        category=CategoryFactory(name="Metalsmithing", guild=guild),
        status=Status.PUBLISHED,
        price_cents=8500,
    )
    starts = timezone.now() + timezone.timedelta(days=14)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timezone.timedelta(hours=4))
    return offering


def _html(client, name, offering) -> str:
    response = client.get(reverse(name, kwargs={"pk": offering.pk}))
    assert response.status_code == 200, f"{name} answered {response.status_code}"
    return response.content.decode()


def describe_the_per_class_tab_strip():
    def it_draws_only_the_tabs_this_viewer_may_use(admin_user, guild_class, client):
        client.force_login(admin_user)
        html = _html(client, "classes:teach_class_detail", guild_class)
        for name in (
            "classes:teach_class_registrations",
            "classes:teach_class_waitlist",
            "classes:teach_class_discount_codes",
            "classes:teach_class_emails",
        ):
            assert reverse(name, kwargs={"pk": guild_class.pk}) in html

    def it_marks_the_open_tab_with_aria_current_and_no_tablist_role(admin_user, guild_class, client):
        # Plain anchors to other pages are links, not ARIA tabs: the role would promise
        # arrow-key navigation the page does not implement.
        client.force_login(admin_user)
        html = _html(client, "classes:teach_class_registrations", guild_class)
        strip = html[html.index('aria-label="This class"') :]
        strip = strip[: strip.index("</nav>")]
        assert 'aria-current="page"' in strip
        assert 'role="tablist"' not in strip

    def it_shows_the_two_counts_the_strip_has_always_shown(admin_user, guild_class, client):
        RegistrationFactory(class_offering=guild_class, status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=guild_class, status=Registration.Status.WAITLISTED)
        client.force_login(admin_user)
        html = _html(client, "classes:teach_class_detail", guild_class)
        assert "Registrations (1)" in html
        assert "Waitlist (1)" in html

    def it_draws_no_strip_at_all_for_a_viewer_with_one_tab(guild_lead, guild_class, client):
        # Decision D2: a one-item strip offers no choice. The guild lead has Emails alone.
        client.force_login(guild_lead.user)
        html = _html(client, "classes:teach_class_emails", guild_class)
        assert 'aria-label="This class"' not in html


def describe_the_class_header():
    def it_names_the_class_and_its_facts(admin_user, guild_class, client):
        client.force_login(admin_user)
        html = _html(client, "classes:teach_class_detail", guild_class)
        assert "Intro to Lost Wax Casting" in html
        assert "Ren Alvarez" in html
        assert "$85" in html

    def it_puts_edit_in_the_header_as_the_primary_action(admin_user, guild_class, client):
        # Decision D5: Edit moved out of the action row and took the gold.
        client.force_login(admin_user)
        html = _html(client, "classes:teach_class_detail", guild_class)
        edit = reverse("classes:teach_class_edit", kwargs={"pk": guild_class.pk})
        assert f'class="hub-btn hub-btn--sm hub-btn--primary" href="{edit}"' in html

    def it_keeps_the_public_profile_link_on_every_teaching_shell_page(instructor, guild_class, client):
        # Only the teaching shell reads `instructor`, and Django renders a missing variable
        # as empty, so a page that forgot to set it would lose this link in silence.
        client.force_login(instructor.user)
        for name in (
            "classes:teach_class_detail",
            "classes:teach_class_registrations",
            "classes:teach_class_waitlist",
            "classes:teach_class_discount_codes",
            "classes:teach_class_emails",
        ):
            html = _html(client, name, guild_class)
            assert "View public profile" in html, name

    def it_offers_send_email_only_to_a_viewer_who_may_use_it(instructor, guild_lead, guild_class, client):
        compose = f"{reverse('hub_compose')}?audience=class:{guild_class.pk}&amp;lock=1"
        client.force_login(instructor.user)
        assert compose in _html(client, "classes:teach_class_detail", guild_class)
        client.force_login(guild_lead.user)
        assert compose not in _html(client, "classes:teach_class_emails", guild_class)


def describe_the_guild_leads_screen():
    def it_lands_them_on_emails_and_explains_the_boundary(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        html = _html(client, "classes:teach_class_emails", guild_class)
        assert "This class is in the <strong>Metalsmithing</strong> guild" in html
        assert "<strong>Ren Alvarez</strong>, who teaches it" in html

    def it_writes_a_whole_sentence_when_the_category_has_no_guild(instructor, client, db):
        # Production carries 86 guild-less categories. The guild leg admits a fog officer who
        # also teaches on one of them, so the sentence has to hold together without a guild
        # name to put in it.
        instructor.fog_role = Member.FogRole.GUILD_OFFICER
        instructor.save(update_fields=["fog_role"])
        offering = ClassOfferingFactory(
            slug="guildless", category=CategoryFactory(guild=None), instructor=InstructorFactory()
        )
        client.force_login(instructor.user)
        html = _html(client, "classes:teach_class_emails", offering)
        assert "You can edit this class and write its welcome email." in html
        assert "guild, so you can edit it" not in html

    def it_serves_them_no_overview_no_roster_and_no_waitlist(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        for name in (
            "classes:teach_class_detail",
            "classes:teach_class_registrations",
            "classes:teach_class_waitlist",
            "classes:teach_class_discount_codes",
        ):
            assert client.get(reverse(name, kwargs={"pk": guild_class.pk})).status_code == 404, name


def describe_the_reviewers_screen():
    def it_gives_them_the_overview_and_their_two_buttons(reviewer, guild_class, client):
        guild_class.status = Status.PENDING
        guild_class.save(update_fields=["status"])
        client.force_login(reviewer.user)
        html = _html(client, "classes:teach_class_detail", guild_class)
        assert reverse("classes:admin_class_approve", kwargs={"pk": guild_class.pk}) in html
        assert "Review with notes" in html

    def it_serves_them_none_of_the_admin_lifecycle_actions(reviewer, guild_class, client):
        client.force_login(reviewer.user)
        html = _html(client, "classes:teach_class_detail", guild_class)
        for name in (
            "classes:admin_class_archive",
            "classes:admin_class_delete",
            "classes:admin_class_unpublish",
            "classes:teach_class_edit",
            "classes:teach_class_cancel",
        ):
            assert reverse(name, kwargs={"pk": guild_class.pk}) not in html, name


def describe_retiring_the_instructor_only_lookup():
    """Each of the ten paths that used to scope itself through ``_teach_class_or_404``.

    Named one by one rather than looped, because the failure this guards against is one path
    quietly falling back to an unscoped lookup — and that is exactly what a single looping
    test would keep green while nine of the ten broke.
    """

    def it_refuses_a_guild_lead_the_overview(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        assert client.get(reverse("classes:teach_class_detail", kwargs={"pk": guild_class.pk})).status_code == 404

    def it_refuses_a_guild_lead_the_roster(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        assert (
            client.get(reverse("classes:teach_class_registrations", kwargs={"pk": guild_class.pk})).status_code == 404
        )

    def it_refuses_a_guild_lead_the_roster_table_partial(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        url = reverse("classes:teach_class_registrations_table", kwargs={"pk": guild_class.pk})
        assert client.get(url).status_code == 404

    def it_refuses_a_guild_lead_the_roster_email_send(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        url = reverse("classes:teach_class_email", kwargs={"pk": guild_class.pk})
        assert client.post(url, {"subject": "hi", "body": "there"}).status_code == 404

    def it_refuses_a_guild_lead_the_waitlist(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        assert client.get(reverse("classes:teach_class_waitlist", kwargs={"pk": guild_class.pk})).status_code == 404

    def it_refuses_a_guild_lead_the_discount_codes(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        url = reverse("classes:teach_class_discount_codes", kwargs={"pk": guild_class.pk})
        assert client.get(url).status_code == 404

    def it_refuses_a_guild_lead_the_sale_modal(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        url = reverse("classes:teach_class_sale", kwargs={"pk": guild_class.pk})
        assert client.post(url, {"sale_kind": "percent", "sale_percent": "10"}).status_code == 404
        guild_class.refresh_from_db()
        assert guild_class.sale_enabled is False

    def it_refuses_a_guild_lead_the_withdraw_action(guild_lead, guild_class, client):
        guild_class.status = Status.PENDING
        guild_class.save(update_fields=["status"])
        client.force_login(guild_lead.user)
        url = reverse("classes:teach_class_withdraw", kwargs={"pk": guild_class.pk})
        assert client.post(url).status_code == 404
        guild_class.refresh_from_db()
        assert guild_class.status == Status.PENDING

    def it_refuses_a_guild_lead_the_cancel_action(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        url = reverse("classes:teach_class_cancel", kwargs={"pk": guild_class.pk})
        assert client.post(url, {"reason": "no"}).status_code == 404
        guild_class.refresh_from_db()
        assert guild_class.status == Status.PUBLISHED

    def it_refuses_a_guild_lead_the_request_change_action(guild_lead, guild_class, client):
        client.force_login(guild_lead.user)
        url = reverse("classes:teach_class_request_change", kwargs={"pk": guild_class.pk})
        assert client.post(url, {"note": "please change it"}).status_code == 404


def describe_the_irreversible_actions():
    """Cancel, Delete, Unpublish, Archive, Approve and the per-class Send Email.

    Cancelling emails every registrant, guests with no account included, and deleting has no
    soft-delete column behind it. Neither is undone by a revert, so the endpoint asserts the
    capability itself rather than trusting the screen not to have drawn the button.
    """

    def _post(client, name, offering, data=None):
        return client.post(reverse(name, kwargs={"pk": offering.pk}), data or {})

    def _preview_as_member(client, admin_user):
        """Log the admin in and pick "Member" in the View As switcher.

        ``force_login`` starts a fresh session, so the pick has to be stamped after it —
        stamping it in a fixture that logs in first is silently undone by the next login.
        """
        client.force_login(admin_user)
        session = client.session
        session["view_as_role"] = "member"
        session.save()

    def it_refuses_cancel(reviewer, guild_lead, admin_user, guild_class, client):
        for member in (reviewer, guild_lead):
            client.force_login(member.user)
            assert _post(client, "classes:teach_class_cancel", guild_class, {"reason": "x"}).status_code == 404
        _preview_as_member(client, admin_user)
        assert _post(client, "classes:teach_class_cancel", guild_class, {"reason": "x"}).status_code == 404
        guild_class.refresh_from_db()
        assert guild_class.status == Status.PUBLISHED

    def it_refuses_delete(reviewer, guild_lead, admin_user, guild_class, client):
        for member in (reviewer, guild_lead):
            client.force_login(member.user)
            assert _post(client, "classes:admin_class_delete", guild_class).status_code == 404
        _preview_as_member(client, admin_user)
        assert _post(client, "classes:admin_class_delete", guild_class).status_code == 404
        assert ClassOffering.objects.filter(pk=guild_class.pk).exists()

    def it_refuses_unpublish(reviewer, guild_lead, admin_user, guild_class, client):
        for member in (reviewer, guild_lead):
            client.force_login(member.user)
            assert _post(client, "classes:admin_class_unpublish", guild_class).status_code == 404
        _preview_as_member(client, admin_user)
        assert _post(client, "classes:admin_class_unpublish", guild_class).status_code == 404
        guild_class.refresh_from_db()
        assert guild_class.status == Status.PUBLISHED

    def it_refuses_archive(reviewer, guild_lead, admin_user, guild_class, client):
        for member in (reviewer, guild_lead):
            client.force_login(member.user)
            assert _post(client, "classes:admin_class_archive", guild_class).status_code == 404
        _preview_as_member(client, admin_user)
        assert _post(client, "classes:admin_class_archive", guild_class).status_code == 404
        guild_class.refresh_from_db()
        assert guild_class.status == Status.PUBLISHED

    def it_refuses_approve_to_a_guild_lead_and_to_a_previewing_admin(guild_lead, admin_user, guild_class, client):
        # A reviewer is NOT refused here: approving is the whole contract of the
        # CLASS_APPROVER grant, and ``can_approve`` is what the endpoint asserts.
        guild_class.status = Status.PENDING
        guild_class.save(update_fields=["status"])
        client.force_login(guild_lead.user)
        assert _post(client, "classes:admin_class_approve", guild_class).status_code == 404
        _preview_as_member(client, admin_user)
        assert _post(client, "classes:admin_class_approve", guild_class).status_code == 404
        guild_class.refresh_from_db()
        assert guild_class.status == Status.PENDING

    def it_refuses_the_per_class_send_email(reviewer, guild_lead, admin_user, guild_class, client):
        RegistrationFactory(class_offering=guild_class, status=Registration.Status.CONFIRMED)
        payload = {"subject": "Hello", "body": "There"}
        for member in (reviewer, guild_lead):
            client.force_login(member.user)
            assert _post(client, "classes:teach_class_email", guild_class, payload).status_code == 404
        _preview_as_member(client, admin_user)
        assert _post(client, "classes:teach_class_email", guild_class, payload).status_code == 404


def describe_a_modal_that_comes_back_with_an_error_in_it():
    """The bound-invalid-form re-renders, which are the render paths most easily missed.

    ``{% extends screen_shell %}`` resolves at render time and an unset variable becomes '',
    which raises TemplateSyntaxError — a missing shell is a 500, not a degraded page.
    """

    def it_reopens_the_cancel_modal_on_a_blank_reason(admin_user, guild_class, client):
        client.force_login(admin_user)
        response = client.post(reverse("classes:teach_class_cancel", kwargs={"pk": guild_class.pk}), {"reason": ""})
        assert response.status_code == 200
        assert "$dispatch('open-modal', 'cancel-class')" in response.content.decode()
        guild_class.refresh_from_db()
        assert guild_class.status == Status.PUBLISHED

    def it_reopens_the_sale_modal_on_an_impossible_amount(admin_user, guild_class, client):
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:teach_class_sale", kwargs={"pk": guild_class.pk}),
            {"sale_kind": "fixed", "sale_amount_cents": "999.00"},
        )
        assert response.status_code == 200
        assert "$dispatch('open-modal', 'class-sale')" in response.content.decode()
        guild_class.refresh_from_db()
        assert guild_class.sale_is_active is False

    def it_reopens_the_request_change_modal_on_a_blank_note(instructor, guild_class, client):
        client.force_login(instructor.user)
        response = client.post(
            reverse("classes:teach_class_request_change", kwargs={"pk": guild_class.pk}), {"note": ""}
        )
        assert response.status_code == 200
        assert "$dispatch('open-modal', 'request-change')" in response.content.decode()


def describe_the_overviews_queryset():
    def it_does_not_cost_a_query_per_session_row(admin_user, guild_class, client, django_assert_num_queries):
        """Criterion 37, and the bot rubric blocks an N+1 besides.

        The teaching Overview used to fetch the class plainly and then walk ``sessions.all``
        and ``instructor`` in the template. The merged screen takes the queryset the admin
        page already had — the joins, the prefetch and the count — so a class with six
        sessions costs exactly what a class with one costs.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        client.force_login(admin_user)
        url = reverse("classes:teach_class_detail", kwargs={"pk": guild_class.pk})
        client.get(url)  # warm anything cached per-process rather than per-request
        with CaptureQueriesContext(connection) as before:
            assert client.get(url).status_code == 200

        starts = timezone.now() + timezone.timedelta(days=20)
        for offset in range(5):
            ClassSessionFactory(
                class_offering=guild_class,
                starts_at=starts + timezone.timedelta(days=offset),
                ends_at=starts + timezone.timedelta(days=offset, hours=2),
            )
        with django_assert_num_queries(len(before)):
            assert client.get(url).status_code == 200


def describe_the_image_endpoints():
    """All five work from the merged screen, for an admin and for the class's instructor.

    Three carry a class pk and go through the screen's own gate; the two that carry an IMAGE
    pk scope themselves through the image's class and then ask that class the same question.
    """

    def _png() -> object:
        from django.core.files.uploadedfile import SimpleUploadedFile

        raw = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
            b"\x00\x00IEND\xaeB`\x82"
        )
        return SimpleUploadedFile("shot.png", raw, content_type="image/png")

    @pytest.fixture
    def draft(instructor) -> ClassOffering:
        return ClassOfferingFactory(instructor=instructor, slug="image-draft", status=Status.DRAFT, gallery=0)

    def it_lets_an_admin_use_all_five(admin_user, draft, client):
        import json

        client.force_login(admin_user)
        pk = {"pk": draft.pk}
        assert client.post(reverse("classes:teach_class_hero_upload", kwargs=pk), {"image": _png()}).status_code == 200
        upload = client.post(reverse("classes:teach_class_image_upload", kwargs=pk), {"image": _png()})
        assert upload.status_code == 200
        image_pk = json.loads(upload.content)["id"]
        reorder = client.post(
            reverse("classes:teach_class_image_reorder", kwargs=pk),
            data=json.dumps({"order": [image_pk]}),
            content_type="application/json",
        )
        assert reorder.status_code == 200
        alt = client.post(
            reverse("classes:teach_class_image_alt", kwargs={"pk": image_pk}),
            data=json.dumps({"alt_text": "A crucible"}),
            content_type="application/json",
        )
        assert alt.status_code == 200
        delete = client.post(reverse("classes:teach_class_image_delete", kwargs={"pk": image_pk}))
        assert delete.status_code == 200

    def it_lets_the_instructor_use_all_five(instructor, draft, client):
        import json

        client.force_login(instructor.user)
        pk = {"pk": draft.pk}
        assert client.post(reverse("classes:teach_class_hero_upload", kwargs=pk), {"image": _png()}).status_code == 200
        upload = client.post(reverse("classes:teach_class_image_upload", kwargs=pk), {"image": _png()})
        assert upload.status_code == 200
        image_pk = json.loads(upload.content)["id"]
        reorder = client.post(
            reverse("classes:teach_class_image_reorder", kwargs=pk),
            data=json.dumps({"order": [image_pk]}),
            content_type="application/json",
        )
        assert reorder.status_code == 200
        alt = client.post(
            reverse("classes:teach_class_image_alt", kwargs={"pk": image_pk}),
            data=json.dumps({"alt_text": "A crucible"}),
            content_type="application/json",
        )
        assert alt.status_code == 200
        delete = client.post(reverse("classes:teach_class_image_delete", kwargs={"pk": image_pk}))
        assert delete.status_code == 200

    def it_refuses_an_image_belonging_to_someone_elses_class(guild_lead, client, db):
        other = ClassOfferingFactory(instructor=InstructorFactory(), slug="not-theirs", status=Status.DRAFT, gallery=0)
        image = ClassImageFactory(class_offering=other)
        client.force_login(guild_lead.user)
        assert client.post(reverse("classes:teach_class_image_delete", kwargs={"pk": image.pk})).status_code == 404

    def it_refuses_a_reviewer_who_may_read_the_class_but_not_edit_it(reviewer, draft, client):
        client.force_login(reviewer.user)
        url = reverse("classes:teach_class_image_upload", kwargs={"pk": draft.pk})
        assert client.post(url, {"image": _png()}).status_code == 404

    def it_refuses_the_instructor_a_cancelled_class_but_still_lets_an_admin_fix_it(
        instructor, admin_user, draft, client
    ):
        # The composer bounces a cancelled or archived class to an admin, so the routes behind
        # it agree: a page gate and a mutation gate that disagree are how someone ends up
        # curling a surface the UI never offers.
        draft.status = Status.CANCELLED
        draft.save(update_fields=["status"])
        url = reverse("classes:teach_class_image_upload", kwargs={"pk": draft.pk})
        client.force_login(instructor.user)
        assert client.post(url, {"image": _png()}).status_code == 404
        client.force_login(admin_user)
        assert client.post(url, {"image": _png()}).status_code == 200


def describe_the_portal_tab_strip():
    """Ruling 13 merged the two portal strips into one; ruling 14 made every tab decide for itself."""

    @pytest.fixture
    def admin_who_teaches(db) -> Member:
        MembershipPlanFactory()
        user = UserFactory(username="screen-both@example.com")
        member = InstructorFactory(user=user, full_legal_name="Bo Teacher", instructor_slug="bo-teacher")
        member.fog_role = Member.FogRole.ADMIN
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["fog_role", "instructor_oriented_at"])
        member.sync_user_permissions()
        return member

    def _strip(client, url: str) -> str:
        html = client.get(url).content.decode()
        start = html.index('aria-label="Class management sections"')
        return html[start : html.index("</nav>", start)]

    def it_gives_an_admin_who_also_teaches_every_tab(admin_who_teaches, client):
        # Ruling 22: the subtractive alternative — drop "My Classes" and add a "Mine" chip to
        # the Classes facet row — was offered and rejected.
        client.force_login(admin_who_teaches.user)
        strip = _strip(client, reverse("classes:admin_overview"))
        for label in (
            ">Overview<",
            ">My Classes<",
            ">Registrations<",
            ">Instructor Profile<",
            ">Classes<",
            ">Catalog Activity<",
            ">Settings<",
        ):
            assert label in strip, label

    def it_hides_the_teaching_tabs_from_an_admin_who_does_not_teach(admin_user, client, db):
        client.force_login(admin_user)
        strip = _strip(client, reverse("classes:admin_overview"))
        assert ">My Classes<" not in strip
        assert ">Instructor Profile<" not in strip
        assert ">Classes<" in strip

    def it_hides_the_site_wide_tabs_from_an_instructor(instructor, client):
        client.force_login(instructor.user)
        strip = _strip(client, reverse("classes:teach_overview"))
        assert ">My Classes<" in strip
        assert ">Instructor Profile<" in strip
        assert ">Classes<" not in strip
        assert ">Catalog Activity<" not in strip
        assert ">Settings<" not in strip

    def it_runs_personal_first_and_site_wide_last(admin_who_teaches, client):
        # An admin's Registrations moves one slot earlier than it used to: that is the price
        # of one order that works for everyone, and it was drawn and approved.
        client.force_login(admin_who_teaches.user)
        strip = _strip(client, reverse("classes:admin_overview"))
        order = [strip.index(label) for label in (">Overview<", ">My Classes<", ">Registrations<", ">Classes<")]
        assert order == sorted(order)

    def it_lights_my_classes_and_not_classes_on_the_instructors_list(admin_who_teaches, client):
        client.force_login(admin_who_teaches.user)
        strip = _strip(client, reverse("classes:teach_dashboard"))
        assert 'vote-tab vote-tab--active" aria-current="page">My Classes</a>' in strip
        assert 'vote-tab vote-tab--active" aria-current="page">Classes</a>' not in strip


def describe_ruling_five():
    def it_introduces_nothing_called_a_workspace(settings):
        """Nothing this change adds is called "Workspace" — not a name, not a string.

        The pre-existing ``_class_workspace_counts`` is out of scope and stays, which is why
        this reads the files the change introduced rather than grepping the whole app.
        """
        import pathlib

        root = pathlib.Path(settings.BASE_DIR)
        introduced = [
            "templates/classes/_components/class_screen_base.html",
            "templates/classes/_components/portal_tabs.html",
            "templates/classes/class_form.html",
        ]
        for name in introduced:
            assert "workspace" not in (root / name).read_text().lower(), name
