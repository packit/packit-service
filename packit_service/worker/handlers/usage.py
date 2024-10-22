# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from ogr.abstract import PRStatus

from packit_service.config import ServiceConfig
from packit_service.models import GitProjectModel


def check_onboarded_projects(projects: set[GitProjectModel]):
    """For every given project check if it has a merged Packit PR.

    If yes it is onboarded: save the flag in the git projects table.
    """
    for project in projects:
        downstream_project_url = f"https://{project.instance_url}"
        if downstream_project_url != "https://src.fedoraproject.org":
            continue

        pagure_service = next(
            service
            for service in ServiceConfig.get_service_config().services
            if service.instance_url == downstream_project_url
        )
        ogr_project = pagure_service.get_project(
            namespace=project.namespace,
            repo=project.repo_name,
        )
        prs = ogr_project.get_pr_list(status=PRStatus.merged)
        prs_from_packit = [pr for pr in prs if pr.author in ("packit", "packit-stg")]

        if prs_from_packit:
            db_project = GitProjectModel.get_by_id(project.id)
            db_project.set_onboarded_downstream(True)
