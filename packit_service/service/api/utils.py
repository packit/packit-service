# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from typing import Any, Union

from flask.json import jsonify

from packit_service.models import (
    AnityaProjectModel,
    BodhiUpdateTargetModel,
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    GitProjectModel,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    SRPMBuildModel,
    SyncReleaseModel,
    SyncReleaseTargetModel,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
    VMImageBuildTargetModel,
    optional_timestamp,
)


def response_maker(result: Any, status: HTTPStatus = HTTPStatus.OK):
    """response_maker is a wrapper around flask's make_response"""
    resp = jsonify(result)
    resp.status_code = status.value
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
        BodhiUpdateTargetModel,
        VMImageBuildTargetModel,
    ],
) -> dict[str, Any]:
    if not (project := build.get_project()):
        return {}

    result_dict = {
        "pr_id": build.get_pr_id(),
        "issue_id": build.get_issue_id(),
        "branch_name": build.get_branch_name(),
        "release": build.get_release_tag(),
        "anitya_version": build.get_anitya_version(),
    }
    result_dict.update(get_project_info(project))
    return result_dict


def get_sync_release_target_info(sync_release_model: SyncReleaseTargetModel):
    pr_models = sync_release_model.pull_requests
    job_result_dict = {
        "status": sync_release_model.status,
        "branch": sync_release_model.branch,
        "downstream_prs": [
            {
                "pr_id": pr.pr_id,
                "branch": pr.target_branch,
                "is_fast_forward": pr.is_fast_forward,
                "url": pr.url,
            }
            for pr in pr_models
        ],
        "downstream_pr_project": pr_models[0].project.project_url if pr_models else None,
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
        "anitya_version": sync_release_model.get_anitya_version(),
    }

    project = sync_release_model.get_project()
    result_dict.update(get_project_info(project))

    return result_dict


def get_project_info(project: Union[AnityaProjectModel, GitProjectModel]):
    result_dict = {}

    anitya_project_id = anitya_project_name = anitya_package = None
    project_url = repo_name = repo_namespace = None

    if isinstance(project, AnityaProjectModel):
        anitya_project_id = project.project_id if project else ""
        anitya_project_name = project.project_name if project else ""
        anitya_package = project.package if project else ""
        project_url = f"https://release-monitoring.org/project/{anitya_project_id}"
    elif isinstance(project, GitProjectModel):
        repo_namespace = project.namespace if project else ""
        repo_name = project.repo_name if project else ""
        project_url = project.project_url if project else ""

    result_dict["repo_namespace"] = repo_namespace
    result_dict["repo_name"] = repo_name
    result_dict["project_url"] = project_url
    result_dict["anitya_project_id"] = anitya_project_id
    result_dict["anitya_project_name"] = anitya_project_name
    result_dict["anitya_package"] = anitya_package
    result_dict["non_git_upstream"] = isinstance(project, AnityaProjectModel)

    return result_dict
