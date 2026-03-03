from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from education.models import ClassSession, ScheduledOrientation
from membership.models import Guild
from outreach.models import Event

GUILD_COLOR_PALETTE = [
    "#3b82f6",
    "#ef4444",
    "#10b981",
    "#f59e0b",
    "#8b5cf6",
    "#ec4899",
    "#06b6d4",
    "#84cc16",
    "#f97316",
    "#6366f1",
    "#14b8a6",
    "#e11d48",
    "#0ea5e9",
    "#a855f7",
]

FALLBACK_COLOR = "#6b7280"


def _build_guild_colors() -> dict[int, str]:
    """Assign a stable color from the palette to each active guild, ordered by name."""
    return {
        g.pk: GUILD_COLOR_PALETTE[i % len(GUILD_COLOR_PALETTE)]
        for i, g in enumerate(Guild.objects.filter(is_active=True).order_by("name"))
    }


def health_check(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "home.html")


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "membership/dashboard.html")


def calendar_events(request: HttpRequest) -> JsonResponse:
    """Return FullCalendar-format JSON for events, class sessions, and orientations."""
    start = request.GET.get("start")
    end = request.GET.get("end")
    guild_slug = request.GET.get("guild")

    if not start or not end:
        return JsonResponse([], safe=False)

    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)

    guild_colors = _build_guild_colors()
    events_out: list[dict] = []

    # Published events within the date range
    event_qs = Event.objects.filter(starts_at__gte=start_dt, starts_at__lte=end_dt, is_published=True)
    if guild_slug:
        event_qs = event_qs.filter(guild__slug=guild_slug)
    for e in event_qs.select_related("guild"):
        color = guild_colors.get(e.guild_id, FALLBACK_COLOR) if e.guild_id else FALLBACK_COLOR
        events_out.append(
            {
                "title": e.name,
                "start": e.starts_at.isoformat(),
                "end": e.ends_at.isoformat(),
                "color": color,
                "type": "event",
                "guild_name": e.guild.name if e.guild else "Past Lives",
                "url": "",
            }
        )

    # Published class sessions within the date range
    session_qs = ClassSession.objects.filter(
        starts_at__gte=start_dt,
        starts_at__lte=end_dt,
        maker_class__status="published",
    )
    if guild_slug:
        session_qs = session_qs.filter(maker_class__guild__slug=guild_slug)
    for s in session_qs.select_related("maker_class__guild"):
        mc = s.maker_class
        color = guild_colors.get(mc.guild_id, FALLBACK_COLOR) if mc.guild_id else FALLBACK_COLOR
        events_out.append(
            {
                "title": mc.name,
                "start": s.starts_at.isoformat(),
                "end": s.ends_at.isoformat(),
                "color": color,
                "type": "class",
                "guild_name": mc.guild.name if mc.guild else "Past Lives",
                "url": "",
            }
        )

    # Non-cancelled scheduled orientations within the date range
    orient_qs = ScheduledOrientation.objects.filter(
        scheduled_at__gte=start_dt,
        scheduled_at__lte=end_dt,
    ).exclude(status="cancelled")
    if guild_slug:
        orient_qs = orient_qs.filter(orientation__guild__slug=guild_slug)
    for so in orient_qs.select_related("orientation__guild"):
        o = so.orientation
        end_time = so.scheduled_at + timedelta(minutes=o.duration_minutes)
        color = guild_colors.get(o.guild_id, FALLBACK_COLOR) if o.guild_id else FALLBACK_COLOR
        events_out.append(
            {
                "title": o.name,
                "start": so.scheduled_at.isoformat(),
                "end": end_time.isoformat(),
                "color": color,
                "type": "orientation",
                "guild_name": o.guild.name if o.guild else "Past Lives",
                "url": "",
            }
        )

    return JsonResponse(events_out, safe=False)
