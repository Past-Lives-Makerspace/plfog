from __future__ import annotations

import pytest

from membership.models import Member
from tests.tours.conftest import make_guide


@pytest.fixture
def guide(db: None) -> Member:
    """An active member holding the Tour Guide permission."""
    return make_guide()
