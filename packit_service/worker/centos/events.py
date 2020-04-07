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
from typing import Optional, Union, Dict

from ogr import PagureService
from ogr.abstract import GitProject
from packit.config import PackageConfig

from packit_service.config import ServiceConfig, PagurePackageConfigGetter
from packit_service.constants import WHITELIST_CONSTANTS
from packit_service.models import (
    AbstractTriggerDbType,
    TestingFarmResult,
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


class AbstractPagureEvent(Event, PagurePackageConfigGetter):
    def __init__(self, trigger: TheJobTriggerType, project_url: str):
        super().__init__(trigger)
        self.project_url: str = project_url
        self.git_ref: Optional[str] = None  # git ref that can be 'git checkout'-ed
        self.identifier: Optional[str] = (
            None  # will be shown to users -- e.g. in logs or in the copr-project name
        )
        # bad implementation but somewhere it is called without
        # possibility to pass required argument, have to be refactored
        self.get_project_kwargs = dict(
            service_mapping_update={"git.stg.centos.org": PagureService}
        )

    def get_project(self, get_project_kwargs: dict = None) -> GitProject:
        return ServiceConfig.get_service_config().get_project(
            url=self.project_url,
            get_project_kwargs=get_project_kwargs or self.get_project_kwargs,
        )


class PagurePushEvent(AbstractPagureEvent):
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


class PagurePullRequestCommentEvent(AbstractPagureEvent):
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


class PagurePullRequestEvent(AbstractPagureEvent):
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
