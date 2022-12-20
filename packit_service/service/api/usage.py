# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

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
