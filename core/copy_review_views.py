"""TEMPORARY — remove on/after 2026-08-10.

Public, unauthenticated JSON comment API for the copy-review gallery
(https://copy-review.pastlives.space). A ~2-week review aid, NOT a member-facing
feature. See docs/superpowers/plans/2026-07-27-copy-review-comments.md.

Plain Django ``JsonResponse`` function views (this project has no DRF and no
django-cors-headers), ``@csrf_exempt``, with manual CORS — mirrors the
push-subscription endpoints in ``core/views.py``. Business logic lives on
``CopyReviewComment`` / its manager; these views only parse the request, validate
via ``CopyReviewCommentForm``, call the model, and serialize.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .forms import CopyReviewCommentForm
from .models import CopyReviewComment

# Only the gallery's own origin may read the browser's response. Requests from any
# other origin still reach the view (CORS is a browser read-guard, not a server
# firewall) but get no Access-Control-Allow-Origin header, so the browser blocks
# the caller from seeing the body.
COPY_REVIEW_ALLOWED_ORIGINS = {"https://copy-review.pastlives.space"}

# Best-effort per-IP post cap. The unlisted URL + length caps + 2-week lifespan are
# the real guards; this just blunts a trivial flood.
_THROTTLE_MAX_POSTS = 30
_THROTTLE_WINDOW_SECONDS = 600


def _cors_headers(request: HttpRequest) -> dict[str, str]:
    """CORS headers for a copy-review response; reflects Origin only when allowed."""
    headers = {
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Vary": "Origin",
    }
    origin = request.headers.get("Origin", "")
    if origin in COPY_REVIEW_ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def _apply_cors(request: HttpRequest, response: HttpResponse) -> HttpResponse:
    """Stamp the CORS headers onto an existing response and return it."""
    for key, value in _cors_headers(request).items():
        response[key] = value
    return response


def cors_json(*methods: str) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Wrap a JSON view: answer OPTIONS preflight with 204, 405 unknown methods, always CORS.

    Args:
        methods: the non-OPTIONS HTTP methods the wrapped view handles.
    """
    allowed = set(methods) | {"OPTIONS"}

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @csrf_exempt
        @functools.wraps(view)
        def wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
            if request.method == "OPTIONS":
                return _apply_cors(request, HttpResponse(status=204))
            if request.method not in allowed:
                return _apply_cors(request, JsonResponse({"error": "Method not allowed"}, status=405))
            return _apply_cors(request, view(request, *args, **kwargs))

        return wrapper

    return decorator


def _parse_body(request: HttpRequest) -> dict[str, object] | HttpResponse:
    """Decode a JSON object body: the ``dict`` on success, else a 400 error response.

    Returning the response (a JsonResponse, which is an HttpResponse) rather than a
    (data, error) pair lets callers narrow the type with a single ``isinstance`` check.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    return data


def _throttled(request: HttpRequest) -> bool:
    """True when this client IP has exceeded the post cap in the rolling window."""
    key = f"copy-review-throttle:{request.META['REMOTE_ADDR']}"
    cache.add(key, 0, timeout=_THROTTLE_WINDOW_SECONDS)
    return cache.incr(key) > _THROTTLE_MAX_POSTS


@cors_json("GET", "POST")
def comments(request: HttpRequest) -> HttpResponse:
    """GET lists all active comments grouped by section; POST creates one."""
    if request.method == "GET":
        return _list_comments()
    return _create_comment(request)


def _list_comments() -> HttpResponse:
    """Serialize every active comment grouped by section_key. Never leaks edit_token."""
    sections = {key: [c.as_public_dict() for c in items] for key, items in CopyReviewComment.objects.grouped().items()}
    return JsonResponse({"sections": sections})


def _create_comment(request: HttpRequest) -> HttpResponse:
    """Validate and persist a new comment, returning its one-time edit_token."""
    data = _parse_body(request)
    if isinstance(data, HttpResponse):
        return data
    if data.get("website"):
        # Honeypot: a bot filled the hidden field. Report success, persist nothing.
        return JsonResponse({"ok": True})
    if _throttled(request):
        return JsonResponse({"error": "Too many comments. Please slow down."}, status=429)
    form = CopyReviewCommentForm(data=data)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)
    comment = CopyReviewComment.objects.post(
        section_key=form.cleaned_data["section"],
        author_name=form.cleaned_data["author_name"],
        body=form.cleaned_data["body"],
    )
    return JsonResponse({"comment": comment.as_public_dict(), "edit_token": comment.edit_token}, status=201)


def _load_owned_comment(request: HttpRequest, pk: int) -> tuple[CopyReviewComment, dict[str, object]] | HttpResponse:
    """Look up an active comment and confirm the body carries its edit_token.

    Returns ``(comment, data)`` on success, or an error response — 404 (missing),
    400 (bad JSON), or 403 (token mismatch). Returning the response directly lets
    callers narrow with one ``isinstance`` check. Shared by the edit and delete views.
    """
    comment = CopyReviewComment.objects.filter(pk=pk).first()
    if comment is None:
        return JsonResponse({"error": "Not found"}, status=404)
    data = _parse_body(request)
    if isinstance(data, HttpResponse):
        return data
    if not comment.owned_by(str(data.get("edit_token", ""))):
        return JsonResponse({"error": "Forbidden"}, status=403)
    return comment, data


@cors_json("POST")
def comment_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit a comment's author_name/body when the request proves ownership."""
    result = _load_owned_comment(request, pk)
    if isinstance(result, HttpResponse):
        return result
    comment, data = result
    form = CopyReviewCommentForm(
        data={
            "section": comment.section_key,
            "author_name": data.get("author_name", ""),
            "body": data.get("body", ""),
        }
    )
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)
    comment.apply_edit(form.cleaned_data["author_name"], form.cleaned_data["body"])
    return JsonResponse({"comment": comment.as_public_dict()})


@cors_json("POST")
def comment_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Soft-delete a comment when the request proves ownership."""
    result = _load_owned_comment(request, pk)
    if isinstance(result, HttpResponse):
        return result
    comment, _data = result
    comment.soft_delete()
    return JsonResponse({"ok": True})
