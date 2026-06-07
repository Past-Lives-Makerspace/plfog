from django.contrib.auth.models import AnonymousUser

from classes.factories import InstructorFactory, UserFactory
from core.context_processors import persona


def _airtable_member(user):
    """Promote the auto-Member created by the post_save signal into a real
    Airtable-imported member. Real members have ``airtable_record_id`` set;
    that's the signal the persona check uses to distinguish them from the
    shell Members the signal creates for every user.

    Refreshes the user so the reverse OneToOne cache (``user.member``) re-fetches
    the updated row — otherwise the persona check sees the stale shell.
    """
    from membership.models import Member

    Member.objects.filter(user=user).update(airtable_record_id="recTEST123")
    if hasattr(user, "_state"):
        user.refresh_from_db()
    if hasattr(user, "_member_cache"):  # Django < 4.x cache name
        del user._member_cache
    # Bust the reverse-OneToOne descriptor cache so the next access reloads.
    user.__dict__.pop("member", None)
    return Member.objects.get(user=user)


def describe_persona():
    def it_returns_anon_for_anonymous_user(rf):
        req = rf.get("/")
        req.user = AnonymousUser()
        assert persona(req) == {"persona": "anon", "is_member_persona": False, "is_instructor_persona": False}

    def it_returns_nonmember_for_plain_authed_user(rf, db, django_user_model):
        # The post_save signal auto-creates a shell Member for every user. With
        # no airtable_record_id, that shell reads as "nonmember" — they're just
        # someone with an account who booked a class.
        user = django_user_model.objects.create_user(email="a@b.com", username="a@b.com")
        req = rf.get("/")
        req.user = user
        result = persona(req)
        assert result["persona"] == "nonmember"
        assert result["is_member_persona"] is False

    def it_returns_member_for_user_linked_to_active_airtable_member(rf, db):
        user = UserFactory()
        _airtable_member(user)
        req = rf.get("/")
        req.user = user
        assert persona(req)["persona"] == "member"
        assert persona(req)["is_member_persona"] is True

    def it_returns_instructor_for_user_linked_to_instructor_no_airtable_member(rf, db):
        instructor = InstructorFactory(user=UserFactory())
        req = rf.get("/")
        req.user = instructor.user
        assert persona(req)["persona"] == "instructor"
        assert persona(req)["is_instructor_persona"] is True

    def context_when_user_is_both_member_and_instructor():
        def it_prefers_member_persona(rf, db):
            user = UserFactory()
            _airtable_member(user)
            InstructorFactory(user=user)
            req = rf.get("/")
            req.user = user
            result = persona(req)
            assert result["persona"] == "member"
            assert result["is_instructor_persona"] is True  # instructor flag still true

    def it_caches_result_on_request(rf, db):
        from membership.models import Member

        user = UserFactory()
        _airtable_member(user)
        req = rf.get("/")
        req.user = user
        first = persona(req)
        # Mutate the underlying record. Cached result should still match the first call.
        Member.objects.filter(user=user).update(status=Member.Status.FORMER)
        second = persona(req)
        assert second == first
