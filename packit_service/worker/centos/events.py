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
import logging
from typing import Optional, Dict

from ogr import PagureService
from ogr.abstract import GitProject
from packit.config import PackageConfig

from packit_service.config import ServiceConfig, PagurePackageConfigGetter
from packit_service.service.db_triggers import AddPullRequestDbTrigger
from packit_service.service.events import (
    Event,
    TheJobTriggerType,
    PullRequestCommentAction,
    PullRequestAction,
)

logger = logging.getLogger(__name__)


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


class PullRequestCommentPagureEvent(AbstractPagureEvent):
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
