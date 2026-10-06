"""BDD specs for parse_gallery_focus, the body check behind the gallery Set focus route."""

from __future__ import annotations

import json

import pytest
from django.core.exceptions import ValidationError

from classes.forms import GALLERY_FOCUS_INVALID, parse_gallery_focus


def _body(payload: object) -> bytes:
    return json.dumps(payload).encode()


def describe_parse_gallery_focus():
    def it_returns_the_point():
        assert parse_gallery_focus(_body({"x": 12, "y": 88})) == (12, 88)

    @pytest.mark.parametrize(("x", "y"), [(0, 0), (100, 100), (0, 100)])
    def it_takes_both_ends_of_the_range(x, y):
        assert parse_gallery_focus(_body({"x": x, "y": y})) == (x, y)

    def it_returns_none_for_two_nulls():
        assert parse_gallery_focus(_body({"x": None, "y": None})) is None

    @pytest.mark.parametrize(
        "body",
        [
            b"not json",
            b"\xff\xfe\x00",
            _body([12, 88]),
            _body(None),
            _body({"x": 12}),
            _body({"y": 88}),
            _body({}),
            _body({"x": 12, "y": 88, "z": 1}),
            _body({"x": "12", "y": 88}),
            _body({"x": 12, "y": "88"}),
            _body({"x": 12.5, "y": 88}),
            _body({"x": 12, "y": 88.0}),
            _body({"x": True, "y": 88}),
            _body({"x": 12, "y": False}),
            _body({"x": -1, "y": 50}),
            _body({"x": 50, "y": -1}),
            _body({"x": 101, "y": 50}),
            _body({"x": 50, "y": 101}),
            _body({"x": None, "y": 50}),
            _body({"x": 50, "y": None}),
        ],
    )
    def it_refuses_anything_else(body):
        with pytest.raises(ValidationError) as exc:
            parse_gallery_focus(body)
        assert exc.value.messages == [GALLERY_FOCUS_INVALID]

    def it_says_what_to_send_without_a_dash():
        assert "-" not in GALLERY_FOCUS_INVALID
        assert "—" not in GALLERY_FOCUS_INVALID
