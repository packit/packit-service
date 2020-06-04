# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.
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
import hashlib
import logging
from typing import Optional, Union

from ogr.abstract import GitProject, CommitStatus
from ogr.services.pagure import PagureProject

logger = logging.getLogger(__name__)


class StatusReporter:
    def __init__(
        self, project: GitProject, commit_sha: str, pr_id: Optional[int] = None
    ):
        logger.debug(
            f"Status reporter will report for {project}, commit={commit_sha}, pr={pr_id}"
        )
        self.project = project
        self.commit_sha = commit_sha
        self.pr_id = pr_id

    def report(
        self,
        state: CommitStatus,
        description: str,
        url: str = "",
        check_names: Union[str, list, None] = None,
    ) -> None:
        """
        set commit check status

        :param state: state accepted by github
        :param description: the long text
        :param url: url to point to (logs usually)
        :param check_names: those in bold
        """

        if not check_names:
            logger.warning("No checks to set status for.")
            return

        elif isinstance(check_names, str):
            check_names = [check_names]

        for check in check_names:
            self.set_status(
                state=state, description=description, check_name=check, url=url
            )

    def __set_pull_request_status(
        self, check_name: str, description: str, url: str, state: CommitStatus
    ):
        if self.pr_id is None:
            return
        pr = self.project.get_pr(self.pr_id)
        if hasattr(pr, "set_flag") and pr.head_commit == self.commit_sha:
            logger.debug("Setting the PR status (pagure only).")
            pr.set_flag(
                username=check_name,
                comment=description,
                url=url,
                status=state,
                # For Pagure: generate a custom uid from the check_name,
                # so that we can update flags we set previously,
                # instead of creating new ones.
                uid=hashlib.md5(check_name.encode()).hexdigest(),
            )

    def set_status(
        self, state: CommitStatus, description: str, check_name: str, url: str = "",
    ):
        # Required because Pagure API doesn't accept empty url.
        if not url and isinstance(self.project, PagureProject):
            url = "https://wiki.centos.org/Manuals/ReleaseNotes/CentOSStream"

        logger.debug(f"Setting status for check '{check_name}': {description}")
        self.project.set_commit_status(
            self.commit_sha, state, url, description, check_name, trim=True
        )
        # Also set the status of the pull-request for forges which don't do
        # this automatically based on the flags on the last commit in the PR.
        self.__set_pull_request_status(check_name, description, url, state)

    def get_statuses(self):
        self.project.get_commit_statuses(commit=self.commit_sha)
