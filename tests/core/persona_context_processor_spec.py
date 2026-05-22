from django.contrib.auth.models import AnonymousUser

from core.context_processors import persona
from classes.factories import InstructorFactory, UserFactory
from tests.membership.factories import MemberFactory


def describe_persona():
    def it_returns_anon_for_anonymous_user(rf):
        req = rf.get("/")
        req.user = AnonymousUser()
        assert persona(req) == {"persona": "anon", "is_member_persona": False, "is_instructor_persona": False}

    def it_returns_nonmember_for_plain_authed_user(rf, db, django_user_model):
        user = django_user_model.objects.create_user(email="a@b.com", username="a@b.com")
        req = rf.get("/")
        req.user = user
        result = persona(req)
        assert result["persona"] == "nonmember"

    def it_returns_member_for_user_linked_to_active_member(rf, db):
        user = UserFactory()
        member = MemberFactory(user=user)
        req = rf.get("/")
        req.user = member.user
        assert persona(req)["persona"] == "member"
        assert persona(req)["is_member_persona"] is True

    def it_returns_instructor_for_user_linked_to_instructor_no_member(rf, db):
        instructor = InstructorFactory()
        req = rf.get("/")
        req.user = instructor.user
        assert persona(req)["persona"] == "instructor"

    def context_when_user_is_both_member_and_instructor():
        def it_prefers_member_persona(rf, db):
            user = UserFactory()
            member = MemberFactory(user=user)
            InstructorFactory(user=member.user)
            req = rf.get("/")
            req.user = member.user
            result = persona(req)
            assert result["persona"] == "member"
            assert result["is_instructor_persona"] is True  # instructor flag still true

    def it_caches_result_on_request(rf, db):
        from membership.models import Member
        user = UserFactory()
        member = MemberFactory(user=user)
        req = rf.get("/")
        req.user = member.user
        first = persona(req)
        # Mutate the underlying record. Cached result should still match the first call.
        member.status = Member.Status.INACTIVE
        member.save(update_fields=["status"])
        second = persona(req)
        assert second == first
