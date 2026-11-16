"""The FOG API's ``/api/v1/feedback/``: the Feedback inbox for a script or an agent (#1220)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast

from django.forms import Form

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from api.permissions import IsFogAdmin
from core.forms import FeedbackApiCommentForm, FeedbackApiUpdateForm
from core.models import FeedbackRequest, FeedbackRequestError, FeedbackRequestQuerySet
from core.serializers import FeedbackRequestSerializer

if TYPE_CHECKING:
    from django.contrib.auth.models import User

#: The ``?status=`` value for the inbox's Open view (:meth:`FeedbackRequestQuerySet.open`).
OPEN_FILTER = "open"


def _cleaned(form: Form) -> dict[str, Any]:
    """The form's cleaned data, or a 400 carrying its errors by field."""
    if not form.is_valid():
        raise ValidationError({field: list(errors) for field, errors in form.errors.items()})
    return form.cleaned_data


class FeedbackRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """``/api/v1/feedback/``: read and answer feedback requests, for fog admins only.

    Every write goes through the model's admin actions, so the inbox page's rules and
    notifications hold here unchanged: ``PATCH`` saves status, comment and GitHub link through
    :meth:`FeedbackRequest.apply_admin_update`, and ``POST <pk>/comment/`` posts through
    :meth:`FeedbackRequest.add_comment`. A refusal from the model is a 400 with its message.
    Reading a request does not mark it seen, as opening it in the inbox does.
    """

    permission_classes: ClassVar = [IsFogAdmin]
    serializer_class = FeedbackRequestSerializer
    http_method_names: ClassVar = ["get", "patch", "post", "head", "options"]

    def get_queryset(self) -> FeedbackRequestQuerySet:
        """Newest first, with sender and thread loaded; ``?status=open`` or ``?status=<status>`` narrows it."""
        queryset = FeedbackRequest.objects.select_related("user__member").with_thread().order_by("-created_at")
        wanted = self.request.query_params.get("status")
        if not wanted or self.action != "list":
            return queryset
        if wanted == OPEN_FILTER:
            return queryset.open()
        if wanted not in FeedbackRequest.Status.values:
            choices = ", ".join([OPEN_FILTER, *FeedbackRequest.Status.values])
            raise ValidationError({"status": f"Unknown status {wanted!r}. Use one of: {choices}."})
        return queryset.filter(status=wanted)

    def _fresh(self, feedback_request: FeedbackRequest) -> dict:
        """The request as saved now, thread included, in the read shape."""
        reloaded = self.get_queryset().get(pk=feedback_request.pk)
        return cast(dict, FeedbackRequestSerializer(reloaded, context=self.get_serializer_context()).data)

    def partial_update(self, request: Request, pk: str | None = None) -> Response:
        """Save any of ``status``, ``comment`` and ``github_issue_url``, as the inbox's Save does."""
        feedback_request = self.get_object()
        data = _cleaned(FeedbackApiUpdateForm(request.data, feedback_request=feedback_request))
        try:
            feedback_request.apply_admin_update(
                status=data["status"],
                comment=data["comment"],
                github_issue_url=data["github_issue_url"],
                actor=cast("User", request.user),
            )
        except FeedbackRequestError as refusal:
            raise ValidationError({"detail": str(refusal)}) from refusal
        return Response(self._fresh(feedback_request))

    @action(detail=True, methods=["post"])
    def comment(self, request: Request, pk: str | None = None) -> Response:
        """Post a text comment on the request's thread as the caller; the sender is always told."""
        feedback_request = self.get_object()
        data = _cleaned(FeedbackApiCommentForm(request.data))
        feedback_request.add_comment(author=cast("User", request.user), body=data["body"], photos=[])
        return Response(self._fresh(feedback_request), status=status.HTTP_201_CREATED)
