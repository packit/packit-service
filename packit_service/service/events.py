# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
This file defines classes for events which are sent by GitHub or FedMsg.
"""
import copy
import enum
import logging

from datetime import datetime, timezone
from typing import Optional, List, Union, Dict

from ogr.abstract import GitProject
from packit.config import (
    get_package_config_from_repo,
    PackageConfig,
)
from packit_service.config import (
    ServiceConfig,
    GithubPackageConfigGetter,
    PagurePackageConfigGetter,
)

from packit_service.constants import WHITELIST_CONSTANTS
from packit_service.models import (
    CoprBuildModel,
    AbstractTriggerDbType,
    JobTriggerModelType,
    TestingFarmResult,
    TFTTestRunModel,
)
from packit_service.service.db_triggers import (
    AddReleaseDbTrigger,
    AddPullRequestDbTrigger,
    AddBranchPushDbTrigger,
    AddIssueDbTrigger,
)

logger = logging.getLogger(__name__)


class PullRequestAction(enum.Enum):
    opened = "opened"
    reopened = "reopened"
    synchronize = "synchronize"


class PullRequestCommentAction(enum.Enum):
    created = "created"
    edited = "edited"


class IssueCommentAction(enum.Enum):
    created = "created"
    edited = "edited"


class FedmsgTopic(enum.Enum):
    dist_git_push = "org.fedoraproject.prod.git.receive"
    copr_build_finished = "org.fedoraproject.prod.copr.build.end"
    copr_build_started = "org.fedoraproject.prod.copr.build.start"
    pr_flag_added = "org.fedoraproject.prod.pagure.pull-request.flag.added"


class WhitelistStatus(enum.Enum):
    approved_automatically = WHITELIST_CONSTANTS["approved_automatically"]
    waiting = WHITELIST_CONSTANTS["waiting"]
    approved_manually = WHITELIST_CONSTANTS["approved_manually"]


class TheJobTriggerType(str, enum.Enum):
    release = "release"
    pull_request = "pull_request"
    push = "push"
    commit = "commit"
    installation = "installation"
    testing_farm_results = "testing_farm_results"
    pr_comment = "pr_comment"
    issue_comment = "issue_comment"
    copr_start = "copr_start"
    copr_end = "copr_end"


class TestResult(dict):
    def __init__(self, name: str, result: TestingFarmResult, log_url: str):
        dict.__init__(self, name=name, result=result, log_url=log_url)
        self.name = name
        self.result = result
        self.log_url = log_url

    def __str__(self) -> str:
        return f"TestResult(name='{self.name}', result={self.result}, log_url='{self.log_url}')"

    def __repr__(self):
        return self.__str__()

    def __hash__(self) -> int:  # type: ignore
        return hash(self.__str__())

    def __eq__(self, o: object) -> bool:
        if not isinstance(o, TestResult):
            return False

        return (
            self.name == o.name
            and self.result == o.result
            and self.log_url == o.log_url
        )


class Event:
    def __init__(
        self, trigger: TheJobTriggerType, created_at: Union[int, float, str] = None
    ):
        self.trigger: TheJobTriggerType = trigger
        self.created_at: datetime
        if created_at:
            if isinstance(created_at, (int, float)):
                self.created_at = datetime.fromtimestamp(created_at, timezone.utc)
            elif isinstance(created_at, str):
                # https://stackoverflow.com/questions/127803/how-do-i-parse-an-iso-8601-formatted-date/49784038
                created_at = created_at.replace("Z", "+00:00")
                self.created_at = datetime.fromisoformat(created_at)
        else:
            self.created_at = datetime.now()

    @staticmethod
    def ts2str(event: dict):
        """
        Convert 'created_at' key from timestamp to iso 8601 time format.
        This would normally be in a from_dict(), but we don't have such method.
        In api/* we read events from db and directly serve them to clients.
        Deserialize (from_dict) and serialize (to_dict) every entry
        just to do this ts2str would be waste of resources.
        """
        created_at = event.get("created_at")
        if isinstance(created_at, int):
            event["created_at"] = datetime.fromtimestamp(created_at).isoformat()
        return event

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        d = default_dict or self.__dict__
        d = copy.deepcopy(d)
        # whole dict have to be JSON serializable because of redis
        d["trigger"] = d["trigger"].value
        d["created_at"] = int(d["created_at"].timestamp())
        return d

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return None

    def get_package_config(self):
        raise NotImplementedError("Please implement me!")

    def get_project(self) -> GitProject:
        raise NotImplementedError("Please implement me!")

    def pre_check(self) -> bool:
        """
        Implement this method for those events, where you want to check if event properties are
        correct. If this method returns False during runtime, execution of service code is skipped.

        :return: False if we can ignore the event
        """
        return True

    def __str__(self):
        return str(self.get_dict())

    def __repr__(self):
        return f"{self.__class__.__name__}({self.get_dict()})"


class AbstractGithubEvent(Event, GithubPackageConfigGetter):
    def __init__(self, trigger: TheJobTriggerType, project_url: str):
        super().__init__(trigger)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None  # git ref that can be 'git checkout'-ed
        self.identifier: Optional[str] = (
            None  # will be shown to users -- e.g. in logs or in the copr-project name
        )

    def get_project(self) -> GitProject:
        return ServiceConfig.get_service_config().get_project(url=self.project_url)


class ReleaseEvent(AddReleaseDbTrigger, AbstractGithubEvent):
    def __init__(
        self, repo_namespace: str, repo_name: str, tag_name: str, https_url: str
    ):
        super().__init__(trigger=TheJobTriggerType.release, project_url=https_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.tag_name = tag_name
        self.git_ref = tag_name
        self.identifier = tag_name

        self._commit_sha = None

    @property
    def commit_sha(self) -> str:
        if not self._commit_sha:
            self._commit_sha = self.get_project().get_sha_from_tag(
                tag_name=self.tag_name
            )
        return self._commit_sha

    def get_package_config(self) -> Optional[PackageConfig]:
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(), reference=self.tag_name, fail_when_missing=False
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config


class PushGitHubEvent(AddBranchPushDbTrigger, AbstractGithubEvent):
    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        https_url: str,
        commit_sha: str,
    ):
        super().__init__(trigger=TheJobTriggerType.push, project_url=https_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.git_ref = git_ref
        self.commit_sha = commit_sha
        self.identifier = git_ref

    def get_package_config(self) -> Optional[PackageConfig]:
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(),
            reference=self.commit_sha,
            fail_when_missing=False,
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config


class PullRequestEvent(AddPullRequestDbTrigger, AbstractGithubEvent):
    def __init__(
        self,
        action: PullRequestAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_ref: str,
        target_repo: str,
        https_url: str,
        commit_sha: str,
        user_login: str,
    ):
        super().__init__(trigger=TheJobTriggerType.pull_request, project_url=https_url)
        self.action = action
        self.pr_id = pr_id
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.target_repo = target_repo
        self.commit_sha = commit_sha
        self.user_login = user_login
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_package_config(self) -> Optional[PackageConfig]:
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(),
            reference=self.base_ref,
            pr_id=self.pr_id,
            fail_when_missing=False,
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config


class PullRequestCommentEvent(AddPullRequestDbTrigger, AbstractGithubEvent):
    def __init__(
        self,
        action: PullRequestCommentAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_ref: Optional[str],
        target_repo: str,
        https_url: str,
        user_login: str,
        comment: str,
        commit_sha: str = "",
    ):
        super().__init__(trigger=TheJobTriggerType.pr_comment, project_url=https_url)
        self.action = action
        self.pr_id = pr_id
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.commit_sha = commit_sha
        self.target_repo = target_repo
        self.user_login = user_login
        self.comment = comment
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_package_config(self) -> Optional[PackageConfig]:
        if not self.base_ref:
            self.base_ref = self.get_project().get_pr_info(self.pr_id).source_branch
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(),
            reference=self.base_ref,
            pr_id=self.pr_id,
            fail_when_missing=False,
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config


class IssueCommentEvent(AddIssueDbTrigger, AbstractGithubEvent):
    def __init__(
        self,
        action: IssueCommentAction,
        issue_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        target_repo: str,
        https_url: str,
        user_login: str,
        comment: str,
        tag_name: str = "",
        base_ref: Optional[
            str
        ] = "master",  # default is master when working with issues
    ):
        super().__init__(trigger=TheJobTriggerType.issue_comment, project_url=https_url)
        self.action = action
        self.issue_id = issue_id
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self._tag_name = tag_name
        self.target_repo = target_repo
        self.user_login = user_login
        self.comment = comment
        self.identifier = str(issue_id)

    @property
    def tag_name(self):
        if not self._tag_name:
            releases = self.get_project().get_releases()
            self._tag_name = releases[0].tag_name if releases else ""
        return self._tag_name

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_package_config(self) -> Optional[PackageConfig]:
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(), reference=self.tag_name, fail_when_missing=False
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config


class InstallationEvent(Event):
    def __init__(
        self,
        installation_id: int,
        account_login: str,
        account_id: int,
        account_url: str,
        account_type: str,
        created_at: int,
        repositories: List[str],
        sender_id: int,
        sender_login: str,
        status: WhitelistStatus = WhitelistStatus.waiting,
    ):
        super().__init__(TheJobTriggerType.installation, created_at)
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

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["status"] = result["status"].value
        return result


class DistGitEvent(Event):
    def __init__(
        self,
        topic: str,
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        branch: str,
        msg_id: str,
        project_url: str,
    ):
        super().__init__(trigger=TheJobTriggerType.commit)
        self.topic = FedmsgTopic(topic)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.git_ref = git_ref
        self.branch = branch
        self.msg_id = msg_id
        self.project_url = project_url
        self.identifier = branch

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["topic"] = result["topic"].value
        return result

    def get_package_config(self):
        return get_package_config_from_repo(self.get_project(), self.git_ref)

    def get_project(self) -> GitProject:
        return ServiceConfig.get_service_config().get_project(self.project_url)


class TestingFarmResultsEvent(AbstractGithubEvent):
    def __init__(
        self,
        pipeline_id: str,
        result: TestingFarmResult,
        environment: str,
        message: str,
        log_url: str,
        copr_repo_name: str,
        copr_chroot: str,
        tests: List[TestResult],
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        https_url: str,
        commit_sha: str,
    ):
        super().__init__(
            trigger=TheJobTriggerType.testing_farm_results, project_url=https_url
        )
        self.pipeline_id = pipeline_id
        self.result = result
        self.environment = environment
        self.message = message
        self.log_url = log_url
        self.copr_repo_name = copr_repo_name
        self.copr_chroot = copr_chroot
        self.tests = tests
        self.repo_name = repo_name
        self.repo_namespace = repo_namespace
        self.git_ref: str = git_ref
        self.commit_sha: str = commit_sha
        self.identifier = git_ref

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["result"] = result["result"].value
        return result

    def get_package_config(self):
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(), reference=self.git_ref, fail_when_missing=False
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        run_model = TFTTestRunModel.get_by_pipeline_id(pipeline_id=self.pipeline_id)
        if not run_model:
            return None
        return run_model.job_trigger.get_trigger_object()


# Wait, what? copr build event doesn't sound like github event
class CoprBuildEvent(AbstractGithubEvent):
    build: Optional[CoprBuildModel]

    def __init__(
        self,
        topic: str,
        build_id: int,
        build: CoprBuildModel,
        chroot: str,
        status: int,
        owner: str,
        project_name: str,
        pkg: str,
        logs_url: str,
        started_on: datetime,
        ended_on: datetime,
    ):
        trigger_db = build.job_trigger.get_trigger_object()
        self.commit_sha = build.commit_sha
        self.base_repo_name = trigger_db.project.repo_name
        self.base_repo_namespace = trigger_db.project.namespace
        # FIXME: hardcoded, move this to PG
        https_url = (
            f"https://github.com/{self.base_repo_namespace}/{self.base_repo_name}.git"
        )
        git_ref = self.commit_sha  # ref should be name of the branch, not a hash

        self.topic = FedmsgTopic(topic)
        if self.topic == FedmsgTopic.copr_build_started:
            trigger = TheJobTriggerType.copr_start
        elif self.topic == FedmsgTopic.copr_build_finished:
            trigger = TheJobTriggerType.copr_end
        else:
            raise ValueError(f"Unknown topic for CoprEvent: '{self.topic}'")

        super().__init__(trigger=trigger, project_url=https_url)

        self.git_ref = git_ref
        self.build_id = build_id
        self.build = build
        self.chroot = chroot
        self.status = status
        self.owner = owner
        self.project_name = project_name
        self.pkg = pkg
        self.logs_url = logs_url
        self.started_on = started_on
        self.ended_on = ended_on

        trigger_type = build.job_trigger.type
        trigger_db = build.job_trigger.get_trigger_object()
        if trigger_type == JobTriggerModelType.pull_request:
            self.pr_id = trigger_db.pr_id
            self.identifier = str(trigger_db.pr_id)
        elif trigger_type == JobTriggerModelType.release:
            self.pr_id = None
            self.identifier = trigger_db.tag_name
        elif trigger_type == JobTriggerModelType.branch_push:
            self.pr_id = None
            self.identifier = trigger_db.name

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return self.build.job_trigger.get_trigger_object()

    @classmethod
    def from_build_id(
        cls,
        topic: str,
        build_id: int,
        chroot: str,
        status: int,
        owner: str,
        project_name: str,
        pkg: str,
        logs_url: str,
        started_on: datetime,
        ended_on: datetime,
    ) -> Optional["CoprBuildEvent"]:
        """ Return cls instance or None if build_id not in CoprBuildDB"""
        build = CoprBuildModel.get_by_build_id(str(build_id), chroot)
        if not build:
            logger.warning(f"Build id: {build_id} not in CoprBuildDB")
            return None
        return cls(
            topic,
            build_id,
            build,
            chroot,
            status,
            owner,
            project_name,
            pkg,
            logs_url,
            started_on,
            ended_on,
        )

    def pre_check(self):
        if not self.build:
            logger.warning("Copr build is not handled by this deployment.")
            return False

        return True

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        d = self.__dict__
        build = d.pop("build")
        result = super().get_dict(d)
        result["topic"] = result["topic"].value
        self.build = build
        return result

    def get_package_config(self) -> Optional[PackageConfig]:
        project = self.get_project()
        if not project:
            return None

        package_config: PackageConfig = self.get_package_config_from_repo(
            project=project, reference=self.commit_sha, fail_when_missing=False
        )
        if not package_config:
            return None

        package_config.upstream_project_url = self.project_url
        return package_config


def get_copr_build_logs_url(event: CoprBuildEvent) -> str:
    return (
        f"https://copr-be.cloud.fedoraproject.org/results/{event.owner}/"
        f"{event.project_name}/{event.chroot}/"
        f"{event.build_id:08d}-{event.pkg}/builder-live.log.gz"
    )


class AbstractPagureEvent(Event, PagurePackageConfigGetter):
    def __init__(self, trigger: TheJobTriggerType, project_url: str):
        super().__init__(trigger)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None  # git ref that can be 'git checkout'-ed
        self.identifier: Optional[str] = (
            None  # will be shown to users -- e.g. in logs or in the copr-project name
        )

    def get_project(self) -> GitProject:
        return ServiceConfig.get_service_config().get_project(url=self.project_url)


class PushPagureEvent(AbstractPagureEvent):
    def __init__(
        self,
        repo_namespace: str,
        repo_name: str,
        git_ref: str,
        https_url: str,
        commit_sha: str,
    ):
        super().__init__(trigger=TheJobTriggerType.push, project_url=https_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.git_ref = git_ref
        self.commit_sha = commit_sha
        self.identifier = git_ref

    def get_package_config(self) -> Optional[PackageConfig]:
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(),
            reference=self.commit_sha,
            fail_when_missing=False,
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config


class PullRequestCommentPagureEvent(AddPullRequestDbTrigger, AbstractPagureEvent):
    def __init__(
        self,
        action: PullRequestCommentAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_ref: Optional[str],
        target_repo: str,
        https_url: str,
        user_login: str,
        comment: str,
        commit_sha: str = "",
    ):
        super().__init__(trigger=TheJobTriggerType.pr_comment, project_url=https_url)
        self.action = action
        self.pr_id = pr_id
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.commit_sha = commit_sha
        self.target_repo = target_repo
        self.user_login = user_login
        self.comment = comment
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result


class PullRequestPagureEvent(AddPullRequestDbTrigger, AbstractPagureEvent):
    def __init__(
        self,
        action: PullRequestAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_ref: str,
        target_repo: str,
        https_url: str,
        commit_sha: str,
        user_login: str,
    ):
        super().__init__(trigger=TheJobTriggerType.pull_request, project_url=https_url)
        self.action = action
        self.pr_id = pr_id
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.target_repo = target_repo
        self.commit_sha = commit_sha
        self.user_login = user_login
        self.identifier = str(pr_id)
        self.git_ref = None  # pr_id will be used for checkout
        self.https_url = https_url

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_package_config(self) -> Optional[PackageConfig]:
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(),
            reference=self.commit_sha,
            fail_when_missing=False,
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config
