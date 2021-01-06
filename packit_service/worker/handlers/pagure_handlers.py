# MIT License
#
# Copyright (c) 2020 Red Hat, Inc.
#
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

import logging
from typing import List, Optional

from ogr.abstract import CommitStatus, PullRequest
from packit.config import JobConfig, PackageConfig
from packit_service.models import BugzillaModel
from packit_service.service.events import (
    EventData,
    PullRequestLabelAction,
    PullRequestLabelPagureEvent,
)
from packit_service.worker.handlers.abstract import (
    JobHandler,
    TaskName,
    reacts_to,
)
from packit_service.worker.psbugzilla import Bugzilla
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@reacts_to(event=PullRequestLabelPagureEvent)
class PagurePullRequestLabelHandler(JobHandler):
    task_name = TaskName.pagure_pr_label

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        data: EventData,
        labels: List[str],
        action: PullRequestLabelAction,
        base_repo_owner: str,
        base_repo_name: str,
        base_repo_namespace: str,
    ):
        super().__init__(
            package_config=package_config, job_config=job_config, data=data
        )
        self.labels = set(labels)
        self.action = action
        self.base_repo_owner = base_repo_owner
        self.base_repo_name = base_repo_name
        self.base_repo_namespace = base_repo_namespace

        self.pr: PullRequest = self.project.get_pr(self.data.pr_id)
        # lazy properties
        self._bz_model: Optional[BugzillaModel] = None
        self._bugzilla: Optional[Bugzilla] = None
        self._status_reporter: Optional[StatusReporter] = None

    @property
    def bz_model(self) -> Optional[BugzillaModel]:
        if self._bz_model is None:
            self._bz_model = BugzillaModel.get_by_pr(
                pr_id=self.data.pr_id,
                namespace=self.base_repo_namespace,
                repo_name=self.base_repo_name,
                project_url=self.data.project_url,
            )
        return self._bz_model

    @property
    def bugzilla(self) -> Bugzilla:
        if self._bugzilla is None:
            self._bugzilla = Bugzilla(
                url=self.service_config.bugzilla_url,
                api_key=self.service_config.bugzilla_api_key,
            )
        return self._bugzilla

    @property
    def status_reporter(self) -> StatusReporter:
        if not self._status_reporter:
            self._status_reporter = StatusReporter(
                self.project, self.data.commit_sha, self.data.pr_id
            )
        return self._status_reporter

    def _create_bug(self):
        """ Fill a Bugzilla bug and store in db. """
        bug_id, bug_url = self.bugzilla.create_bug(
            product="Red Hat Enterprise Linux 8",
            version="CentOS Stream",
            component=self.base_repo_name,
            summary=self.pr.title,
            description=f"Based on approved CentOS Stream pull-request: {self.pr.url}",
        )
        self._bz_model = BugzillaModel.get_or_create(
            pr_id=self.data.pr_id,
            namespace=self.base_repo_namespace,
            repo_name=self.base_repo_name,
            project_url=self.data.project_url,
            bug_id=bug_id,
            bug_url=bug_url,
        )

    def _attach_patch(self):
        """ Attach a patch from the pull request to the bug. """
        if not (self.bz_model and self.bz_model.bug_id):
            raise RuntimeError(
                "PagurePullRequestLabelHandler._attach_patch(): bug_id not set"
            )

        self.bugzilla.add_patch(
            bzid=self.bz_model.bug_id,
            content=self.pr.patch,
            file_name=f"pr-{self.data.pr_id}.patch",
        )

    def _set_status(self):
        """
        Set commit status & pull-request flag with bug id as a name and a link to the created bug.
        """
        if not (self.bz_model and self.bz_model.bug_id and self.bz_model.bug_url):
            raise RuntimeError(
                "PagurePullRequestLabelHandler._set_status(): bug_id or bug_url not set"
            )

        self.status_reporter.set_status(
            state=CommitStatus.success,
            description="Bugzilla bug created.",
            check_name=f"RHBZ#{self.bz_model.bug_id}",
            url=self.bz_model.bug_url,
        )

    def run(self) -> TaskResults:
        logger.debug(
            f"Handling labels/tags {self.labels} {self.action.value} to Pagure PR "
            f"{self.base_repo_owner}/{self.base_repo_namespace}/"
            f"{self.base_repo_name}/{self.data.identifier}"
        )
        if self.labels.intersection(self.service_config.pr_accepted_labels):
            if not self.bz_model:
                self._create_bug()
            self._attach_patch()
            self._set_status()
        else:
            logger.debug(
                f"We accept only {self.service_config.pr_accepted_labels} labels/tags"
            )
        return TaskResults(success=True)
