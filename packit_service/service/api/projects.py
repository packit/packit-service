# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import GitProjectModel
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import response_maker
from packit_service.service.urls import get_srpm_build_info_url

logger = getLogger("packit_service")

ns = Namespace(
    "projects",
    description="Repositories which have Packit Service enabled.",
)


@ns.route("")
class ProjectsList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Projects list follows")
    @ns.response(HTTPStatus.OK.value, "OK")
    def get(self):
        """List all GitProjects"""

        result = []
        first, last = indices()

        for project in GitProjectModel.get_range(first, last):
            project_info = {
                "namespace": project.namespace,
                "repo_name": project.repo_name,
                "project_url": project.project_url,
                "prs_handled": len(project.pull_requests),
                "branches_handled": len(project.branches),
                "releases_handled": len(project.releases),
                "issues_handled": len(project.issues),
            }
            result.append(project_info)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT if result else HTTPStatus.OK,
        )
        resp.headers["Content-Range"] = f"git-projects {first + 1}-{last}/*"
        return resp


@ns.route("/<forge>/<namespace>/<repo_name>")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
@ns.param("repo_name", "Repo Name")
class ProjectInfo(Resource):
    @ns.response(HTTPStatus.OK.value, "Project details follow")
    def get(self, forge, namespace, repo_name):
        """Project Details"""
        project = GitProjectModel.get_project(forge, namespace, repo_name)
        if not project:
            return response_maker(
                {"error": "No info about project stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )
        project_info = {
            "namespace": project.namespace,
            "repo_name": project.repo_name,
            "project_url": project.project_url,
            "prs_handled": len(project.pull_requests),
            "branches_handled": len(project.branches),
            "releases_handled": len(project.releases),
            "issues_handled": len(project.issues),
        }
        return response_maker(project_info)


@ns.route("/<forge>")
@ns.param("forge", "Git Forge")
class ProjectsForge(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Projects list follows")
    @ns.response(HTTPStatus.OK.value, "OK")
    def get(self, forge):
        """List of projects of given forge (e.g. github.com, gitlab.com)"""

        result = []
        first, last = indices()

        for project in GitProjectModel.get_by_forge(first, last, forge):
            project_info = {
                "namespace": project.namespace,
                "repo_name": project.repo_name,
                "project_url": project.project_url,
                "prs_handled": len(project.pull_requests),
                "branches_handled": len(project.branches),
                "releases_handled": len(project.releases),
                "issues_handled": len(project.issues),
            }
            result.append(project_info)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT if result else HTTPStatus.OK,
        )
        resp.headers["Content-Range"] = f"git-projects {first + 1}-{last}/*"
        return resp


@ns.route("/<forge>/<namespace>")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
class ProjectsNamespace(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.OK.value, "Projects details follow")
    def get(self, forge, namespace):
        """List of projects of given forge and namespace"""
        result = []
        first, last = indices()

        for project in GitProjectModel.get_by_forge_namespace(
            first,
            last,
            forge,
            namespace,
        ):
            project_info = {
                "namespace": project.namespace,
                "repo_name": project.repo_name,
                "project_url": project.project_url,
                "prs_handled": len(project.pull_requests),
                "branches_handled": len(project.branches),
                "releases_handled": len(project.releases),
                "issues_handled": len(project.issues),
            }
            result.append(project_info)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT if result else HTTPStatus.OK,
        )
        resp.headers["Content-Range"] = f"git-projects {first + 1}-{last}/*"
        return resp


@ns.route("/<forge>/<namespace>/<repo_name>/prs")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
@ns.param("repo_name", "Repo Name")
class ProjectsPRs(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(
        HTTPStatus.PARTIAL_CONTENT.value,
        "Project PRs handled by Packit Service follow",
    )
    @ns.response(HTTPStatus.OK.value, "OK")
    def get(self, forge, namespace, repo_name):
        """List PRs"""

        result = []
        first, last = indices()

        for pr in GitProjectModel.get_project_prs(
            first,
            last,
            forge,
            namespace,
            repo_name,
        ):
            pr_info = {
                "pr_id": pr.pr_id,
                "builds": [],
                "koji_builds": [],
                "srpm_builds": [],
                "tests": [],
            }

            for build in pr.get_copr_builds():
                build_info = {
                    "build_id": build.build_id,
                    "chroot": build.target,
                    "status": build.status,
                    "web_url": build.web_url,
                }
                pr_info["builds"].append(build_info)

            for build in pr.get_koji_builds():
                build_info = {
                    "task_id": build.task_id,
                    "chroot": build.target,
                    "status": build.status,
                    "web_url": build.web_url,
                }
                pr_info["koji_builds"].append(build_info)

            for build in pr.get_srpm_builds():
                build_info = {
                    "srpm_build_id": build.id,
                    "status": build.status,
                    "log_url": get_srpm_build_info_url(build.id),
                }
                pr_info["srpm_builds"].append(build_info)

            for test_run in pr.get_test_runs():
                test_info = {
                    "pipeline_id": test_run.pipeline_id,
                    "chroot": test_run.target,
                    "status": str(test_run.status),
                    "web_url": test_run.web_url,
                }
                pr_info["tests"].append(test_info)

            result.append(pr_info)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT if result else HTTPStatus.OK,
        )

        resp.headers["Content-Range"] = f"git-project-prs {first + 1}-{last}/*"
        return resp


@ns.route("/<forge>/<namespace>/<repo_name>/issues")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
@ns.param("repo_name", "Repo Name")
class ProjectIssues(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(
        HTTPStatus.OK.value,
        "OK, project issues handled by Packit Service follow",
    )
    def get(self, forge, namespace, repo_name):
        """Project issues"""
        first, last = indices()

        issues = [
            issue.issue_id
            for issue in GitProjectModel.get_project_issues(
                first,
                last,
                forge,
                namespace,
                repo_name,
            )
        ]

        resp = response_maker(
            issues,
            status=HTTPStatus.PARTIAL_CONTENT if issues else HTTPStatus.OK,
        )

        resp.headers["Content-Range"] = f"git-project-issues {first + 1}-{last}/*"
        return resp


@ns.route("/<forge>/<namespace>/<repo_name>/releases")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
@ns.param("repo_name", "Repo Name")
class ProjectReleases(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(
        HTTPStatus.OK.value,
        "OK, project releases handled by Packit Service follow",
    )
    def get(self, forge, namespace, repo_name):
        """Project releases"""
        result = []
        first, last = indices()

        for release in GitProjectModel.get_project_releases(
            first,
            last,
            forge,
            namespace,
            repo_name,
        ):
            release_info = {
                "tag_name": release.tag_name,
                "commit_hash": release.commit_hash,
            }
            result.append(release_info)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT if result else HTTPStatus.OK,
        )

        resp.headers["Content-Range"] = f"git-project-releases {first + 1}-{last}/*"
        return resp


@ns.route("/<forge>/<namespace>/<repo_name>/branches")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
@ns.param("repo_name", "Repo Name")
class ProjectBranches(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(
        HTTPStatus.OK.value,
        "OK, project branches handled by Packit Service follow",
    )
    def get(self, forge, namespace, repo_name):
        """Project branches"""
        result = []
        first, last = indices()

        for branch in GitProjectModel.get_project_branches(
            first,
            last,
            forge,
            namespace,
            repo_name,
        ):
            branch_info = {
                "branch": branch.name,
                "builds": [],
                "koji_builds": [],
                "srpm_builds": [],
                "tests": [],
            }

            for build in branch.get_copr_builds():
                build_info = {
                    "build_id": build.build_id,
                    "chroot": build.target,
                    "status": build.status,
                    "web_url": build.web_url,
                }
                branch_info["builds"].append(build_info)

            for build in branch.get_koji_builds():
                build_info = {
                    "task_id": build.task_id,
                    "chroot": build.target,
                    "status": build.status,
                    "web_url": build.web_url,
                }
                branch_info["koji_builds"].append(build_info)

            for build in branch.get_srpm_builds():
                build_info = {
                    "srpm_build_id": build.id,
                    "status": build.status,
                    "log_url": get_srpm_build_info_url(build.id),
                }
                branch_info["srpm_builds"].append(build_info)

            for test_run in branch.get_test_runs():
                test_info = {
                    "pipeline_id": test_run.pipeline_id,
                    "chroot": test_run.target,
                    "status": test_run.status,
                    "web_url": test_run.web_url,
                }
                branch_info["tests"].append(test_info)
            result.append(branch_info)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT if result else HTTPStatus.OK,
        )

        resp.headers["Content-Range"] = f"git-project-branches {first + 1}-{last}/*"
        return resp
