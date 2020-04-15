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
import logging
from typing import Union

from ogr.abstract import GitProject, CommitStatus
from ogr.services.pagure import PagureProject

logger = logging.getLogger(__name__)


class StatusReporter:
    def __init__(
        self, project: GitProject, commit_sha: str,
    ):
        self.project = project
        self.commit_sha = commit_sha

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

    def set_status(
        self, state: CommitStatus, description: str, check_name: str, url: str = "",
    ):
        # required because pagure api doesnt accept, empty url
        if isinstance(self.project, PagureProject):
            url = "https://wiki.centos.org/Manuals/ReleaseNotes/CentOSStream"

        logger.debug(f"Setting status for check '{check_name}': {description}")
        self.project.set_commit_status(
            self.commit_sha, state, url, description, check_name, trim=True
        )

    def get_statuses(self):
        self.project.get_commit_statuses(commit=self.commit_sha)
