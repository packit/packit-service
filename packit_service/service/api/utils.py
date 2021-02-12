from http import HTTPStatus
from json import dumps
from typing import Any, Dict, Union

from flask import make_response

from packit_service.models import (
    CoprBuildModel,
    KojiBuildModel,
    SRPMBuildModel,
    TFTTestRunModel,
)


def response_maker(result, status=HTTPStatus.OK.value):
    """response_maker is a wrapper around flask's make_response"""
    resp = make_response(dumps(result), status)
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def get_project_info_from_build(
    build: Union[SRPMBuildModel, CoprBuildModel, KojiBuildModel, TFTTestRunModel]
) -> Dict[str, Any]:
    project = build.get_project()
    if not project:
        return {}

    return {
        "repo_namespace": project.namespace,
        "repo_name": project.repo_name,
        "git_repo": project.project_url,
        "pr_id": build.get_pr_id(),
        "branch_name": build.get_branch_name(),
        "release": build.get_release_tag(),
    }
