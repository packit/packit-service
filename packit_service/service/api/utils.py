# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from json import dumps
from typing import Any, Dict, Union

from flask import make_response

from packit_service.models import (
    CoprBuildTargetModel,
    CoprBuildGroupModel,
    KojiBuildTargetModel,
    KojiBuildGroupModel,
    SRPMBuildModel,
    TFTTestRunTargetModel,
    TFTTestRunGroupModel,
    SyncReleaseModel,
    SyncReleaseTargetModel,
    optional_timestamp,
)


def response_maker(result: Any, status: HTTPStatus = HTTPStatus.OK):
    """response_maker is a wrapper around flask's make_response"""
    resp = make_response(dumps(result), status.value)
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def get_project_info_from_build(
    build: Union[
        SRPMBuildModel,
        CoprBuildTargetModel,
        CoprBuildGroupModel,
        KojiBuildTargetModel,
        KojiBuildGroupModel,
        TFTTestRunTargetModel,
        TFTTestRunGroupModel,
        SyncReleaseModel,
    ]
) -> Dict[str, Any]:
    if not (project := build.get_project()):
        return {}

    return {
        "repo_namespace": project.namespace,
        "repo_name": project.repo_name,
        "git_repo": project.project_url,
        "pr_id": build.get_pr_id(),
        "issue_id": build.get_issue_id(),
        "branch_name": build.get_branch_name(),
        "release": build.get_release_tag(),
    }


def get_sync_release_target_info(sync_release_model: SyncReleaseTargetModel):
    job_result_dict = {
        "status": sync_release_model.status,
        "branch": sync_release_model.branch,
        "downstream_pr_url": sync_release_model.downstream_pr_url,
        "submitted_time": optional_timestamp(sync_release_model.submitted_time),
        "start_time": optional_timestamp(sync_release_model.start_time),
        "finished_time": optional_timestamp(sync_release_model.finished_time),
        "logs": sync_release_model.logs,
    }

    job_result_dict.update(get_project_info_from_build(sync_release_model.sync_release))
    return job_result_dict


def get_sync_release_info(sync_release_model: SyncReleaseModel):
    result_dict = {
        "packit_id": sync_release_model.id,
        "status": sync_release_model.status,
        "submitted_time": optional_timestamp(sync_release_model.submitted_time),
        "status_per_downstream_pr": {
            pr.branch: pr.status for pr in sync_release_model.sync_release_targets
        },
        "packit_id_per_downstream_pr": {
            pr.branch: pr.id for pr in sync_release_model.sync_release_targets
        },
        "pr_id": sync_release_model.get_pr_id(),
        "issue_id": sync_release_model.get_issue_id(),
        "release": sync_release_model.get_release_tag(),
    }

    project = sync_release_model.get_project()
    result_dict["repo_namespace"] = project.namespace if project else ""
    result_dict["repo_name"] = project.repo_name if project else ""
    result_dict["project_url"] = project.project_url if project else ""
    return result_dict
