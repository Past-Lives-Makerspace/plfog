from __future__ import annotations

import pytest

from membership.models import Member
from tests.tours.conftest import make_guide


@pytest.fixture
def guide(db: None) -> Member:
    """An active member holding the Tour Guide permission."""
    return make_guide()


@pytest.fixture
def admin(db: None) -> Member:
    """An admin, who may edit anyone's hours (#1300, part 4)."""
    from tests.membership.factories import MemberFactory

    return MemberFactory(full_legal_name="Ada Admin", fog_role=Member.FogRole.ADMIN)


@pytest.fixture
def plain(db: None) -> Member:
    """An active member with no hours page: no tours, no orientations."""
    from tests.membership.factories import MemberFactory

    return MemberFactory(full_legal_name="Pat Plain")
