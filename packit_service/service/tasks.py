# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from collections import OrderedDict
from datetime import timedelta
from typing import Union

from packit_service.celerizer import celery_app
from packit_service.constants import (
    USAGE_CURRENT_DATE,
    USAGE_DATE_IN_THE_PAST,
)
from packit_service.models import (
    get_onboarded_projects,
    get_usage_data,
)

logger = logging.getLogger(__name__)


# format the chart needs is a list of {"x": "datetimelegend", "y": value}
CHART_DATA_TYPE = list[dict[str, Union[str, int]]]


@celery_app.task(ignore_result=False)
def get_usage_interval_data(
    days: int,
    hours: int,
    count: int,
) -> dict[str, Union[str, CHART_DATA_TYPE, dict[str, CHART_DATA_TYPE]]]:
    """
    :param days: number of days for the interval length
    :param hours: number of days for the interval length
    :param count: number of intervals
    :return: usage data for the COUNT number of intervals
      (delta is DAYS number of days and HOURS number of hours)
    """
    delta = timedelta(days=days, hours=hours)

    current_date = USAGE_CURRENT_DATE
    days_legend = []
    for _ in range(count):
        days_legend.append(current_date)
        current_date -= delta

    result_jobs: dict[str, CHART_DATA_TYPE] = {}
    result_jobs_project_count: dict[str, CHART_DATA_TYPE] = {}
    result_jobs_project_cumulative_count: dict[str, CHART_DATA_TYPE] = {}
    result_events: dict[str, CHART_DATA_TYPE] = {}
    result_active_projects: CHART_DATA_TYPE = []
    result_active_projects_cumulative: CHART_DATA_TYPE = []

    logger.warn(
        f"Getting usage data datetime_from {USAGE_DATE_IN_THE_PAST} datetime_to {days_legend[-1]}",
    )
    past_data = get_usage_data(
        datetime_from=USAGE_DATE_IN_THE_PAST,
        datetime_to=days_legend[-1],
        top=100000,
    )
    logger.warn("Got usage data ")
    cumulative_projects_past = set(
        past_data["active_projects"]["top_projects_by_events_handled"].keys(),
    )
    cumulative_projects = cumulative_projects_past.copy()
    cumulative_projects_for_jobs_past = {
        job: set(data["top_projects_by_job_runs"].keys()) for job, data in past_data["jobs"].items()
    }
    cumulative_projects_for_jobs = cumulative_projects_for_jobs_past.copy()

    for day in reversed(days_legend):
        day_from = (day - delta).isoformat()
        day_to = day.isoformat()
        legend = day.strftime("%H:%M" if (hours and not days) else "%Y-%m-%d")

        interval_result = get_usage_data(
            datetime_from=day_from,
            datetime_to=day_to,
            top=100000,
        )

        for job, data in interval_result["jobs"].items():
            result_jobs.setdefault(job, [])
            result_jobs[job].append({"x": legend, "y": data["job_runs"]})
            result_jobs_project_count.setdefault(job, [])
            result_jobs_project_count[job].append(
                {"x": legend, "y": len(data["top_projects_by_job_runs"])},
            )

            cumulative_projects_for_jobs[job] |= data["top_projects_by_job_runs"].keys()
            result_jobs_project_cumulative_count.setdefault(job, [])
            result_jobs_project_cumulative_count[job].append(
                {"x": legend, "y": len(cumulative_projects_for_jobs[job])},
            )

        for event, data in interval_result["events"].items():
            result_events.setdefault(event, [])
            result_events[event].append({"x": legend, "y": data["events_handled"]})

        result_active_projects.append(
            {"x": legend, "y": interval_result["active_projects"].get("project_count")},
        )
        cumulative_projects |= interval_result["active_projects"][
            "top_projects_by_events_handled"
        ].keys()
        result_active_projects_cumulative.append(
            {"x": legend, "y": len(cumulative_projects)},
        )

    onboarded_projects_per_job = {}
    for job in past_data["jobs"]:
        onboarded_projects_per_job[job] = list(
            cumulative_projects_for_jobs[job] - cumulative_projects_for_jobs_past[job],
        )

    return {
        "jobs": result_jobs,
        "jobs_project_count": result_jobs_project_count,
        "jobs_project_cumulative_count": result_jobs_project_cumulative_count,
        "events": result_events,
        "from": days_legend[0].isoformat(),
        "to": days_legend[-1].isoformat(),
        "active_projects": result_active_projects,
        "active_projects_cumulative": result_active_projects_cumulative,
        "onboarded_projects": list(cumulative_projects - cumulative_projects_past),
        "onboarded_projects_per_job": onboarded_projects_per_job,
    }


def calculate_onboarded_projects():
    onboarded, almost_onboarded = get_onboarded_projects()

    return {"onboarded": onboarded, "almost_onboarded": almost_onboarded}


@celery_app.task(ignore_result=False)
def get_past_usage_data(datetime_from=None, datetime_to=None, top=5):
    # Even though frontend expects only the first N (=5) to be present
    # in the project lists, we need to get all to calculate the number
    # of active projects.
    # (This info will be added to the payload for frontend.)
    # The original `top` argument will be used later
    # to get the expected number of projects in the response.
    top_all_project = 100000

    onboarded = get_onboarded_projects()[0]
    num_of_onboarded_projects = len(onboarded)

    raw_result = get_usage_data(
        datetime_from=datetime_from,
        datetime_to=datetime_to,
        top=top_all_project,
    )
    return {
        "active_projects": raw_result["active_projects"],
        "jobs": {
            job: {
                "job_runs": data["job_runs"],
                "top_projects_by_job_runs": dict(
                    list(OrderedDict(data["top_projects_by_job_runs"]).items())[:top],
                ),
                "active_projects": len(data["top_projects_by_job_runs"]),
            }
            for job, data in raw_result["jobs"].items()
        },
        "onboarded_projects_q1_2024": num_of_onboarded_projects,
    }
