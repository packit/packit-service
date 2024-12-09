# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import time
from datetime import datetime, timezone
from http import HTTPStatus
from logging import getLogger
from typing import Any

from flask import Response, redirect, request
from flask_restx import Namespace, Resource
from markupsafe import escape

from packit_service.celerizer import celery_app
from packit_service.constants import (
    USAGE_DATE_IN_THE_PAST_STR,
    USAGE_PAST_DAY_DATE_STR,
    USAGE_PAST_MONTH_DATE_STR,
    USAGE_PAST_WEEK_DATE_STR,
    USAGE_PAST_YEAR_DATE_STR,
)
from packit_service.models import (
    CoprBuildGroupModel,
    GitProjectModel,
    KojiBuildGroupModel,
    ProjectEventModelType,
    SRPMBuildModel,
    SyncReleaseModel,
    TFTTestRunGroupModel,
    VMImageBuildTargetModel,
    get_usage_data,
)
from packit_service.service.api.utils import response_maker
from packit_service.service.tasks import (
    calculate_onboarded_projects,
    get_past_usage_data,
    get_usage_interval_data,
)

logger = getLogger("packit_service")

usage_ns = Namespace("usage", description="Data about Packit usage")


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
            jobs[job_name]["per_event"][project_event_type.value] = get_result_dictionary(
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
            datetime_from=datetime_from,
            datetime_to=datetime_to,
            top=None,
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

    return {
        "events_handled": events_handled,
        "jobs": jobs,
    }


def get_result_dictionary(
    project,
    top_projects,
    position_name="position",
    count_name="count",
) -> dict[str, int]:
    position = list(top_projects.keys()).index(project) + 1 if project in top_projects else None
    return {position_name: position, count_name: top_projects.get(project)}


@usage_ns.route("/onboarded-projects")
class Onboarded2024Q1(Resource):
    @usage_ns.response(
        HTTPStatus.OK,
        "Onboarded projects for which exist a Bodhi update or a Koji build or a Packit merged PR.",
    )
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
        return calculate_onboarded_projects()


def _get_celery_result(id: str) -> Response:
    """
    Present the Celery task result.

    The redirect link provided by the below api functions
    is meant to be polled by the UX.

    Wait here until the UX can deal with polling for the result.
    """
    TIMEOUT = 15  # seconds
    STEP = 0.1  # second
    elapsed = 0.0
    while not (celery_app.AsyncResult(id).ready() or elapsed > TIMEOUT):
        elapsed += STEP
        time.sleep(STEP)
    result = celery_app.AsyncResult(id)
    return response_maker(result.result)


@usage_ns.route("/past-day")
class UsagePastDay(Resource):
    @usage_ns.response(
        HTTPStatus.OK,
        "Provides a url where to wait for Packit last day usage",
    )
    def get(self):
        task = get_past_usage_data.delay(datetime_from=USAGE_PAST_DAY_DATE_STR)
        return _get_celery_result(task.id)


@usage_ns.route("/past-day/<id>")
@usage_ns.param("id", "Celery task id")
class UsagePastDayResult(Resource):
    @usage_ns.response(HTTPStatus.OK, "Provide data about Packit last day usage")
    def get(self, id):
        return _get_celery_result(id)


@usage_ns.route("/past-week")
class UsagePastWeek(Resource):
    @usage_ns.response(
        HTTPStatus.OK,
        "Provides a url where to wait for Packit last week usage",
    )
    def get(self):
        task = get_past_usage_data.delay(datetime_from=USAGE_PAST_WEEK_DATE_STR)
        return redirect(f"past-week/{task.id}", code=302)


@usage_ns.route("/past-week/<id>")
@usage_ns.param("id", "Celery task id")
class UsagePastWeekResult(Resource):
    @usage_ns.response(HTTPStatus.OK, "Provide data about Packit last week usage")
    def get(self, id):
        return _get_celery_result(id)


@usage_ns.route("/past-month")
class UsagePastMonth(Resource):
    @usage_ns.response(
        HTTPStatus.OK,
        "Provides a url where to wait for Packit last month usage",
    )
    def get(self):
        task = get_past_usage_data.delay(datetime_from=USAGE_PAST_MONTH_DATE_STR)
        return redirect(f"past-month/{task.id}", code=302)


@usage_ns.route("/past-month/<id>")
@usage_ns.param("id", "Celery task id")
class UsagePastMonthResult(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit last month usage")
    def get(self, id):
        return _get_celery_result(id)


@usage_ns.route("/past-year")
class UsagePastYear(Resource):
    @usage_ns.response(
        HTTPStatus.OK,
        "Provides a url where to wait for Packit last year usage",
    )
    def get(self):
        task = get_past_usage_data.delay(datetime_from=USAGE_PAST_YEAR_DATE_STR)
        return redirect(f"past-year/{task.id}", code=302)


@usage_ns.route("/past-year/<id>")
@usage_ns.param("id", "Celery task id")
class UsagePastYearResult(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit last year usage")
    def get(self, id):
        return _get_celery_result(id)


@usage_ns.route("/total")
class UsageTotal(Resource):
    @usage_ns.response(
        HTTPStatus.OK,
        "Provides a url where to wait for Packit total usage data",
    )
    def get(self):
        task = get_past_usage_data.delay(datetime_from=USAGE_DATE_IN_THE_PAST_STR)
        return redirect(f"total/{task.id}", code=302)


@usage_ns.route("/total/<id>")
@usage_ns.param("id", "Celery task id")
class UsageTotalResult(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit total usage")
    def get(self, id):
        return _get_celery_result(id)


@usage_ns.route("/intervals")
class UsageIntervals(Resource):
    @usage_ns.response(HTTPStatus.OK, "Ask data about Packit interval usage")
    def get(self):
        """
        Returns a new url where to wait for Celery task results.

        Use `days` and `hours` parameters to define interval and `count` to set number of intervals.

        Examples:
        /api/usage/intervals/past?days=7&hours=0&count=52 for the weekly data of the last year
        /api/usage/intervals?days=0&hours=1&count=24 for the hourly data of the last day
        """
        count = int(escape(request.args.get("count", "10")))
        delta_hours = int(escape(request.args.get("hours", "0")))
        delta_days = int(escape(request.args.get("days", "0")))
        task = get_usage_interval_data.delay(
            hours=delta_hours,
            days=delta_days,
            count=count,
        )
        return redirect(f"intervals/{task.id}", code=302)


@usage_ns.route("/intervals/<id>")
@usage_ns.param("id", "Celery task id")
class UsageIntervalsResult(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    def get(self, id):
        """
        Returns the data for trend charts collected by a celery worker.
        """
        return _get_celery_result(id)
