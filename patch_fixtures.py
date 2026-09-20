import re

for path in ['tests/core/spec/middleware/member_agreement_spec.py', 'tests/hub/member_agreement_spec.py', 'tests/membership/member_agreement_spec.py']:
    with open(path, 'r') as f:
        content = f.read()

    # We need to import UserFactory from tests.core.factories maybe?
    # Or just use the standard django_user_model fixture or simply from tests.membership.factories import MemberFactory
    # Actually if we do MemberFactory(_pre_signup_email="test@example.com") it might create a user?
    # Let's check how they do it in other tests.
