# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from .abstract import ForgejoEvent


class ActionRun(ForgejoEvent):
    def __init__(
        self,
        actor: str,
        title: str,
        comment: Optional[str],
        status: str,
        date_updated: str,
        url: str,
        commit_sha: str,
        pr_id: Optional[int],
        project_url: str,
        project_name: str,
        project_namespace: str,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.actor = actor
        self.title = title
        self.comment = comment
        self.status = status
        self.date_updated = date_updated
        self.url = url
        self.commit_sha = commit_sha
        self.project_name = project_name
        self.project_namespace = project_namespace


class PullRequest(ActionRun):
    def __init__(
        self,
        actor: str,
        title: str,
        comment: Optional[str],
        status: str,
        date_updated: str,
        url: str,
        commit_sha: str,
        pr_id: int,
        pr_url: str,
        pr_source_branch: str,
        project_url: str,
        project_name: str,
        project_namespace: str,
    ):
        super().__init__(
            actor=actor,
            title=title,
            comment=comment,
            status=status,
            date_updated=date_updated,
            url=url,
            commit_sha=commit_sha,
            pr_id=pr_id,
            project_url=project_url,
            project_name=project_name,
            project_namespace=project_namespace,
        )

        self.identifier = str(pr_id)
        self.pr_url = pr_url
        self.pr_source_branch = pr_source_branch

    @classmethod
    def event_type(cls) -> str:
        return "forgejo.action_run.PullRequest"


class Push(ActionRun):
    def __init__(
        self,
        actor: str,
        title: str,
        comment: Optional[str],
        status: str,
        date_updated: str,
        url: str,
        commit_sha: str,
        git_ref: str,
        project_url: str,
        project_name: str,
        project_namespace: str,
    ):
        super().__init__(
            actor=actor,
            title=title,
            comment=comment,
            status=status,
            date_updated=date_updated,
            url=url,
            commit_sha=commit_sha,
            pr_id=None,
            project_url=project_url,
            project_name=project_name,
            project_namespace=project_namespace,
        )

        self.identifier = git_ref
        self.git_ref = git_ref

    @classmethod
    def event_type(cls) -> str:
        return "forgejo.action_run.Push"
