# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
import itertools
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import (
    CoprBuildModel,
    KojiBuildModel,
    RunModel,
    SRPMBuildModel,
    TFTTestRunModel,
    optional_time,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

ns = Namespace("runs", description="Pipelines")


def process_runs(runs):
    """
    Process `RunModel`s and construct a JSON that is returned from the endpoints
    that return merged chroots.

    Args:
        runs: Iterator over merged `RunModel`s.

    Returns:
        List of JSON objects where each represents pipelines run on single SRPM.
    """
    result = []

    for pipeline in runs:
        srpm_build = SRPMBuildModel.get_by_id(pipeline.srpm_build_id)
        response_dict = {
            "merged_run_id": pipeline.merged_id,
            "time_submitted": optional_time(srpm_build.build_submitted_time),
            "trigger": get_project_info_from_build(srpm_build),
            "srpm": {
                "packit_id": srpm_build.id,
                "success": srpm_build.success,
            },
            "copr": [],
            "koji": [],
            "test_run": [],
        }

        for model_type, Model, packit_ids in (
            ("copr", CoprBuildModel, pipeline.copr_build_id),
            ("koji", KojiBuildModel, pipeline.koji_build_id),
            ("test_run", TFTTestRunModel, pipeline.test_run_id),
        ):
            for packit_id in set(filter(None, map(lambda ids: ids[0], packit_ids))):
                row = Model.get_by_id(packit_id)
                response_dict[model_type].append(
                    {
                        "packit_id": packit_id,
                        "target": row.target,
                        "status": row.status,
                    }
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
        result = process_runs(RunModel.get_merged_chroots(first, last))
        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT.value,
        )
        resp.headers["Content-Range"] = f"runs {first + 1}-{last}/*"
        return resp


@ns.route("/<path:project>")
class RunsListPerProject(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "List of runs per project follows")
    def get(self, project: str):
        """List all runs of a specific project."""
        first, last = indices()

        result = process_runs(
            itertools.islice(
                filter(
                    lambda run: SRPMBuildModel.get_by_id(run.srpm_build_id)
                    .get_project()
                    .full_path
                    == project,
                    RunModel.get_merged_chroots_of(project),
                ),
                first,
                last,
            )
        )
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
        if result := process_runs([RunModel.get_merged_run(id)]):
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
        run = RunModel.get_run(id_=id)
        if not run:
            return response_maker(
                {"error": "No run has been found in DB"},
                status=HTTPStatus.NOT_FOUND.value,
            )

        result = {
            "run_id": run.id,
            "trigger": get_project_info_from_build(run.srpm_build),
            "srpm_build_id": run.srpm_build_id,
            "copr_build_id": run.copr_build_id,
            "koji_build_id": run.koji_build_id,
            "test_run_id": run.test_run_id,
        }
        return response_maker(result)
