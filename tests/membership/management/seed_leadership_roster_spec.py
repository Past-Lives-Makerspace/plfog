"""BDD specs for the seed_leadership_roster management command (#464)."""

from __future__ import annotations

import base64
import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from membership.models import LeadershipListing
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

ROSTER = {
    "team": [{"name": "Ada Lovelace", "discord": "", "roles": [{"title": "Founder", "email": "ada@example.org"}]}],
    "guild_channels": [],
}


def _run(**options: object) -> str:
    out = StringIO()
    call_command("seed_leadership_roster", stdout=out, **options)
    return out.getvalue()


def describe_seed_leadership_roster():
    def it_seeds_from_a_file(tmp_path):
        MemberFactory(full_legal_name="Ada Lovelace")
        path = tmp_path / "roster.json"
        path.write_text(json.dumps(ROSTER), encoding="utf-8")
        out = _run(file=str(path))
        assert LeadershipListing.objects.filter(member__full_legal_name="Ada Lovelace", is_listed=True).exists()
        assert "Listed: Ada Lovelace" in out
        assert "Dry run" not in out

    def it_seeds_from_a_base64_argument():
        MemberFactory(full_legal_name="Ada Lovelace")
        encoded = base64.b64encode(json.dumps(ROSTER).encode("utf-8")).decode("ascii")
        out = _run(roster_b64=encoded)
        assert LeadershipListing.objects.count() == 1
        assert "Listed: Ada Lovelace" in out

    def it_writes_nothing_on_a_dry_run_and_says_so(tmp_path):
        MemberFactory(full_legal_name="Ada Lovelace")
        path = tmp_path / "roster.json"
        path.write_text(json.dumps(ROSTER), encoding="utf-8")
        out = _run(file=str(path), dry_run=True)
        assert LeadershipListing.objects.count() == 0
        assert "Listed: Ada Lovelace" in out
        assert "Dry run: nothing written." in out

    def it_requires_exactly_one_source():
        with pytest.raises(CommandError):
            _run()
        with pytest.raises(CommandError):
            _run(file="x.json", roster_b64="e30=")
