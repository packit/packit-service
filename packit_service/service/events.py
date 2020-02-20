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

import requests
from ogr.abstract import GitProject
from packit.config import JobTriggerType, get_package_config_from_repo, PackageConfig

from packit_service.config import ServiceConfig, GithubPackageConfigGetter
from packit_service.models import CoprBuild
from packit_service.worker.copr_db import CoprBuildDB
from packit_service.worker.utils import get_copr_build_url_for_values

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
    approved_automatically = "approved_automatically"
    waiting = "waiting"
    approved_manually = "approved_manually"


class TestingFarmResult(str, enum.Enum):
    passed = "passed"
    failed = "failed"
    error = "error"
    running = "running"


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
        self, trigger: JobTriggerType, created_at: Union[int, float, str] = None
    ):
        self.trigger: JobTriggerType = trigger
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

    def get_package_config(self):
        raise NotImplementedError("Please implement me!")

    def get_project(self) -> GitProject:
        raise NotImplementedError("Please implement me!")

    def pre_check(self) -> bool:
        """
        Implement this method for those events, where you want to check if event properties are
        correct. If this method returns false during runtime, execution of service code is skipped.
        :return:
        """
        return True

    def __str__(self):
        return str(self.get_dict())

    def __repr__(self):
        return f"{self.__class__.__name__}({self.get_dict()})"


class AbstractGithubEvent(Event, GithubPackageConfigGetter):
    def __init__(self, trigger: JobTriggerType, project_url: str):
        super().__init__(trigger)
        self.project_url: str = project_url

    def get_project(self) -> GitProject:
        return ServiceConfig.get_service_config().get_project(url=self.project_url)


class ReleaseEvent(AbstractGithubEvent):
    def __init__(
        self, repo_namespace: str, repo_name: str, tag_name: str, https_url: str
    ):
        super().__init__(trigger=JobTriggerType.release, project_url=https_url)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.tag_name = tag_name

    def get_package_config(self) -> Optional[PackageConfig]:
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(), reference=self.tag_name, fail_when_missing=False
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config


class PullRequestEvent(AbstractGithubEvent):
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
        github_login: str,
    ):
        super().__init__(trigger=JobTriggerType.pull_request, project_url=https_url)
        self.action = action
        self.pr_id = pr_id
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.target_repo = target_repo
        self.commit_sha = commit_sha
        self.github_login = github_login

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


class PullRequestCommentEvent(AbstractGithubEvent):
    def __init__(
        self,
        action: PullRequestCommentAction,
        pr_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        base_ref: Optional[str],
        target_repo: str,
        https_url: str,
        github_login: str,
        comment: str,
        commit_sha: str = "",
    ):
        super().__init__(trigger=JobTriggerType.comment, project_url=https_url)
        self.action = action
        self.pr_id = pr_id
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.commit_sha = commit_sha
        self.target_repo = target_repo
        self.github_login = github_login
        self.comment = comment

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


class IssueCommentEvent(AbstractGithubEvent):
    def __init__(
        self,
        action: IssueCommentAction,
        issue_id: int,
        base_repo_namespace: str,
        base_repo_name: str,
        target_repo: str,
        https_url: str,
        github_login: str,
        comment: str,
        tag_name: str = "",
        base_ref: Optional[
            str
        ] = "master",  # default is master when working with issues
    ):
        super().__init__(trigger=JobTriggerType.comment, project_url=https_url)
        self.action = action
        self.issue_id = issue_id
        self.base_repo_namespace = base_repo_namespace
        self.base_repo_name = base_repo_name
        self.base_ref = base_ref
        self.tag_name = tag_name
        self.target_repo = target_repo
        self.github_login = github_login
        self.comment = comment

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["action"] = result["action"].value
        return result

    def get_package_config(self) -> Optional[PackageConfig]:
        releases = self.get_project().get_releases()

        if releases:
            self.tag_name = releases[0].tag_name
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
        super().__init__(JobTriggerType.installation, created_at)
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
        ref: str,
        branch: str,
        msg_id: str,
        project_url: str,
    ):
        super().__init__(JobTriggerType.commit)
        self.topic = FedmsgTopic(topic)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.ref = ref
        self.branch = branch
        self.msg_id = msg_id
        self.project_url = project_url

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["topic"] = result["topic"].value
        return result

    def get_package_config(self):
        return get_package_config_from_repo(self.get_project(), self.ref)

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
        ref: str,
        https_url: str,
        commit_sha: str,
    ):
        super().__init__(
            trigger=JobTriggerType.testing_farm_results, project_url=https_url
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
        self.ref: str = ref
        self.commit_sha: str = commit_sha

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["result"] = result["result"].value
        return result

    def get_package_config(self):
        package_config: PackageConfig = self.get_package_config_from_repo(
            project=self.get_project(), reference=self.ref, fail_when_missing=False
        )
        if not package_config:
            return None
        package_config.upstream_project_url = self.project_url
        return package_config


# Wait, what? copr build event doesn't sound like github event
class CoprBuildEvent(AbstractGithubEvent):
    def __init__(
        self,
        topic: str,
        build_id: int,
        build: dict,
        chroot: str,
        status: int,
        owner: str,
        project_name: str,
        pkg: str,
        build_pg: Optional[CoprBuild] = None,
    ):
        if build_pg:
            self.pr_id = build_pg.pr.pr_id
            self.commit_sha = build_pg.commit_sha
            self.ref = self.commit_sha  # ref should be name of the branch, not a hash
            self.base_repo_name = build_pg.pr.project.repo_name
            self.base_repo_namespace = build_pg.pr.project.namespace
            # FIXME: hardcoded, move this to PG
            https_url = f"https://github.com/{self.base_repo_namespace}/{self.base_repo_name}.git"
        else:
            self.pr_id = build.get("pr_id")
            self.ref = build.get("ref", "")
            self.commit_sha = build.get("commit_sha", "")
            self.base_repo_name = build.get("repo_name")
            self.base_repo_namespace = build.get("repo_namespace")
            https_url = build["https_url"]

        super().__init__(trigger=JobTriggerType.commit, project_url=https_url)
        self.topic = FedmsgTopic(topic)
        self.build_id = build_id
        self.build = build
        self.chroot = chroot
        self.status = status
        self.owner = owner
        self.project_name = project_name
        self.pkg = pkg
        self.build_pg = build_pg

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
    ) -> Optional["CoprBuildEvent"]:
        """ Return cls instance or None if build_id not in CoprBuildDB"""
        # pg
        build_pg = CoprBuild.get_by_build_id(str(build_id), chroot)
        build = None
        if not build_pg:
            # let's try redis now
            build = CoprBuildDB().get_build(build_id)
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
            build_pg=build_pg,
        )

    def pre_check(self):
        if not self.build and not self.build_pg:
            logger.warning("Copr build is not handled by this deployment.")
            return False

        return True

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        d = self.__dict__
        d.pop("build_pg")
        result = super().get_dict(d)
        result["topic"] = result["topic"].value
        return result

    def get_package_config(self) -> Optional[PackageConfig]:
        project = self.get_project()
        if not project:
            return None

        package_config: PackageConfig = self.get_package_config_from_repo(
            project=project, reference=self.ref, fail_when_missing=False
        )
        if not package_config:
            return None

        package_config.upstream_project_url = self.project_url
        return package_config


def get_copr_build_url(event: CoprBuildEvent) -> str:
    return get_copr_build_url_for_values(
        event.owner, event.project_name, event.build_id
    )


def get_copr_build_logs_url(event: CoprBuildEvent) -> str:
    return (
        f"https://copr-be.cloud.fedoraproject.org/results/{event.owner}/"
        f"{event.project_name}/{event.chroot}/"
        f"{event.build_id:08d}-{event.pkg}/builder-live.log.gz"
    )


def copr_url_from_event(event: CoprBuildEvent):
    """
    Get url to builder-live.log.gz bound to single event
    :param event: fedora messaging event from topic copr.build.start or copr.build.end
    :return: reachable url
    """
    url = get_copr_build_logs_url(event)
    # make sure we provide valid url in status, let sentry handle if not
    try:
        logger.debug(f"Reaching url {url}")
        r = requests.head(url)
        r.raise_for_status()
    except requests.RequestException:
        # we might want sentry to know but don't want to start handling things?
        logger.error(f"Failed to reach url with copr chroot build result.")
        url = get_copr_build_url_for_values(
            event.owner, event.project_name, event.build_id
        )
    # return the frontend URL no matter what
    # we don't want to fail on this step; the error log is just enough
    return url
