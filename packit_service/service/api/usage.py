# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger
from typing import Any

from flask import request
from flask_restx import Namespace, Resource

from packit_service.models import (
    CoprBuildTargetModel,
    GitProjectModel,
    JobTriggerModelType,
    KojiBuildTargetModel,
    SRPMBuildModel,
    SyncReleaseModel,
    TFTTestRunTargetModel,
    VMImageBuildTargetModel,
)
from packit_service.service.api.utils import response_maker

logger = getLogger("packit_service")

usage_ns = Namespace("usage", description="Data about Packit usage")


@usage_ns.route("")
class Usage(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    def get(self):
        """List all SRPM builds."""

        top = int(request.args.get("top")) if "top" in request.args else None
        datetime_from = request.args.get("from")
        datetime_to = request.args.get("to")

        result = get_usage_data(datetime_from, datetime_to, top)

        return response_maker(result)


@usage_ns.route("/project/<forge>/<namespace>/<repo_name>")
@usage_ns.param("forge", "Git Forge")
@usage_ns.param("namespace", "Namespace")
@usage_ns.param("repo_name", "Repo Name")
class ProjectUsage(Resource):
    @usage_ns.response(HTTPStatus.OK, "Providing data about Packit usage")
    def get(self, forge, namespace, repo_name):
        """List all SRPM builds."""

        datetime_from = request.args.get("from")
        datetime_to = request.args.get("to")
        project_url = f"https://{forge}/{namespace}/{repo_name}"
        result = get_project_usage_data(project_url, datetime_from, datetime_to)

        return response_maker(result)


def get_usage_data(datetime_from=None, datetime_to=None, top=10):
    jobs = {}
    for job_model in [
        SRPMBuildModel,
        CoprBuildTargetModel,
        KojiBuildTargetModel,
        VMImageBuildTargetModel,
        TFTTestRunTargetModel,
        SyncReleaseModel,
    ]:
        jobs[job_model.__tablename__] = dict(
            job_runs=GitProjectModel.get_job_usage_numbers_count_all_triggers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                job_result_model=job_model,
            ),
            top_projects_by_job_runs=GitProjectModel.get_job_usage_numbers_all_triggers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                top=top,
                job_result_model=job_model,
            ),
        )
        jobs[job_model.__tablename__]["per_event"] = {}
        jobs[job_model.__tablename__]["per_event"].update(
            {
                trigger_type.value: dict(
                    job_runs=GitProjectModel.get_job_usage_numbers_count(
                        datetime_from=datetime_from,
                        datetime_to=datetime_to,
                        job_result_model=job_model,
                        trigger_type=trigger_type,
                    ),
                    top_projects_by_job_runs=GitProjectModel.get_job_usage_numbers(
                        datetime_from=datetime_from,
                        datetime_to=datetime_to,
                        top=top,
                        job_result_model=job_model,
                        trigger_type=trigger_type,
                    ),
                )
                for trigger_type in JobTriggerModelType
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
            trigger_type.value: dict(
                events_handled=GitProjectModel.get_trigger_usage_count(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    trigger_type=trigger_type,
                ),
                top_projects=GitProjectModel.get_trigger_usage_numbers(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    top=top,
                    trigger_type=trigger_type,
                ),
            )
            for trigger_type in JobTriggerModelType
        },
        jobs=jobs,
    )


def get_project_usage_data(project: str, datetime_from=None, datetime_to=None):
    jobs: dict[str, Any] = {}
    for job_model in [
        SRPMBuildModel,
        CoprBuildTargetModel,
        KojiBuildTargetModel,
        VMImageBuildTargetModel,
        TFTTestRunTargetModel,
        SyncReleaseModel,
    ]:
        job_name: str = job_model.__tablename__  # type: ignore
        jobs[job_name] = get_result_dictionary(
            project,
            top_projects=GitProjectModel.get_job_usage_numbers_all_triggers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                top=None,
                job_result_model=job_model,
            ),
            count_name="job_runs",
        )

        jobs[job_name]["per_event"] = {}
        for trigger_type in JobTriggerModelType:
            jobs[job_name]["per_event"][trigger_type.value] = get_result_dictionary(
                project,
                top_projects=GitProjectModel.get_job_usage_numbers(
                    datetime_from=datetime_from,
                    datetime_to=datetime_to,
                    top=None,
                    job_result_model=job_model,
                    trigger_type=trigger_type,
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
        trigger_type.value: get_result_dictionary(
            project=project,
            top_projects=GitProjectModel.get_trigger_usage_numbers(
                datetime_from=datetime_from,
                datetime_to=datetime_to,
                top=None,
                trigger_type=trigger_type,
            ),
            count_name="events_handled",
        )
        for trigger_type in JobTriggerModelType
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
