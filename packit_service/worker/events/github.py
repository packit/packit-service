# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Dict, Optional, Union, List, Set

from ogr.abstract import GitProject, PRComment, IssueComment

from packit_service.models import (
    AllowlistStatus,
    PullRequestModel,
    GitBranchModel,
    ProjectReleaseModel,
)
from packit_service.service.db_triggers import (
    AddIssueDbTrigger,
    AddPullRequestDbTrigger,
    AddBranchPushDbTrigger,
    AddReleaseDbTrigger,
)
from packit_service.worker.events.enums import (
    IssueCommentAction,
    PullRequestCommentAction,
    PullRequestAction,
)
from packit_service.worker.events.event import (
    Event,
    AbstractForgeIndependentEvent,
)


class AbstractGithubEvent(AbstractForgeIndependentEvent):
    def __init__(self, project_url: str, pr_id: Optional[int] = None):
        super().__init__(pr_id=pr_id)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None  # git ref that can be 'git checkout'-ed
        self.identifier: Optional[
            str
        ] = None  # will be shown to users -- e.g. in logs or in the copr-project name


class ReleaseEvent(AddReleaseDbTrigger, AbstractGithubEvent):
    def __init__(
        self, repo_namespace: str, repo_name: str, tag_name: str, project_url: str
    ):
        super().__init__(project_url=project_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.tag_name = tag_name
        self.git_ref = tag_name
        self.identifier = tag_name
        self._commit_sha: Optional[str] = None

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        # mypy does not like properties
        if not self._commit_sha:
            self._commit_sha = self.project.get_sha_from_tag(tag_name=self.tag_name)
        return self._commit_sha

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["commit_sha"] = self.commit_sha
        return result


class PushGitHubEvent(AddBranchPushDbTrigger, AbstractGithubEvent):
    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        project_url: str,
        commit_sha: str,
    ):
        super().__init__(project_url=project_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.git_ref = git_ref
        self.commit_sha = commit_sha
        self.identifier = git_ref


class PullRequestGithubEvent(AddPullRequestDbTrigger, AbstractGithubEvent):
    def __init__(
        self,
        action: PullRequestAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_ref: str,
        target_repo_namespace: str,
        target_repo_name: str,
        project_url: str,
        commit_sha: str,
        user_login: str,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.action = action
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.commit_sha = commit_sha
        self.user_login = user_login
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> Optional[GitProject]:
        return None  # With Github app, we cannot work with fork repo


class PullRequestCommentGithubEvent(AddPullRequestDbTrigger, AbstractGithubEvent):
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
        user_login: str,
        comment: str,
        comment_id: int,
        commit_sha: Optional[str] = None,
        comment_object: Optional[PRComment] = None,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.action = action
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.user_login = user_login
        self.comment = comment
        self.comment_id = comment_id
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout

        # Lazy properties
        self._commit_sha = commit_sha
        self._comment_object = comment_object

    @property
    def commit_sha(self) -> Optional[str]:  # type:ignore
        # mypy does not like properties
        if not self._commit_sha:
            self._commit_sha = self.project.get_pr(pr_id=self.pr_id).head_commit
        return self._commit_sha

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        result["commit_sha"] = self.commit_sha
        result.pop("_comment_object")
        return result

    def get_base_project(self) -> Optional[GitProject]:
        return None  # With Github app, we cannot work with fork repo

    @property
    def comment_object(self) -> Optional[PRComment]:
        if not self._comment_object:
            self._comment_object = self.project.get_pr(self.pr_id).get_comment(
                self.comment_id
            )
        return self._comment_object


class IssueCommentEvent(AddIssueDbTrigger, AbstractGithubEvent):
    def __init__(
        self,
        action: IssueCommentAction,
        issue_id: int,
        repo_namespace: str,
        repo_name: str,
        target_repo: str,
        project_url: str,
        user_login: str,
        comment: str,
        comment_id: int,
        tag_name: str = "",
        base_ref: Optional[
            str
        ] = "master",  # default is master when working with issues
        comment_object: Optional[IssueComment] = None,
    ):
        super().__init__(project_url=project_url)
        self.action = action
        self.issue_id = issue_id
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.base_ref = base_ref
        self._tag_name = tag_name
        self.target_repo = target_repo
        self.user_login = user_login
        self.comment = comment
        self.comment_id = comment_id
        self.identifier = str(issue_id)

        # Lazy properties
        self._comment_object = comment_object

    @property
    def tag_name(self):
        if not self._tag_name:
            self._tag_name = ""
            if latest_release := self.project.get_latest_release():
                self._tag_name = latest_release.tag_name
        return self._tag_name

    @property
    def commit_sha(self):
        return self.tag_name

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        result["tag_name"] = self.tag_name
        result["issue_id"] = self.issue_id
        result.pop("_comment_object")
        return result

    @property
    def comment_object(self) -> Optional[IssueComment]:
        if not self._comment_object:
            self._comment_object = self.project.get_issue(self.issue_id).get_comment(
                self.comment_id
            )
        return self._comment_object


class CheckRerunEvent(AbstractGithubEvent):
    def __init__(
        self,
        check_name_job: str,
        check_name_target: str,
        project_url: str,
        repo_namespace: str,
        repo_name: str,
        db_trigger: Union[PullRequestModel, GitBranchModel, ProjectReleaseModel],
        commit_sha: str,
        pr_id: Optional[int] = None,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.check_name_job = check_name_job
        self.check_name_target = check_name_target
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.commit_sha = commit_sha
        self._db_trigger = db_trigger

    @property
    def targets_override(self) -> Optional[Set[str]]:
        return {self.check_name_target}

    @property
    def db_trigger(
        self,
    ) -> Union[PullRequestModel, GitBranchModel, ProjectReleaseModel]:
        return self._db_trigger

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result.pop("_db_trigger")
        return result


class CheckRerunCommitEvent(CheckRerunEvent):
    _db_trigger: GitBranchModel

    def __init__(
        self,
        project_url: str,
        repo_namespace: str,
        repo_name: str,
        commit_sha: str,
        git_ref: str,
        check_name_job: str,
        check_name_target: str,
        db_trigger,
    ):
        super().__init__(
            check_name_job=check_name_job,
            check_name_target=check_name_target,
            project_url=project_url,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            db_trigger=db_trigger,
            commit_sha=commit_sha,
        )
        self.identifier = git_ref
        self.git_ref = git_ref


class CheckRerunPullRequestEvent(CheckRerunEvent):
    _db_trigger: PullRequestModel

    def __init__(
        self,
        pr_id: int,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
        commit_sha: str,
        check_name_job: str,
        check_name_target: str,
        db_trigger,
    ):
        super().__init__(
            check_name_job=check_name_job,
            check_name_target=check_name_target,
            project_url=project_url,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            db_trigger=db_trigger,
            commit_sha=commit_sha,
            pr_id=pr_id,
        )
        self.identifier = str(pr_id)
        self.git_ref = None


class CheckRerunReleaseEvent(CheckRerunEvent):
    _db_trigger: ProjectReleaseModel

    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        tag_name: str,
        project_url: str,
        commit_sha: str,
        check_name_job: str,
        check_name_target: str,
        db_trigger,
    ):
        super().__init__(
            check_name_job=check_name_job,
            check_name_target=check_name_target,
            project_url=project_url,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            db_trigger=db_trigger,
            commit_sha=commit_sha,
        )
        self.tag_name = tag_name
        self.git_ref = tag_name
        self.identifier = tag_name


class InstallationEvent(Event):
    def __init__(
        self,
        installation_id: int,
        account_login: str,
        account_id: int,
        account_url: str,
        account_type: str,
        created_at: Union[int, float, str],
        repositories: List[str],
        sender_id: int,
        sender_login: str,
        status: AllowlistStatus = AllowlistStatus.waiting,
    ):
        super().__init__(created_at)
        self.installation_id = installation_id
        # account == namespace (user/organization) into which the app has been installed
        self.account_login = account_login
        self.account_id = account_id
        self.account_url = account_url
        self.account_type = account_type
        # repos within the account/namespace
        self.repositories = repositories
        # sender == user who installed the app into 'account'
        self.sender_id = sender_id
        self.sender_login = sender_login
        self.status = status

    @classmethod
    def from_event_dict(cls, event: dict):
        return InstallationEvent(
            installation_id=event.get("installation_id"),
            account_login=event.get("account_login"),
            account_id=event.get("account_id"),
            account_url=event.get("account_url"),
            account_type=event.get("account_type"),
            created_at=event.get("created_at"),
            repositories=event.get("repositories"),
            sender_id=event.get("sender_id"),
            sender_login=event.get("sender_login"),
        )

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["status"] = result["status"].value
        return result

    @property
    def package_config(self):
        return None

    @property
    def project(self):
        return self.get_project()

    def get_project(self):
        return None
