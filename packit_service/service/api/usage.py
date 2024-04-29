# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from logging import getLogger
from typing import Any, Union

from cachetools.func import ttl_cache

from flask import request, escape
from flask_restx import Namespace, Resource

from packit_service.models import (
    CoprBuildGroupModel,
    GitProjectModel,
    ProjectEventModelType,
    KojiBuildGroupModel,
    SRPMBuildModel,
    SyncReleaseModel,
    TFTTestRunGroupModel,
    VMImageBuildTargetModel,
    BodhiUpdateTargetModel,
    KojiBuildTargetModel,
    SyncReleaseTargetModel,
    get_usage_data,
)
from packit_service.service.api.utils import response_maker
from packit_service.constants import (
    USAGE_CURRENT_DATE,
    USAGE_DATE_IN_THE_PAST,
    USAGE_DATE_IN_THE_PAST_STR,
    USAGE_PAST_DAY_DATE_STR,
    USAGE_PAST_WEEK_DATE_STR,
    USAGE_PAST_MONTH_DATE_STR,
    USAGE_PAST_YEAR_DATE_STR,
)

logger = getLogger("packit_service")

usage_ns = Namespace("usage", description="Data about Packit usage")

_CACHE_MAXSIZE = 100  # can it be removed, should things being cached already lower?


@usage_ns.route("")
class Usage(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @usage_ns.response(HTTPStatus.BAD_REQUEST, "Timestamps are in wrong format")
    def get(self):
        """
        Show a usage statistics for the service.

        You can use `from` and `to` arguments to specify a time range
        (e.g. `/api/usage?from=2022-01-30`).
        Also, you can use `top` argument to specify number of project
        in the top_projects_by_something parts of the response.
        """

        top = int(request.args.get("top")) if "top" in request.args else None

        errors, datetime_from, datetime_to = process_timestamps(
            request.args.get("from"),
            request.args.get("to"),
        )
        if errors:
            return response_maker({"errors": errors}, status=HTTPStatus.BAD_REQUEST)

        result = get_usage_data(datetime_from, datetime_to, top)

        return response_maker(result)


@usage_ns.route("/project/<forge>/<namespace>/<repo_name>")
@usage_ns.param("forge", "Git Forge")
@usage_ns.param("namespace", "Namespace")
@usage_ns.param("repo_name", "Repo Name")
class ProjectUsage(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @usage_ns.response(HTTPStatus.BAD_REQUEST, "Timestamps are in wrong format")
    def get(self, forge, namespace, repo_name):
        """
        Show a usage statistics for a given project.

        You can use `from` and `to` arguments to specify a time range
        (e.g. `api/usage/project/github.com/packit/ogr?from=2022-01-30`).
        """

        errors, datetime_from, datetime_to = process_timestamps(
            request.args.get("from"),
            request.args.get("to"),
        )
        if errors:
            return response_maker({"errors": errors}, status=HTTPStatus.BAD_REQUEST)

        project_url = f"https://{forge}/{namespace}/{repo_name}"
        result = get_project_usage_data(project_url, datetime_from, datetime_to)

        return response_maker(result)


def __parse_timestamp(stamp):
    try:
        parsed_stamp = datetime.fromisoformat(stamp)
        parsed_stamp = parsed_stamp.astimezone(timezone.utc)
        return (None, parsed_stamp.isoformat())
    except TypeError:
        # we have gotten a None which means no start
        return (None, None)
    except ValueError:
        return ("invalid format", None)


def process_timestamps(start, end):
    """
    Process timestamps passed through the request.

    Args:
      start: Start of the time period. Can be `None` if no start is
        specified.
      end: End of the time period. Can be `None` if no end is specified.

    Returns:
        Triplet representing (in this order):
        * List of errors that happened during the parsing of the timestamps.
        * Start of the time period that can be directly passed to the DB.
        * End of the time period that can be directly passed to the DB.
    """
    (start_error, parsed_start) = __parse_timestamp(start)
    (end_error, parsed_end) = __parse_timestamp(end)

    errors = []
    if start_error:
        errors.append(f"From timestamp: {start_error}")
    if end_error:
        errors.append(f"To timestamp: {end_error}")

    return (errors, parsed_start, parsed_end)


def get_project_usage_data(project: str, datetime_from=None, datetime_to=None):
    """
    Return usage data for a given project:

    Example:
    ```
    >>> safe_dump(get_project_usage_data("https://github.com/packit/ogr"))
    events_handled:
      events_handled: 270
      per_event:
        branch_push:
          events_handled: 3
          position: 2
        issue:
          events_handled: 2
          position: 4
        pull_request:
          events_handled: 232
          position: 24
        release:
          events_handled: 33
          position: 3
      position: 21
    jobs:
      copr_build_targets:
        job_runs: 3413
        per_event:
          branch_push:
            job_runs: 515
            position: 17
          issue:
            job_runs: null
            position: null
          pull_request:
            job_runs: 2794
            position: 28
          release:
            job_runs: 104
            position: 3
        position: 27
      koji_build_targets:
        job_runs: 509
        per_event:
          branch_push:
            job_runs: null
            position: null
          issue:
            job_runs: null
            position: null
          pull_request:
            job_runs: 509
            position: 1
          release:
            job_runs: null
            position: null
        position: 1
      srpm_builds:
        job_runs: 1196
        per_event:
          branch_push:
            job_runs: 147
            position: 14
          issue:
            job_runs: null
            position: null
          pull_request:
            job_runs: 1015
            position: 19
          release:
            job_runs: 34
            position: 3
        position: 19
      sync_release_runs:
        job_runs: 16
        per_event:
          branch_push:
            job_runs: null
            position: null
          issue:
            job_runs: 2
            position: 4
          pull_request:
            job_runs: null
            position: null
          release:
            job_runs: 14
            position: 11
        position: 9
      tft_test_run_targets:
        job_runs: 2755
        per_event:
          branch_push:
            job_runs: 3
            position: 12
          issue:
            job_runs: null
            position: null
          pull_request:
            job_runs: 2748
            position: 12
          release:
            job_runs: 4
            position: 5
        position: 12
      vm_image_build_targets:
        job_runs: 2
        per_event:
          branch_push:
            job_runs: null
            position: null
          issue:
            job_runs: null
            position: null
          pull_request:
            job_runs: 2
            position: 1
          release:
            job_runs: null
            position: null
        position: 1
    ```
    """
    jobs: dict[str, Any] = {}
    for job_model in [
        SRPMBuildModel,
        CoprBuildGroupModel,
        KojiBuildGroupModel,
        VMImageBuildTargetModel,
        TFTTestRunGroupModel,
        SyncReleaseModel,
    ]:
        job_name: str = job_model.__tablename__  # type: ignore
        jobs[job_name] = get_result_dictionary(
            project,
            top_projects=GitProjectModel.get_job_usage_numbers_all_project_events(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                top=None,
                job_result_model=job_model,
            ),
            count_name="job_runs",
        )

        jobs[job_name]["per_event"] = {}
        for project_event_type in ProjectEventModelType:
            jobs[job_name]["per_event"][
                project_event_type.value
            ] = get_result_dictionary(
                project,
                top_projects=GitProjectModel.get_job_usage_numbers(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    top=None,
                    job_result_model=job_model,
                    project_event_type=project_event_type,
                ),
                count_name="job_runs",
            )

    events_handled: dict[str, Any] = get_result_dictionary(
        project=project,
        top_projects=GitProjectModel.get_active_projects_usage_numbers(
            datetime_from=datetime_from, datetime_to=datetime_to, top=None
        ),
        count_name="events_handled",
    )
    events_handled["per_event"] = {
        project_event_type.value: get_result_dictionary(
            project=project,
            top_projects=GitProjectModel.get_project_event_usage_numbers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                top=None,
                project_event_type=project_event_type,
            ),
            count_name="events_handled",
        )
        for project_event_type in ProjectEventModelType
    }

    return dict(
        events_handled=events_handled,
        jobs=jobs,
    )


def get_result_dictionary(
    project,
    top_projects,
    position_name="position",
    count_name="count",
) -> dict[str, int]:
    position = (
        list(top_projects.keys()).index(project) + 1
        if project in top_projects
        else None
    )
    return {position_name: position, count_name: top_projects.get(project)}


@usage_ns.route("/onboarded-projects")
class Onboarded2024Q1(Resource):
    @usage_ns.response(
        HTTPStatus.OK,
        "Onboarded projects for which exist a Bodhi update or a Koji build or a Packit merged PR.",
    )
    @classmethod
    def calculate(cls):
        known_onboarded_projects = (
            GitProjectModel.get_known_onboarded_downstream_projects()
        )

        bodhi_updates = BodhiUpdateTargetModel.get_all_projects()
        koji_builds = KojiBuildTargetModel.get_all_projects()
        onboarded_projects = bodhi_updates.union(koji_builds).union(
            known_onboarded_projects
        )

        # find **downstream git projects** with a PR created by Packit
        downstream_synced_projects = (
            SyncReleaseTargetModel.get_all_downstream_projects()
        )
        # if there exist a downstream Packit PR we are not sure it has been
        # merged, the project is *almost onboarded* until the PR is merged
        # (unless we already know it has a koji build or bodhi update, then
        # we don't need to check for a merged PR - it obviously has one)
        almost_onboarded_projects = downstream_synced_projects.difference(
            onboarded_projects
        )
        # do not re-check projects we already checked and we know they
        # have a merged Packit PR
        recheck_if_onboarded = almost_onboarded_projects.difference(
            known_onboarded_projects
        )

        onboarded = {
            project.id: project.project_url
            for project in onboarded_projects.union(known_onboarded_projects)
        }
        almost_onboarded = {
            project.id: project.project_url
            for project in recheck_if_onboarded.difference(onboarded_projects)
        }

        return {"onboarded": onboarded, "almost_onboarded": almost_onboarded}

    @classmethod
    def get_num_of_onboarded_projects(cls):
        return len(cls.calculate()["onboarded"])

    def get(self):
        """
        Returns a list of onboarded projects for which exist at least a
        Bodhi update, a downstream Koji build or a merged Packit PR.

        The data for the response is taken from the database but a long running
        task is spawned in the mean time, and the new long running task will
        look for new onboarded packages.
        If you re-call this endpoint a few minutes later the result may be different.

        Examples:
        /api/usage/onboarded-projects
        """
        return self.calculate()


def _get_past_usage_data(datetime_from=None, datetime_to=None, top=5):
    # Even though frontend expects only the first N (=5) to be present
    # in the project lists, we need to get all to calculate the number
    # of active projects.
    # (This info will be added to the payload for frontend.)
    # The original `top` argument will be used later
    # to get the expected number of projects in the response.
    top_all_project = 100000

    raw_result = get_usage_data(
        datetime_from=datetime_from, datetime_to=datetime_to, top=top_all_project
    )
    return response_maker(
        {
            "active_projects": raw_result["active_projects"],
            "jobs": {
                job: {
                    "job_runs": data["job_runs"],
                    "top_projects_by_job_runs": dict(
                        list(OrderedDict(data["top_projects_by_job_runs"]).items())[
                            :top
                        ]
                    ),
                    "active_projects": len(data["top_projects_by_job_runs"]),
                }
                for job, data in raw_result["jobs"].items()
            },
            "onboarded_projects_q1_2024": Onboarded2024Q1.get_num_of_onboarded_projects(),
        }
    )


@usage_ns.route("/past-day")
class UsagePastDay(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(hours=1).total_seconds())
    def get(self):
        return _get_past_usage_data(datetime_from=USAGE_PAST_DAY_DATE_STR)


@usage_ns.route("/past-week")
class UsagePastWeek(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(hours=1).total_seconds())
    def get(self):
        return _get_past_usage_data(datetime_from=USAGE_PAST_WEEK_DATE_STR)


@usage_ns.route("/past-month")
class UsagePastMonth(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(days=1).total_seconds())
    def get(self):
        return _get_past_usage_data(datetime_from=USAGE_PAST_MONTH_DATE_STR)


@usage_ns.route("/past-year")
class UsagePastYear(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(days=1).total_seconds())
    def get(self):
        return _get_past_usage_data(datetime_from=USAGE_PAST_YEAR_DATE_STR)


@usage_ns.route("/total")
class UsageTotal(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(days=1).total_seconds())
    def get(self):
        return _get_past_usage_data(datetime_from=USAGE_DATE_IN_THE_PAST_STR)


# format the chart needs is a list of {"x": "datetimelegend", "y": value}
CHART_DATA_TYPE = list[dict[str, Union[str, int]]]


@ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(hours=1).total_seconds())
def _get_usage_interval_data(
    days: int, hours: int, count: int
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
        f"Getting usage data datetime_from {USAGE_DATE_IN_THE_PAST} datetime_to {days_legend[-1]}"
    )
    past_data = get_usage_data(
        datetime_from=USAGE_DATE_IN_THE_PAST, datetime_to=days_legend[-1], top=100000
    )
    logger.warn("Got usage data ")
    cumulative_projects_past = set(
        past_data["active_projects"]["top_projects_by_events_handled"].keys()
    )
    cumulative_projects = cumulative_projects_past.copy()
    cumulative_projects_for_jobs_past = {
        job: set(data["top_projects_by_job_runs"].keys())
        for job, data in past_data["jobs"].items()
    }
    cumulative_projects_for_jobs = cumulative_projects_for_jobs_past.copy()

    for day in reversed(days_legend):
        day_from = (day - delta).isoformat()
        day_to = day.isoformat()
        legend = day.strftime("%H:%M" if (hours and not days) else "%Y-%m-%d")

        interval_result = get_usage_data(
            datetime_from=day_from, datetime_to=day_to, top=100000
        )

        for job, data in interval_result["jobs"].items():
            result_jobs.setdefault(job, [])
            result_jobs[job].append({"x": legend, "y": data["job_runs"]})
            result_jobs_project_count.setdefault(job, [])
            result_jobs_project_count[job].append(
                {"x": legend, "y": len(data["top_projects_by_job_runs"])}
            )

            cumulative_projects_for_jobs[job] |= data["top_projects_by_job_runs"].keys()
            result_jobs_project_cumulative_count.setdefault(job, [])
            result_jobs_project_cumulative_count[job].append(
                {"x": legend, "y": len(cumulative_projects_for_jobs[job])}
            )

        for event, data in interval_result["events"].items():
            result_events.setdefault(event, [])
            result_events[event].append({"x": legend, "y": data["events_handled"]})

        result_active_projects.append(
            {"x": legend, "y": interval_result["active_projects"].get("project_count")}
        )
        cumulative_projects |= interval_result["active_projects"][
            "top_projects_by_events_handled"
        ].keys()
        result_active_projects_cumulative.append(
            {"x": legend, "y": len(cumulative_projects)}
        )

    onboarded_projects_per_job = {}
    for job, data in past_data["jobs"].items():
        onboarded_projects_per_job[job] = list(
            cumulative_projects_for_jobs[job] - cumulative_projects_for_jobs_past[job]
        )

    return response_maker(
        {
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
    )


@usage_ns.route("/intervals")
class UsageIntervals(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    def get(self):
        """
        Returns the data for trend charts.

        Use `days` and `hours` parameters to define interval and `count` to set number of intervals.

        Examples:
        /api/usage/intervals/past?days=7&hours=0&count=52 for the weekly data of the last year
        /api/usage/intervals?days=0&hours=1&count=24 for the hourly data of the last day
        """
        count = int(escape(request.args.get("count", "10")))
        delta_hours = int(escape(request.args.get("hours", "0")))
        delta_days = int(escape(request.args.get("days", "0")))
        return _get_usage_interval_data(hours=delta_hours, days=delta_days, count=count)
