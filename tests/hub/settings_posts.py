"""POST bodies for Site Settings and the four pages split from it (#922), shaped as each page posts them.

Every page posts its whole form, so a spec that wants to change one field builds the full body
here and overrides that field. A checkbox left out of a body reads as off.
"""

from __future__ import annotations

from core.models import FeatureSwitch, ScheduledJobState, SiteConfiguration
from core.scheduled_jobs import SCHEDULED_JOBS


def _management(prefix: str, count: int) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(count),
        f"{prefix}-INITIAL_FORMS": str(count),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def site_settings_post(**overrides: str) -> dict[str, str]:
    """A valid Site Settings save from the General tab, with no calendar feeds."""
    data = {
        "org_name": "Past Lives Makerspace",
        "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        "classes_calendar_color": "#abcdef",
        "submitted_tab": "general",
        **_management("feeds", 0),
    }
    data.update(overrides)
    return data


def feature_formset_post() -> dict[str, str]:
    """Every feature row as it stands, in the formset's order (``Meta.ordering = feature_key``)."""
    FeatureSwitch.objects.sync_registry()
    rows = list(FeatureSwitch.objects.all())
    data = _management("features", len(rows))
    for index, row in enumerate(rows):
        data[f"features-{index}-id"] = str(row.pk)
        data[f"features-{index}-state"] = row.state
        data[f"features-{index}-message"] = row.message
    return data


def features_post(**overrides: str) -> dict[str, str]:
    """A valid Manage Features save: the two required hours, every feature row, switches off."""
    data = {"late_cancel_notice_hours": "24", "late_cancel_grace_hours": "2", **feature_formset_post()}
    data.update(overrides)
    return data


def jobstate_formset_post() -> dict[str, str]:
    """Every toggleable job's switch, on, in the formset's order."""
    ScheduledJobState.objects.sync_registry()
    toggleable = [job.key for job in SCHEDULED_JOBS if job.toggleable]
    rows = list(ScheduledJobState.objects.filter(task_key__in=toggleable).order_by("task_key"))
    data = _management("jobstates", len(rows))
    for index, row in enumerate(rows):
        data[f"jobstates-{index}-id"] = str(row.pk)
        data[f"jobstates-{index}-enabled"] = "on"
    return data
