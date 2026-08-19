import pytest
from datetime import timedelta
from django.test import Client
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import Registration


def _airtable_promote(user):
    from membership.models import Member

    Member.objects.filter(user=user).update(airtable_record_id="recTEST123")
    user.__dict__.pop("member", None)


def _future_class():
    offering = ClassOfferingFactory(status="published", title="Forge a Hook")
    ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=5))
    return offering


@pytest.fixture
def book_client(settings):
    settings.PUBLIC_HOSTS = ["book.pastlives.space"]
    settings.ALLOWED_HOSTS = ["book.pastlives.space", "members.pastlives.space"]
    return Client(HTTP_HOST="book.pastlives.space")


def describe_account_overview():
    def describe_with_no_upcoming():
        def it_renders_empty_state_with_browse_cta(book_client, db):
            user = UserFactory()
            book_client.force_login(user)
            resp = book_client.get("/account/")
            assert resp.status_code == 200
            assert b"No upcoming classes yet" in resp.content
            assert b"Browse classes" in resp.content

    def describe_with_upcoming():
        def it_renders_each_class_card(book_client, db):
            user = UserFactory()
            offering = _future_class()
            RegistrationFactory(
                email=user.email,
                class_offering=offering,
                status=Registration.Status.CONFIRMED,
            )
            book_client.force_login(user)
            resp = book_client.get("/account/")
            assert b"Forge a Hook" in resp.content
            assert b"Registered" in resp.content

    def describe_with_slugless_instructor():
        def it_renders_the_card_without_a_500(book_client, db):
            # An instructor Member whose instructor_slug is blank (e.g. set as an
            # offering's instructor without being promoted) must not blow up the
            # page: {% url 'public_instructor' '' %} raises NoReverseMatch.
            user = UserFactory()
            inst = InstructorFactory(instructor_slug="", full_legal_name="Pat Smith")
            offering = ClassOfferingFactory(status="published", title="Forge a Hook", instructor=inst)
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=5))
            RegistrationFactory(
                email=user.email,
                class_offering=offering,
                status=Registration.Status.CONFIRMED,
            )
            book_client.force_login(user)
            resp = book_client.get("/account/")
            assert resp.status_code == 200
            # Name still shown, just not linked.
            assert inst.display_name.encode() in resp.content
            assert b"/classes/instructors//" not in resp.content

    def describe_persona_member():
        def it_shows_the_member_banner(book_client, db):
            user = UserFactory()
            _airtable_promote(user)
            book_client.force_login(user)
            resp = book_client.get("/account/")
            assert b"You&#x27;re a Past Lives member" in resp.content or b"You're a Past Lives member" in resp.content
            assert b"Open Member Portal dashboard" in resp.content

    def describe_persona_instructor():
        def it_shows_instructor_banner_with_upcoming_count(book_client, db):
            inst = InstructorFactory(user=UserFactory())
            offering = ClassOfferingFactory(status="published", instructor=inst)
            ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=2))
            book_client.force_login(inst.user)
            resp = book_client.get("/account/")
            assert b"bk-banner is-instructor" in resp.content
            assert b"/classes/teach/" in resp.content

        def it_hides_instructor_banner_when_no_upcoming_classes(book_client, db):
            inst = InstructorFactory(user=UserFactory())
            book_client.force_login(inst.user)
            resp = book_client.get("/account/")
            assert b"bk-banner is-instructor" not in resp.content

    def describe_persona_nonmember():
        def it_shows_the_soft_nudge_when_user_has_upcoming(book_client, db):
            user = UserFactory()
            offering = _future_class()
            RegistrationFactory(
                email=user.email,
                class_offering=offering,
                status=Registration.Status.CONFIRMED,
            )
            book_client.force_login(user)
            resp = book_client.get("/account/")
            assert b"Membership opens the door" in resp.content

        def it_hides_the_nudge_when_cookie_is_set(book_client, db):
            user = UserFactory()
            offering = _future_class()
            RegistrationFactory(
                email=user.email,
                class_offering=offering,
                status=Registration.Status.CONFIRMED,
            )
            book_client.force_login(user)
            book_client.cookies["pl_nudge_dismissed"] = "1"
            resp = book_client.get("/account/")
            assert b"Membership opens the door" not in resp.content

        def it_does_not_show_nudge_when_user_has_no_upcoming(book_client, db):
            user = UserFactory()
            book_client.force_login(user)
            resp = book_client.get("/account/")
            # Nudge only shows when there's a list of upcoming classes;
            # the empty state replaces it.
            assert b"Membership opens the door" not in resp.content
