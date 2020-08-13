from http import HTTPStatus
from logging import getLogger

try:
    from flask_restx import Namespace, Resource
except ModuleNotFoundError:
    from flask_restplus import Namespace, Resource

from packit_service.service.api.utils import response_maker
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.models import GitProjectModel
from flask import url_for

logger = getLogger("packit_service")

ns = Namespace(
    "projects", description="Repositories which have Packit Service enabled."
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

        projects_list = GitProjectModel.get_projects(first, last)
        if not projects_list:
            return response_maker([])
        for project in projects_list:
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

        resp = response_maker(result, status=HTTPStatus.PARTIAL_CONTENT.value,)
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


@ns.route("/<forge>/<namespace>")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
class ProjectsNamespace(Resource):
    @ns.response(HTTPStatus.OK.value, "Projects details follow")
    def get(self, forge, namespace):
        """List of projects of given forge and namespace"""
        result = []
        projects = GitProjectModel.get_namespace(forge, namespace)
        if not projects:
            return response_maker([])
        for project in projects:
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
        return response_maker(result)


@ns.route("/<forge>/<namespace>/<repo_name>/prs")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
@ns.param("repo_name", "Repo Name")
class ProjectsPRs(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(
        HTTPStatus.PARTIAL_CONTENT, "Project PRs handled by Packit Service follow"
    )
    @ns.response(HTTPStatus.OK.value, "OK")
    def get(self, forge, namespace, repo_name):
        """List PRs"""

        result = []
        first, last = indices()

        pr_list = GitProjectModel.get_project_prs(
            first, last, forge, namespace, repo_name
        )
        if not pr_list:
            return response_maker([])
        for pr in pr_list:
            pr_info = {
                "pr_id": pr.pr_id,
                "builds": [],
                "srpm_builds": [],
                "tests": [],
            }
            copr_builds = []
            test_runs = []
            srpm_builds = []

            for build in pr.get_copr_builds():
                build_info = {
                    "build_id": build.build_id,
                    "chroot": build.target,
                    "status": build.status,
                    "web_url": build.web_url,
                }
                copr_builds.append(build_info)
            pr_info["builds"] = copr_builds

            for build in pr.get_srpm_builds():
                build_info = {
                    "srpm_build_id": build.id,
                    "success": build.success,
                    "log_url": url_for(
                        "builds.get_srpm_build_logs_by_id", id_=build.id, _external=True
                    ),
                }
                srpm_builds.append(build_info)
            pr_info["srpm_builds"] = srpm_builds

            for test_run in pr.get_test_runs():
                test_info = {
                    "pipeline_id": test_run.pipeline_id,
                    "chroot": test_run.target,
                    "status": str(test_run.status),
                    "web_url": test_run.web_url,
                }
                test_runs.append(test_info)
            pr_info["tests"] = test_runs

            result.append(pr_info)

        resp = response_maker(result, status=HTTPStatus.PARTIAL_CONTENT.value,)
        resp.headers["Content-Range"] = f"git-project-prs {first + 1}-{last}/*"
        return resp


@ns.route("/<forge>/<namespace>/<repo_name>/issues")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
@ns.param("repo_name", "Repo Name")
class ProjectIssues(Resource):
    @ns.response(
        HTTPStatus.OK.value, "OK, project issues handled by Packit Service follow"
    )
    def get(self, forge, namespace, repo_name):
        """Project issues"""
        issues_list = GitProjectModel.get_project_issues(forge, namespace, repo_name)
        if not issues_list:
            return response_maker([])
        result = []
        for issue in issues_list:
            result.append(issue.issue_id)
        return response_maker(result)


@ns.route("/<forge>/<namespace>/<repo_name>/releases")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
@ns.param("repo_name", "Repo Name")
class ProjectReleases(Resource):
    @ns.response(
        HTTPStatus.OK.value, "OK, project releases handled by Packit Service follow"
    )
    def get(self, forge, namespace, repo_name):
        """Project releases"""
        releases_list = GitProjectModel.get_project_releases(
            forge, namespace, repo_name
        )
        if not releases_list:
            return response_maker([])
        result = []
        for release in releases_list:
            release_info = {
                "tag_name": release.tag_name,
                "commit_hash": release.commit_hash,
            }
            result.append(release_info)
        return response_maker(result)


@ns.route("/<forge>/<namespace>/<repo_name>/branches")
@ns.param("forge", "Git Forge")
@ns.param("namespace", "Namespace")
@ns.param("repo_name", "Repo Name")
class ProjectBranches(Resource):
    @ns.response(
        HTTPStatus.OK.value, "OK, project branches handled by Packit Service follow"
    )
    def get(self, forge, namespace, repo_name):
        """Project branches"""
        branches = GitProjectModel.get_project_branches(forge, namespace, repo_name)
        if not branches:
            return response_maker([])
        result = []
        for branch in branches:
            branch_info = {
                "branch": branch.name,
                "builds": [],
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

            for build in branch.get_srpm_builds():
                build_info = {
                    "srpm_build_id": build.id,
                    "success": build.success,
                    "log_url": url_for(
                        "builds.get_srpm_build_logs_by_id", id_=build.id, _external=True
                    ),
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

        return response_maker(result)
