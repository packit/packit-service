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
This file contains helper classes for events.
"""
from typing import Optional

from ogr.abstract import GitProject
from packit_service.models import (
    AbstractTriggerDbType,
    GitBranchModel,
    IssueModel,
    ProjectReleaseModel,
    PullRequestModel,
)


class AddReleaseDbTrigger:
    tag_name: str
    repo_namespace: str
    repo_name: str
    project_url: str

    @property
    def commit_sha(self):
        """
        To please the mypy.
        """
        raise NotImplementedError()

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return ProjectReleaseModel.get_or_create(
            tag_name=self.tag_name,
            namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
            commit_hash=self.commit_sha,
        )


class AddPullRequestDbTrigger:
    pr_id: int
    project: GitProject
    project_url: str

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return PullRequestModel.get_or_create(
            pr_id=self.pr_id,
            namespace=self.project.namespace,
            repo_name=self.project.repo,
            project_url=self.project_url,
        )


class AddIssueDbTrigger:
    issue_id: int
    repo_namespace: str
    repo_name: str
    project_url: str

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return IssueModel.get_or_create(
            issue_id=self.issue_id,
            namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
        )


class AddBranchPushDbTrigger:
    git_ref: str
    repo_namespace: str
    repo_name: str
    project_url: str

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        return GitBranchModel.get_or_create(
            branch_name=self.git_ref,
            namespace=self.repo_namespace,
            repo_name=self.repo_name,
            project_url=self.project_url,
        )
