# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

from ogr.abstract import GitProject

from packit_service.worker.events.event import EventData
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.reporting.reporters.base import StatusReporter

logger = logging.getLogger(__name__)


class FedoraCIHelper:
    status_name: str = "Packit - scratch build"

    def __init__(
        self,
        project: GitProject,
        metadata: EventData,
    ):
        self.project = project
        self.metadata = metadata

        self._status_reporter = None

    @property
    def status_reporter(self) -> StatusReporter:
        if not self._status_reporter:
            self._status_reporter = StatusReporter.get_instance(
                project=self.project,
                commit_sha=self.metadata.commit_sha,
                pr_id=self.metadata.pr_id,
                packit_user=None,
            )
        return self._status_reporter

    def report(self, state: BaseCommitStatus, description: str, url: str):
        self.status_reporter.set_status(
            state=state,
            description=description,
            url=url,
            check_name=self.status_name,
        )
