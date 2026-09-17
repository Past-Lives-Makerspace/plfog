"""The review flow map: its derivation, its hand-written lines, and the page that draws it.

Half of this file is the map's drift guard. ``classes/review_map.py`` derives the steps from
the pipeline, the audience from the event registry and the trigger function from the sender
object itself, but four things in each :class:`~classes.review_map.Notice` are still typed by a
human: the step it hangs off, the event keys it rides, its gallery key, and whether it writes a
bell row. Every one of those is pinned below to something that would have to change with it:

* the **event keys** to the sender's own source, so re-pointing a sender at a different event
  fails here rather than shipping a page that names the old one;
* the **email-only** flag and the **audience override** to ``recipient_user_ids=set()``, the one
  thing in the codebase that actually empties a fan-out;
* the **gallery key** to the published email gallery, so a preview link cannot 404;
* and **completeness** to the gallery's own registry, which ``tests/core/
  email_gallery_completeness_spec.py`` already fails CI over. Add a review email, register its
  card, forget this map, and ``it_maps_every_review_email_the_gallery_knows_about`` says so.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from django.urls import reverse

from classes import emails, review_map
from classes.factories import CategoryFactory, ClassOfferingFactory, UserFactory
from classes.models import ClassOffering
from classes.review_map import GALLERY_URL, NOTICES, reference_pipeline, review_flow

REPO_ROOT = Path(__file__).resolve().parents[2]

# The events the review flow rides. Derived from the table so a notice added on a NEW event key
# widens the completeness guard with it instead of quietly falling outside it.
REVIEW_EVENT_KEYS = {key for notice in NOTICES for key in notice.event_keys}


def _gallery_emails():
    from tests.e2e.email_gallery.registry import gallery_emails

    return gallery_emails()


@pytest.fixture
def cms_admin_user(db):
    """A plain member holding the CLASS_APPROVER capability (a CMS Administrator)."""
    from membership.models import AdminCapability, Member
    from tests.membership.factories import MembershipPlanFactory

    MembershipPlanFactory()
    user = UserFactory(username="flowmap-cms@example.com")
    member = Member.objects.get(user=user)
    member.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
    return user


def describe_reference_pipeline():
    def it_draws_both_review_lanes_in_one_column():
        pipeline = reference_pipeline()
        assert [column.key for column in pipeline.columns] == ["submitted", "review", "live"]
        review = pipeline.columns[1]
        assert review.is_parallel
        assert [lane.key for lane in review.lanes] == ["guild_lead", "admin"]

    def it_puts_neither_reviewer_ahead_of_the_other():
        # The whole point of the map: a sample that made one lane "current" and the other
        # "ahead" would draw the queue this flow stopped being.
        lanes = reference_pipeline().columns[1].lanes
        assert {lane.state for lane in lanes} == {"ahead"}

    def it_reads_no_database(django_assert_num_queries):
        with django_assert_num_queries(0):
            reference_pipeline()

    def it_invents_no_approval_rows():
        assert all(lane.detail == "" for lane in reference_pipeline().steps)


def describe_review_flow():
    def it_returns_one_stage_per_pipeline_lane():
        stages = review_flow(reference_pipeline())
        assert [stage.step.key for stage in stages] == ["submitted", "guild_lead", "admin", "live"]

    def it_hangs_every_notice_off_a_lane_the_pipeline_draws():
        lane_keys = {step.key for step in reference_pipeline().steps}
        orphans = sorted({notice.step for notice in NOTICES} - lane_keys)
        assert orphans == [], f"notices hang off steps no pipeline lane carries: {orphans}"

    def it_sends_nothing_of_its_own_when_the_class_goes_live():
        live = review_flow(reference_pipeline())[-1]
        assert live.step.key == "live"
        assert live.notices == ()

    def it_drops_the_guild_lead_notices_for_a_lead_less_category(db):
        # The pipeline decides the shape, so a category with no lead loses that lane's three
        # notices without review_map branching on it.
        offering = ClassOfferingFactory(ready=True, category=CategoryFactory(guild=None))
        stages = review_flow(offering.review_pipeline())
        assert [stage.step.key for stage in stages] == ["submitted", "admin", "live"]

    def it_maps_a_real_class_the_same_way_it_maps_the_sample(db):
        from membership.models import Member
        from tests.membership.factories import GuildFactory

        lead = Member.objects.get(user=UserFactory(username="flowmap-lead@example.com"))
        category = CategoryFactory(guild=GuildFactory(name="Flowmap Guild", guild_lead=lead))
        offering = ClassOfferingFactory(ready=True, category=category, status=ClassOffering.Status.DRAFT)
        offering.submit_for_review()
        real = [stage.step.key for stage in review_flow(offering.review_pipeline())]
        assert real == [stage.step.key for stage in review_flow(reference_pipeline())]

    def describe_the_resolved_notice():
        def it_names_the_real_sender_by_module_and_function():
            first = review_flow(reference_pipeline())[0].notices[0]
            assert first.sender == "classes.emails.send_guild_lead_review_request"

        def it_reads_the_audience_off_the_event_registry():
            from core.events.copy import audience_description
            from core.events.registry import get_event

            admin_request = review_flow(reference_pipeline())[0].notices[1]
            assert admin_request.audience == audience_description(get_event("class_validation_requested"))

        def it_reads_the_email_default_off_the_event_registry():
            admin_request = review_flow(reference_pipeline())[0].notices[1]
            assert [event.email_default for event in admin_request.events] == ["on"]

        def it_says_an_audience_once_when_two_events_share_it():
            decision = review_flow(reference_pipeline())[1].notices[-1]
            assert len(decision.events) == 2
            assert decision.audience == "The class's instructor."

        def it_links_the_preview_by_gallery_anchor():
            first = review_flow(reference_pipeline())[0].notices[0]
            assert first.preview_url == f"{GALLERY_URL}#review_request"


def describe_resolve_event():
    def it_refuses_an_event_that_declares_no_email_channel(monkeypatch):
        from dataclasses import replace

        from core.events.registry import get_event

        emailless = replace(get_event("class_review_requested"), channels=())
        monkeypatch.setattr(review_map, "get_event", lambda key: emailless)
        with pytest.raises(ValueError, match="declares no EMAIL channel"):
            review_map._resolve_event("class_review_requested")


def describe_the_hand_written_lines():
    """Each of these pins one thing a human typed to the thing it claims to describe."""

    def it_names_a_sender_that_really_emits_the_event_it_is_mapped_to():
        for notice in NOTICES:
            source = inspect.getsource(notice.sender)
            for key in notice.event_keys:
                assert key in source, f"{notice.sender.__name__} does not mention '{key}'"

    def it_names_senders_that_live_in_classes_emails():
        for notice in NOTICES:
            assert getattr(emails, notice.sender.__name__) is notice.sender

    def it_marks_a_notice_email_only_exactly_when_its_sender_empties_the_fan_out():
        # ``recipient_user_ids=set()`` is the one thing that stops emit writing bell rows, so it
        # is the only justification for claiming an email lands with no in-app notification.
        for notice in NOTICES:
            empties = "recipient_user_ids=set()" in inspect.getsource(notice.sender)
            assert empties == (not notice.in_app), f"{notice.title} disagrees with {notice.sender.__name__}"

    def it_overrides_the_registry_audience_only_where_the_fan_out_is_emptied():
        for notice in NOTICES:
            empties = "recipient_user_ids=set()" in inspect.getsource(notice.sender)
            assert bool(notice.audience) == empties, f"{notice.title} overrides an audience it should read"

    def it_points_every_preview_at_a_card_the_gallery_really_builds():
        known = {email.key for email in _gallery_emails()}
        missing = sorted({notice.gallery_key for notice in NOTICES} - known)
        assert missing == [], f"gallery has no card for {missing}; the preview link would land nowhere"

    def it_points_previews_at_the_host_the_gallery_publishes_to():
        workflow = (REPO_ROOT / ".github/workflows/copy-review.yml").read_text(encoding="utf-8")
        assert GALLERY_URL == "https://copy-review.pastlives.space/"
        assert "copy-review.pastlives.space" in workflow

    def it_maps_every_review_email_the_gallery_knows_about():
        # The gallery's own completeness guard already fails CI on an unregistered email, so a
        # new review email has to appear here — and then in NOTICES, or this fails.
        mapped = {notice.gallery_key for notice in NOTICES}
        unmapped = sorted(
            email.key for email in _gallery_emails() if email.event_keys & REVIEW_EVENT_KEYS and email.key not in mapped
        )
        assert unmapped == [], f"review emails missing from classes/review_map.py: {unmapped}"


def describe_admin_review_flow_map():
    def it_renders_for_an_admin(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_review_flow_map"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "How Class Review Works" in body
        # The real registry, not a fixture: the audience line comes from the event resolver.
        assert "The CMS Administrators (holders only)." in body
        assert "classes.emails.send_guild_lead_review_request" in body
        assert f"{GALLERY_URL}#review_decision" in body

    def it_draws_the_shared_pipeline_strip(admin_user, client, db):
        client.force_login(admin_user)
        body = client.get(reverse("classes:admin_review_flow_map")).content.decode()
        assert 'data-step="guild_lead"' in body
        assert 'class="pl-pipeline__col pl-pipeline__col--parallel"' in body

    def it_admits_a_cms_administrator(cms_admin_user, client, db):
        client.force_login(cms_admin_user)
        assert client.get(reverse("classes:admin_review_flow_map")).status_code == 200

    def it_refuses_a_plain_member(member_user, client, db):
        client.force_login(member_user)
        assert client.get(reverse("classes:admin_review_flow_map")).status_code == 403

    def it_refuses_a_signed_out_visitor(client, db):
        response = client.get(reverse("classes:admin_review_flow_map"))
        assert response.status_code == 302
        assert "/login" in response.url or "/accounts/login" in response.url

    def it_links_the_map_from_the_classes_area_navigation(admin_user, client, db):
        client.force_login(admin_user)
        body = client.get(reverse("classes:admin_overview")).content.decode()
        assert reverse("classes:admin_review_flow_map") in body
