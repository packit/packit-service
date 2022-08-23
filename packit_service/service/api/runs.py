# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger
from typing import Dict

from flask_restx import Namespace, Resource

from packit_service.models import (
    CoprBuildGroupModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProposeDownstreamModel,
    SRPMBuildModel,
    TFTTestRunTargetModel,
    TFTTestRunGroupModel,
    GroupModel,
    optional_timestamp,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import (
    get_project_info_from_build,
    response_maker,
)

logger = getLogger("packit_service")

ns = Namespace("runs", description="Pipelines")


def _add_propose_downstream(run: ProposeDownstreamModel, response_dict: Dict):
    targets = response_dict["propose_downstream"]

    for target in run.propose_downstream_targets:
        targets.append(
            {
                "packit_id": target.id,
                "target": target.branch,
                "status": target.status,
            }
        )

    if "trigger" not in response_dict:
        response_dict["time_submitted"] = optional_timestamp(run.submitted_time)
        response_dict["trigger"] = get_project_info_from_build(run)


def flatten_and_remove_none(ids):
    return filter(None, map(lambda arr: arr[0], ids))


def process_runs(runs):
    """
    Process `PipelineModel`s and construct a JSON that is returned from the endpoints
    that return merged chroots.

    Args:
        runs: Iterator over merged `PipelineModel`s.

    Returns:
        List of JSON objects where each represents pipelines run on single SRPM.
    """
    result = []

    for pipeline in runs:
        response_dict = {
            "merged_run_id": pipeline.merged_id,
            "srpm": None,
            "copr": [],
            "koji": [],
            "test_run": [],
            "propose_downstream": [],
        }

        srpm_build = SRPMBuildModel.get_by_id(pipeline.srpm_build_id)
        if srpm_build:
            response_dict["srpm"] = {
                "packit_id": srpm_build.id,
                "status": srpm_build.status,
            }
            response_dict["time_submitted"] = optional_timestamp(
                srpm_build.build_submitted_time
            )
            response_dict["trigger"] = get_project_info_from_build(srpm_build)

        for model_type, Model, packit_ids in (
            ("copr", CoprBuildGroupModel, pipeline.copr_build_group_id),
            ("koji", KojiBuildTargetModel, pipeline.koji_build_id),
            ("test_run", TFTTestRunGroupModel, pipeline.test_run_group_id),
        ):
            for packit_id in set(flatten_and_remove_none(packit_ids)):
                group_row = Model.get_by_id(packit_id)
                target_models = (
                    group_row.grouped_targets
                    if isinstance(group_row, GroupModel)
                    else [group_row]
                )
                for row in target_models:
                    if row.status == "waiting_for_srpm":
                        continue
                    response_dict[model_type].append(
                        {
                            "packit_id": row.id,
                            "target": row.target,
                            "status": row.status,
                        }
                    )
                    if "trigger" not in response_dict:
                        submitted_time = (
                            row.submitted_time
                            if isinstance(row, TFTTestRunTargetModel)
                            else row.build_submitted_time
                        )
                        response_dict["time_submitted"] = optional_timestamp(
                            submitted_time
                        )
                        response_dict["trigger"] = get_project_info_from_build(row)

        # handle propose-downstream
        if propose_downstream := list(
            flatten_and_remove_none(pipeline.propose_downstream_run_id)
        ):
            _add_propose_downstream(
                ProposeDownstreamModel.get_by_id(propose_downstream[0]),
                response_dict,
            )

        result.append(response_dict)

    return result


@ns.route("")
class RunsList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "List of runs follows")
    def get(self):
        """List all runs."""
        first, last = indices()
        result = process_runs(PipelineModel.get_merged_chroots(first, last))
        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT.value,
        )
        resp.headers["Content-Range"] = f"runs {first + 1}-{last}/*"
        return resp


@ns.route("/merged/<int:id>")
@ns.param("id", "First packit ID of the merged run")
class MergedRun(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, merged run details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "Run ID not found in DB")
    def get(self, id):
        """Return details for merged run."""
        if result := process_runs(filter(None, [PipelineModel.get_merged_run(id)])):
            return response_maker(result[0])

        return response_maker(
            {"error": "No run has been found in DB"}, status=HTTPStatus.NOT_FOUND.value
        )


@ns.route("/<int:id>")
@ns.param("id", "Packit ID of the run")
class Run(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, run details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "Run ID not found in DB")
    def get(self, id):
        """Return details for given run."""
        run = PipelineModel.get_run(id_=id)
        if not run:
            return response_maker(
                {"error": "No run has been found in DB"},
                status=HTTPStatus.NOT_FOUND.value,
            )

        result = {
            "run_id": run.id,
            "trigger": get_project_info_from_build(
                run.srpm_build or run.propose_downstream_run
            ),
            "srpm_build_id": run.srpm_build_id,
            "copr_build_group_id": run.copr_build_group_id,
            "koji_build_id": run.koji_build_id,
            "test_run_group_id": run.test_run_group_id,
        }
        return response_maker(result)
