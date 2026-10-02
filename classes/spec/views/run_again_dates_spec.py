"""Running a class again lands on its dates, and the dates surfaces say when to run it again.

A second group is a second run with its own dates and seats (``duplicate_as_new_run``), never
more dates on the same series, so every place an instructor could add dates says so and the
run-again redirect opens the composer on the Dates step. Anchors are markup
(``data-run-again-hint``, the ``run-again`` modal, ``?step=3``) and factory titles.
"""

from __future__ import annotations

from django.contrib.messages import get_messages
from django.urls import reverse

from classes.factories import InstructorFactory, SeriesClassOfferingFactory, UserFactory
from classes.models import ClassOffering


def _instructor(client):
    user = UserFactory(username="run-again-dates@example.com")
    member = InstructorFactory(user=user, instructor_slug="run-again-dates")
    client.force_login(user)
    return member


def describe_run_it_again():
    def it_lands_on_the_dates_step_with_a_message_naming_the_run(client, db):
        member = _instructor(client)
        original = SeriesClassOfferingFactory(
            instructor=member, title="Blacksmithing 101 Again", status=ClassOffering.Status.PUBLISHED, session_count=3
        )
        resp = client.post(reverse("classes:teach_class_duplicate_run", kwargs={"pk": original.pk}))
        run = ClassOffering.objects.exclude(pk=original.pk).get(title="Blacksmithing 101 Again")
        assert resp.url == f"{reverse('classes:teach_class_edit', kwargs={'pk': run.pk})}?step=3"
        texts = [str(m) for m in get_messages(resp.wsgi_request)]
        assert any(t.startswith("This is a new run of Blacksmithing 101 Again.") for t in texts)

    def it_explains_separate_runs_in_the_confirm(client, db):
        member = _instructor(client)
        offering = SeriesClassOfferingFactory(
            instructor=member, title="Confirm Copy Series", status=ClassOffering.Status.PUBLISHED, session_count=2
        )
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        start = html.index("confirm-run-again") if "confirm-run-again" in html else html.index("run-again")
        assert "Students sign up for each run separately" in html[start:]


def describe_the_dates_surfaces():
    def it_tells_a_draft_composer_not_to_add_a_second_groups_dates(client, db):
        member = _instructor(client)
        offering = SeriesClassOfferingFactory(instructor=member, status=ClassOffering.Status.DRAFT, session_count=1)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        assert "<span data-run-again-hint>" in html
        assert "Teaching it again to a new group? Use Run it again on the class page." in html

    def it_points_a_published_class_at_run_it_again(client, db):
        member = _instructor(client)
        offering = SeriesClassOfferingFactory(instructor=member, status=ClassOffering.Status.PUBLISHED, session_count=2)
        html = client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()
        detail = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert (
            f'data-run-again-hint style="margin:0.75rem 0 0;">To offer this class to another group, use <a href="{detail}">'
            in html
        )
