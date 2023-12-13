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
)
from packit_service.service.api.utils import response_maker

logger = getLogger("packit_service")

usage_ns = Namespace("usage", description="Data about Packit usage")

_CACHE_MAXSIZE = 100

__now = datetime.now()
_DATE_IN_THE_PAST = __now.replace(year=__now.year - 100)


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


def get_usage_data(datetime_from=None, datetime_to=None, top=10):
    """
    Get usage data.

    Example:
    ```
    >>> safe_dump(get_usage_data(top=3))
    active_projects:
      instances:
        github.com: 279
        gitlab.com: 3
        gitlab.freedesktop.org: 3
        gitlab.gnome.org: 2
      project_count: 287
      top_projects_by_events_handled:
        https://github.com/avocado-framework/avocado: 1327
        https://github.com/cockpit-project/cockpit: 1829
        https://github.com/systemd/systemd: 4960
    all_projects:
      instances:
        git.centos.org: 25
        github.com: 7855
        gitlab.com: 8
        gitlab.freedesktop.org: 4
        gitlab.gnome.org: 2
        src.fedoraproject.org: 22175
      project_count: 30069
    events:
      branch_push:
        events_handled: 115
        top_projects:
          https://github.com/packit/ogr: 3
          https://github.com/packit/packit: 3
          https://github.com/rhinstaller/anaconda: 3
      issue:
        events_handled: 18
        top_projects:
          https://github.com/martinpitt/python-dbusmock: 2
          https://github.com/packit/packit: 3
          https://github.com/packit/specfile: 3
      pull_request:
        events_handled: 26605
        top_projects:
          https://github.com/avocado-framework/avocado: 1327
          https://github.com/cockpit-project/cockpit: 1808
          https://github.com/systemd/systemd: 4960
      release:
        events_handled: 425
        top_projects:
          https://github.com/facebook/folly: 40
          https://github.com/packit/ogr: 33
          https://github.com/packit/packit: 57
    jobs:
      copr_build_targets:
        job_runs: 530955
        per_event:
          branch_push:
            job_runs: 48160
            top_projects_by_job_runs:
              https://github.com/osandov/drgn: 5812
              https://github.com/osbuild/osbuild: 7078
              https://github.com/osbuild/osbuild-composer: 12847
          issue:
            job_runs: 0
            top_projects_by_job_runs: {}
          pull_request:
            job_runs: 481561
            top_projects_by_job_runs:
              https://github.com/osbuild/osbuild: 31108
              https://github.com/osbuild/osbuild-composer: 93939
              https://github.com/systemd/systemd: 60158
          release:
            job_runs: 1234
            top_projects_by_job_runs:
              https://github.com/facebook/folly: 340
              https://github.com/packit/ogr: 104
              https://github.com/packit/packit: 174
        top_projects_by_job_runs:
          https://github.com/osbuild/osbuild: 38186
          https://github.com/osbuild/osbuild-composer: 106786
          https://github.com/systemd/systemd: 60158
      koji_build_targets:
        job_runs: 1466
        per_event:
          branch_push:
            job_runs: 56
            top_projects_by_job_runs:
              https://github.com/besser82/libxcrypt: 46
              https://github.com/ostreedev/ostree: 10
          issue:
            job_runs: 0
            top_projects_by_job_runs: {}
          pull_request:
            job_runs: 1410
            top_projects_by_job_runs:
              https://github.com/containers/podman: 297
              https://github.com/packit/ogr: 509
              https://github.com/rear/rear: 267
          release:
            job_runs: 0
            top_projects_by_job_runs: {}
        top_projects_by_job_runs:
          https://github.com/containers/podman: 297
          https://github.com/packit/ogr: 509
          https://github.com/rear/rear: 267
      srpm_builds:
        job_runs: 103695
        per_event:
          branch_push:
            job_runs: 7084
            top_projects_by_job_runs:
              https://github.com/osbuild/osbuild-composer: 646
              https://github.com/packit/packit: 549
              https://github.com/rhinstaller/anaconda: 1015
          issue:
            job_runs: 0
            top_projects_by_job_runs: {}
          pull_request:
            job_runs: 96305
            top_projects_by_job_runs:
              https://github.com/cockpit-project/cockpit: 6915
              https://github.com/packit/hello-world: 10401
              https://github.com/systemd/systemd: 14489
          release:
            job_runs: 306
            top_projects_by_job_runs:
              https://github.com/facebook/folly: 40
              https://github.com/packit/ogr: 34
              https://github.com/packit/packit: 54
        top_projects_by_job_runs:
          https://github.com/cockpit-project/cockpit: 6937
          https://github.com/packit/hello-world: 10409
          https://github.com/systemd/systemd: 14489
      sync_release_runs:
        job_runs: 419
        per_event:
          branch_push:
            job_runs: 0
            top_projects_by_job_runs: {}
          issue:
            job_runs: 22
            top_projects_by_job_runs:
              https://github.com/martinpitt/python-dbusmock: 3
              https://github.com/packit/packit: 3
              https://github.com/packit/specfile: 6
          pull_request:
            job_runs: 0
            top_projects_by_job_runs: {}
          release:
            job_runs: 397
            top_projects_by_job_runs:
              https://github.com/martinpitt/python-dbusmock: 35
              https://github.com/packit/packit: 35
              https://github.com/rhinstaller/anaconda: 34
        top_projects_by_job_runs:
          https://github.com/martinpitt/python-dbusmock: 38
          https://github.com/packit/packit: 38
          https://github.com/rhinstaller/anaconda: 34
      tft_test_run_targets:
        job_runs: 150525
        per_event:
          branch_push:
            job_runs: 441
            top_projects_by_job_runs:
              https://github.com/oamg/convert2rhel: 209
              https://github.com/packit-service/packit: 50
              https://github.com/python-bugzilla/python-bugzilla: 88
          issue:
            job_runs: 0
            top_projects_by_job_runs: {}
          pull_request:
            job_runs: 150026
            top_projects_by_job_runs:
              https://github.com/cockpit-project/cockpit: 21157
              https://github.com/oamg/convert2rhel: 15297
              https://github.com/teemtee/tmt: 22136
          release:
            job_runs: 58
            top_projects_by_job_runs:
              https://github.com/fedora-infra/fedora-messaging: 8
              https://github.com/fedora-iot/zezere: 8
              https://github.com/psss/tmt: 21
        top_projects_by_job_runs:
          https://github.com/cockpit-project/cockpit: 21157
          https://github.com/oamg/convert2rhel: 15506
          https://github.com/teemtee/tmt: 22136
      vm_image_build_targets:
        job_runs: 2
        per_event:
          branch_push:
            job_runs: 0
            top_projects_by_job_runs: {}
          issue:
            job_runs: 0
            top_projects_by_job_runs: {}
          pull_request:
            job_runs: 2
            top_projects_by_job_runs:
              https://github.com/packit/ogr: 2
          release:
            job_runs: 0
            top_projects_by_job_runs: {}
        top_projects_by_job_runs:
          https://github.com/packit/ogr: 2

    ```
    """
    jobs = {}
    for job_model in [
        SRPMBuildModel,
        CoprBuildGroupModel,
        KojiBuildGroupModel,
        VMImageBuildTargetModel,
        TFTTestRunGroupModel,
        SyncReleaseModel,
    ]:
        jobs[job_model.__tablename__] = dict(
            job_runs=GitProjectModel.get_job_usage_numbers_count_all_project_events(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                job_result_model=job_model,
            ),
            top_projects_by_job_runs=GitProjectModel.get_job_usage_numbers_all_project_events(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                top=top,
                job_result_model=job_model,
            ),
        )
        jobs[job_model.__tablename__]["per_event"] = {}
        jobs[job_model.__tablename__]["per_event"].update(
            {
                project_event_type.value: dict(
                    job_runs=GitProjectModel.get_job_usage_numbers_count(
                        datetime_from=datetime_from,
                        datetime_to=datetime_to,
                        job_result_model=job_model,
                        project_event_type=project_event_type,
                    ),
                    top_projects_by_job_runs=GitProjectModel.get_job_usage_numbers(
                        datetime_from=datetime_from,
                        datetime_to=datetime_to,
                        top=top,
                        job_result_model=job_model,
                        project_event_type=project_event_type,
                    ),
                )
                for project_event_type in ProjectEventModelType
            }
        )

    return dict(
        all_projects=dict(
            project_count=GitProjectModel.get_project_count(),
            instances=GitProjectModel.get_instance_numbers(),
        ),
        active_projects=dict(
            project_count=GitProjectModel.get_active_projects_count(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
            ),
            top_projects_by_events_handled=GitProjectModel.get_active_projects_usage_numbers(
                datetime_from=datetime_from, datetime_to=datetime_to, top=top
            ),
            instances=GitProjectModel.get_instance_numbers_for_active_projects(
                datetime_from=datetime_from, datetime_to=datetime_to
            ),
        ),
        events={
            project_event_type.value: dict(
                events_handled=GitProjectModel.get_project_event_usage_count(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    project_event_type=project_event_type,
                ),
                top_projects=GitProjectModel.get_project_event_usage_numbers(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    top=top,
                    project_event_type=project_event_type,
                ),
            )
            for project_event_type in ProjectEventModelType
        },
        jobs=jobs,
    )


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
        }
    )


@usage_ns.route("/past-day")
class UsagePastDay(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(hours=1).seconds)
    def get(self):
        yesterday_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        return _get_past_usage_data(datetime_from=yesterday_date)


@usage_ns.route("/past-week")
class UsagePastWeek(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(hours=1).seconds)
    def get(self):
        past_week_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        return _get_past_usage_data(datetime_from=past_week_date)


@usage_ns.route("/past-month")
class UsagePastMonth(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(days=1).seconds)
    def get(self):
        now = datetime.now()
        past_month_past_day = now.replace(day=1) - timedelta(days=1)
        past_month_date = now.replace(
            year=past_month_past_day.year, month=past_month_past_day.month
        ).strftime("%Y-%m-%d")
        return _get_past_usage_data(datetime_from=past_month_date)


@usage_ns.route("/past-year")
class UsagePastYear(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(days=1).seconds)
    def get(self):
        now = datetime.now()
        past_year_date = now.replace(year=now.year - 1).strftime("%Y-%m-%d")
        return _get_past_usage_data(datetime_from=past_year_date)


@usage_ns.route("/total")
class UsageTotal(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    @ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(days=1).seconds)
    def get(self):
        past_date = _DATE_IN_THE_PAST.strftime("%Y-%m-%d")
        return _get_past_usage_data(datetime_from=past_date)


# format the chart needs is a list of {"x": "datetimelegend", "y": value}
CHART_DATA_TYPE = list[dict[str, Union[str, int]]]


@ttl_cache(maxsize=_CACHE_MAXSIZE, ttl=timedelta(hours=1).seconds)
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

    current_date = datetime.now()
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

    past_data = get_usage_data(
        datetime_from=_DATE_IN_THE_PAST, datetime_to=days_legend[-1], top=100000
    )
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
