# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from logging import getLogger
from typing import Optional

from ogr.abstract import Comment, GitProject
from ogr.parsing import RepoUrl
from packit.config import PackageConfig

from packit_service.config import PackageConfigGetter, ServiceConfig
from packit_service.service.db_project_events import (
    AddBranchPushEventToDb,
    AddPullRequestEventToDb,
)
from packit_service.utils import get_packit_commands_from_comment
from packit_service.worker.events.comment import AbstractPRCommentEvent
from packit_service.worker.events.enums import (
    PullRequestAction,
    PullRequestCommentAction,
)
from packit_service.worker.events.event import AbstractForgeIndependentEvent

logger = getLogger(__name__)


class AbstractPagureEvent(AbstractForgeIndependentEvent):
    def __init__(self, project_url: str, pr_id: Optional[int] = None, **kwargs):
        super().__init__(pr_id=pr_id)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None  # git ref that can be 'git checkout'-ed
        self.identifier: Optional[str] = (
            None  # will be shown to users -- e.g. in logs or in the copr-project name
        )

    def get_packages_config(self) -> Optional[PackageConfig]:
        logger.debug(
            f"Getting packages_config:\n"
            f"\tproject: {self.project}\n"
            f"\tdefault_branch: {self.project.default_branch}\n",
        )

        packages_config = PackageConfigGetter.get_package_config_from_repo(
            base_project=None,
            project=self.project,
            pr_id=None,
            reference=self.project.default_branch,
            fail_when_missing=self.fail_when_config_file_missing,
        )

        return packages_config


class PushPagureEvent(AddBranchPushEventToDb, AbstractPagureEvent):
    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        project_url: str,
        commit_sha: str,
        committer: str,
        pr_id: Optional[int],
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.git_ref = git_ref
        self.commit_sha = commit_sha
        self.identifier = git_ref
        self.committer = committer


class PullRequestCommentPagureEvent(AbstractPRCommentEvent, AbstractPagureEvent):
    def __init__(
        self,
        action: PullRequestCommentAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_repo_owner: str,
        base_ref: Optional[str],
        target_repo: str,
        project_url: str,
        user_login: str,
        comment: str,
        comment_id: int,
        commit_sha: str = "",
        comment_object: Optional[Comment] = None,
    ):
        super().__init__(
            pr_id=pr_id,
            project_url=project_url,
            comment=comment,
            comment_id=comment_id,
            commit_sha=commit_sha,
            comment_object=comment_object,
        )
        self.action = action
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_repo_owner = base_repo_owner
        self.base_ref = base_ref
        self.target_repo = target_repo
        self.user_login = user_login
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout

        self._repo_url: Optional[RepoUrl] = None

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        d = self.__dict__
        d["repo_name"] = self.repo_name
        d["repo_namespace"] = self.repo_namespace
        result = super().get_dict(d)
        result.pop("_repo_url")
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        project = self.project.service.get_project(
            namespace=self.base_repo_namespace,
            repo=self.base_repo_name,
            username=self.base_repo_owner,
            is_fork=False,
        )
        logger.debug(f"Base project: {project} owned by {self.base_repo_owner}")
        return project

    def get_packages_config(self) -> Optional[PackageConfig]:
        comment = self.__dict__["comment"]
        commands = get_packit_commands_from_comment(
            comment,
            ServiceConfig.get_service_config().comment_command_prefix,
        )
        if not commands:
            return super().get_packages_config()
        command = commands[0]
        args = commands[1:] if len(commands) > 1 else []
        if command == "pull-from-upstream" and "--with-pr-config" in args:
            # take packages config from the corresponding branch
            # for pull-from-upstream --with-pr-config
            logger.debug(
                f"Getting packages_config:\n"
                f"\tproject: {self.project}\n"
                f"\tbase_project: {self.base_project}\n"
                f"\treference: {self.commit_sha}\n"
                f"\tpr_id: {self.pr_id}",
            )
            packages_config = PackageConfigGetter.get_package_config_from_repo(
                base_project=self.base_project,
                project=self.project,
                reference=self.commit_sha,
                pr_id=self.pr_id,
                fail_when_missing=self.fail_when_config_file_missing,
            )

        else:
            logger.debug(
                f"Getting packages_config:\n"
                f"\tproject: {self.project}\n"
                f"\tdefault_branch: {self.base_project.default_branch}\n",
            )
            packages_config = PackageConfigGetter.get_package_config_from_repo(
                base_project=None,
                project=self.project,
                reference=self.project.default_branch,
                pr_id=None,
                fail_when_missing=self.fail_when_config_file_missing,
            )

        return packages_config

    @property
    def repo_url(self) -> Optional[RepoUrl]:
        if not self._repo_url:
            self._repo_url = RepoUrl.parse(
                (
                    self.packages_config.upstream_project_url
                    if self.packages_config
                    else None
                ),
            )
        return self._repo_url

    @property
    def repo_namespace(self) -> Optional[str]:
        return self.repo_url.namespace if self.repo_url else None

    @property
    def repo_name(self) -> Optional[str]:
        return self.repo_url.repo if self.repo_url else None


class PullRequestPagureEvent(AddPullRequestEventToDb, AbstractPagureEvent):
    def __init__(
        self,
        action: PullRequestAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_repo_owner: str,
        base_ref: str,
        target_repo: str,
        project_url: str,
        commit_sha: str,
        user_login: str,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.action = action
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_repo_owner = base_repo_owner
        self.base_ref = base_ref
        self.target_repo = target_repo
        self.commit_sha = commit_sha
        self.user_login = user_login
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout
        self.project_url = project_url

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> GitProject:
        fork = self.project.service.get_project(
            namespace=self.base_repo_namespace,
            repo=self.base_repo_name,
            username=self.base_repo_owner,
            is_fork=True,
        )
        logger.debug(f"Base project: {fork} owned by {self.base_repo_owner}")
        return fork


class PullRequestFlagPagureEvent(AbstractPagureEvent):
    def __init__(
        self,
        username: str,
        comment: str,
        status: str,
        date_updated: int,
        url: str,
        commit_sha: str,
        pr_id: int,
        pr_url: str,
        pr_source_branch: str,
        project_url: str,
        project_name: str,
        project_namespace: str,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.username = username
        self.comment = comment
        self.status = status
        self.date_updated = date_updated
        self.url = url
        self.commit_sha = commit_sha
        self.pr_url = pr_url
        self.pr_source_branch = pr_source_branch
        self.project_name = project_name
        self.project_namespace = project_namespace
