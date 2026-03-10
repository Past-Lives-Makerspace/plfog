from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def active_nav(context: dict, url_name: str, pk: int | None = None) -> str:  # type: ignore[type-arg]
    """Return 'active' if the current URL matches the given URL name."""
    request = context.get("request")
    if request is None:
        return ""
    from django.urls import reverse

    if pk is not None:
        target = reverse(url_name, args=[pk])
    else:
        target = reverse(url_name)
    return "active" if request.path == target else ""
