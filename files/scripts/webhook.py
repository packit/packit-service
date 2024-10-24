#!/usr/bin/env python3

# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Generator of webhooks
"""

import json
from typing import Union

import click
import ogr
import requests
from github.PullRequest import PullRequest


class PRWebhookPayload:
    """
    generate a webhook payload when a PR content changes
    """

    def __init__(
        self,
        namespace: str,
        project_name: str,
        pr_id: Union[int, str],
        github_token: str,
    ):
        self.namespace = namespace
        self.project_name = project_name
        self.pr_id = pr_id
        self.github_token = github_token

    def generate(self) -> dict:
        s = ogr.GithubService(token=self.github_token)
        project = s.get_project(namespace=self.namespace, repo=self.project_name)
        pr_info = project.get_pr(self.pr_id)
        github_pr: PullRequest = project.github_repo.get_pull(number=self.pr_id)
        full_repository_name = github_pr.base.repo.full_name
        target_namespace = github_pr.base.repo.owner.login
        target_project_name = github_pr.base.repo.name
        fork_project_name = github_pr.head.repo.name
        fork_namespace = github_pr.head.repo.owner.login
        full_fork_name = github_pr.head.repo.full_name
        return {
            "action": "synchronize",
            "number": self.pr_id,
            "repository": {
                "full_name": full_repository_name,
                "html_url": github_pr.base.repo.html_url,
            },
            "pull_request": {
                "head": {
                    "ref": pr_info.source_branch,
                    "sha": github_pr.head.sha,
                    "repo": {
                        "name": fork_project_name,
                        "full_name": full_fork_name,
                        "owner": {"login": fork_namespace},
                        "html_url": f"https://github.com/{full_fork_name}",
                        "clone_url": f"https://github.com/{full_fork_name}.git",
                    },
                },
                "base": {
                    "ref": pr_info.target_branch,
                    "repo": {
                        "name": target_project_name,
                        "full_name": full_repository_name,
                        "owner": {"login": target_namespace},
                        "html_url": github_pr.base.repo.html_url,
                        "clone_url": github_pr.base.repo.clone_url,
                    },
                },
                "user": {"login": github_pr.user.login},
            },
        }


@click.command()
@click.option(
    "--hostname",
    default="dev.packit.dev:8443",
    help="Hostname of packit-service where we should connect.",
)
@click.option(
    "--github-token",
    envvar="GITHUB_TOKEN",
    help="GitHub token so we can reach the api.",
)
@click.option("--pr", help="ID of the pull request.", default=None, type=int)
@click.argument(
    "project",
    default="packit-service/hello-world",
    metavar="<NAMESPACE/PROJECT>",
)
def run(hostname, pr, project, github_token):
    if "/" not in project:
        click.echo(
            'project should be specified as "PROJECT/NAMESPACE", e.g. "packit-service/ogr"',
        )
        return 1
    if pr is not None:
        project_namespace, project_name = project.split("/", 1)
        p = PRWebhookPayload(project_namespace, project_name, pr, github_token)
        j = p.generate()
        print(json.dumps(j, indent=2))
        response = requests.post(
            f"https://{hostname}/api/webhooks/github",
            json=j,
            verify=False,
        )
        print(response.text)
        return None
    return None


if __name__ == "__main__":
    run()
