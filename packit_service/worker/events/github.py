# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Dict, Optional, Union, List, Set

from ogr.abstract import GitProject, Comment

from packit_service.models import (
    AllowlistStatus,
    GitBranchModel,
    ProjectReleaseModel,
    PullRequestModel,
    ProjectEventModel,
    AbstractProjectObjectDbType,
)
from packit_service.service.db_project_events import (
    AddPullRequestEventToDb,
    AddBranchPushEventToDb,
    AddReleaseEventToDb,
)
from packit_service.worker.events.comment import (
    AbstractPRCommentEvent,
    AbstractIssueCommentEvent,
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
    def __init__(self, project_url: str, pr_id: Optional[int] = None, **kwargs):
        super().__init__(pr_id=pr_id)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None  # git ref that can be 'git checkout'-ed
        self.identifier: Optional[
            str
        ] = None  # will be shown to users -- e.g. in logs or in the copr-project name


class ReleaseEvent(AddReleaseEventToDb, AbstractGithubEvent):
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

    def get_dict(
        self, default_dict: Optional[Dict] = None, store_event: bool = False
    ) -> dict:
        result = super().get_dict()
        result["commit_sha"] = self.commit_sha
        return result


class PushGitHubEvent(AddBranchPushEventToDb, AbstractGithubEvent):
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


class PullRequestGithubEvent(AddPullRequestEventToDb, AbstractGithubEvent):
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
        actor: str,
    ) -> None:
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.action = action
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.target_repo_namespace = target_repo_namespace
        self.target_repo_name = target_repo_name
        self.commit_sha = commit_sha
        self.actor = actor
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout

    def get_dict(
        self, default_dict: Optional[Dict] = None, store_event: bool = False
    ) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> Optional[GitProject]:
        return None  # With Github app, we cannot work with fork repo


class PullRequestCommentGithubEvent(AbstractPRCommentEvent, AbstractGithubEvent):
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
        actor: str,
        comment: str,
        comment_id: int,
        commit_sha: Optional[str] = None,
        comment_object: Optional[Comment] = None,
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
        self.actor = actor
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout

    def get_dict(
        self, default_dict: Optional[Dict] = None, store_event: bool = False
    ) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_base_project(self) -> Optional[GitProject]:
        return None  # With Github app, we cannot work with fork repo


class IssueCommentEvent(AbstractIssueCommentEvent, AbstractGithubEvent):
    def __init__(
        self,
        action: IssueCommentAction,
        issue_id: int,
        repo_namespace: str,
        repo_name: str,
        target_repo: str,
        project_url: str,
        actor: str,
        comment: str,
        comment_id: int,
        tag_name: str = "",
        base_ref: Optional[
            str
        ] = "master",  # default is master when working with issues
        comment_object: Optional[Comment] = None,
        dist_git_project_url=None,
    ) -> None:
        super().__init__(
            issue_id=issue_id,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            project_url=project_url,
            comment=comment,
            comment_id=comment_id,
            tag_name=tag_name,
            comment_object=comment_object,
            dist_git_project_url=dist_git_project_url,
        )
        self.action = action
        self.actor = actor
        self.base_ref = base_ref
        self.target_repo = target_repo
        self.identifier = str(issue_id)

    def get_dict(
        self, default_dict: Optional[Dict] = None, store_event: bool = False
    ) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result


class CheckRerunEvent(AbstractGithubEvent):
    def __init__(
        self,
        check_name_job: str,
        check_name_target: str,
        project_url: str,
        repo_namespace: str,
        repo_name: str,
        db_project_event: ProjectEventModel,
        commit_sha: str,
        actor: str,
        pr_id: Optional[int] = None,
        job_identifier: Optional[str] = None,
    ):
        super().__init__(project_url=project_url, pr_id=pr_id)
        self.check_name_job = check_name_job
        self.check_name_target = check_name_target
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.commit_sha = commit_sha
        self.actor = actor
        self._db_project_event = db_project_event
        self._db_project_object: AbstractProjectObjectDbType = (
            db_project_event.get_project_event_object()
        )
        self.job_identifier = job_identifier

    @property
    def build_targets_override(self) -> Optional[Set[str]]:
        if self.check_name_job in {"rpm-build", "production-build", "koji-build"}:
            return {self.check_name_target}
        return None

    @property
    def tests_targets_override(self) -> Optional[Set[str]]:
        if self.check_name_job == "testing-farm":
            return {self.check_name_target}
        return None

    @property
    def branches_override(self) -> Optional[Set[str]]:
        if self.check_name_job == "propose-downstream":
            return {self.check_name_target}
        return None


class CheckRerunCommitEvent(CheckRerunEvent):
    _db_project_object: GitBranchModel

    def __init__(
        self,
        project_url: str,
        repo_namespace: str,
        repo_name: str,
        commit_sha: str,
        git_ref: str,
        check_name_job: str,
        check_name_target: str,
        db_project_event,
        actor: str,
        job_identifier: Optional[str] = None,
    ):
        super().__init__(
            check_name_job=check_name_job,
            check_name_target=check_name_target,
            project_url=project_url,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            db_project_event=db_project_event,
            commit_sha=commit_sha,
            actor=actor,
            job_identifier=job_identifier,
        )
        self.identifier = git_ref
        self.git_ref = git_ref


class CheckRerunPullRequestEvent(CheckRerunEvent):
    _db_project_object: PullRequestModel

    def __init__(
        self,
        pr_id: int,
        repo_namespace: str,
        repo_name: str,
        project_url: str,
        commit_sha: str,
        check_name_job: str,
        check_name_target: str,
        db_project_event,
        actor: str,
        job_identifier: Optional[str] = None,
    ):
        super().__init__(
            check_name_job=check_name_job,
            check_name_target=check_name_target,
            project_url=project_url,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            db_project_event=db_project_event,
            commit_sha=commit_sha,
            pr_id=pr_id,
            actor=actor,
            job_identifier=job_identifier,
        )
        self.identifier = str(pr_id)
        self.git_ref = None


class CheckRerunReleaseEvent(CheckRerunEvent):
    _db_project_object: ProjectReleaseModel

    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        tag_name: str,
        project_url: str,
        commit_sha: str,
        check_name_job: str,
        check_name_target: str,
        db_project_event,
        actor: str,
        job_identifier: Optional[str] = None,
    ):
        super().__init__(
            check_name_job=check_name_job,
            check_name_target=check_name_target,
            project_url=project_url,
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            db_project_event=db_project_event,
            commit_sha=commit_sha,
            actor=actor,
            job_identifier=job_identifier,
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
        self.actor = account_login
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

    def get_dict(
        self, default_dict: Optional[Dict] = None, store_event: bool = False
    ) -> dict:
        result = super().get_dict()
        result["status"] = result["status"].value
        return result

    @property
    def packages_config(self):
        return None

    @property
    def project(self):
        return self.get_project()

    def get_project(self):
        return None
