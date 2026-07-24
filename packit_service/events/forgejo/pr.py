# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from logging import getLogger
from typing import Optional

from ogr.abstract import Comment as OgrComment
from ogr.abstract import GitProject
from ogr.parsing import RepoUrl
from packit.config import PackageConfig

from packit_service.config import ServiceConfig
from packit_service.package_config_getter import PackageConfigGetter
from packit_service.service.db_project_events import AddPullRequestEventToDb
from packit_service.utils import get_packit_commands_from_comment

from ..abstract.comment import PullRequest as AbstractPRCommentEvent
from ..enums import PullRequestAction, PullRequestCommentAction
from .abstract import ForgejoEvent

logger = getLogger(__name__)


class Action(AddPullRequestEventToDb, ForgejoEvent):
    def __init__(
        self,
        action: PullRequestAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_ref: str,
        target_repo_namespace: str,
        target_repo_name: str,
        target_branch: str,
        project_url: str,
        commit_sha: str,
        commit_sha_before: Optional[str],
        actor: str,
        body: str,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.action = action
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.target_branch = target_branch
        self.actor = actor
        self.identifier = str(pr_id)
        self.commit_sha = commit_sha
        self.commit_sha_before = commit_sha_before
        self.body = body

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    @classmethod
    def event_type(cls) -> str:
        return "forgejo.pr.Action"

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.base_repo_namespace,
            repo=self.base_repo_name,
        )

    def get_packages_config(self) -> Optional[PackageConfig]:
        return PackageConfigGetter.get_package_config_from_repo(
            base_project=self.base_project,
            project=self.project,
            reference=self.commit_sha,
            pr_id=self.pr_id,
            fail_when_missing=self.fail_when_config_file_missing,
        )


class Comment(AbstractPRCommentEvent, ForgejoEvent):
    def __init__(
        self,
        action: PullRequestCommentAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: Optional[str],
        base_ref: Optional[str],
        target_repo_namespace: str,
        target_repo_name: str,
        project_url: str,
        source_project_url: str,
        actor: str,
        comment: str,
        comment_id: int,
        commit_sha: Optional[str] = None,
        comment_object: Optional[OgrComment] = None,
    ) -> None:
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
        self.base_ref = base_ref
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.source_project_url = source_project_url
        self.actor = actor
        self.identifier = str(pr_id)
        self.git_ref = None

        self._repo_url: Optional[RepoUrl] = None

    @classmethod
    def event_type(cls) -> str:
        return "forgejo.pr.Comment"

    def get_dict(self, default_dict: Optional[dict] = None) -> dict:
        """
        Override get_dict to avoid accessing properties that make API calls.
        Use private attributes directly, similar to forgejo/issue.py.
        """
        from ..abstract.comment import CommentEvent

        result = CommentEvent.get_dict(self, default_dict=default_dict)

        # prevent leakage of private attributes
        result.pop("_build_targets_override", None)
        result.pop("_tests_targets_override", None)
        result.pop("_repo_url", None)

        result["action"] = self.action.value
        result["pr_id"] = self.pr_id
        result["commit_sha"] = self._commit_sha
        return result

    def get_base_project(self) -> GitProject:
        return self.project.service.get_project(
            namespace=self.base_repo_namespace,
            repo=self.base_repo_name,
        )

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
                f"\tdefault_branch: {self.project.default_branch}\n",
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
                (self.packages_config.upstream_project_url if self.packages_config else None),
            )
        return self._repo_url

    @property
    def repo_namespace(self) -> Optional[str]:
        return self.repo_url.namespace if self.repo_url else None

    @property
    def repo_name(self) -> Optional[str]:
        return self.repo_url.repo if self.repo_url else None
