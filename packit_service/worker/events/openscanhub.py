# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from logging import getLogger
from packit_service.worker.events import Event

logger = getLogger(__name__)


class OpenScanHubTaskFinishEvent(Event):
    def __init__(
        self,
        task_id: int,
        issues_added_url: str,
        issues_fixed_url: str,
        scan_results_url: str,
    ):
        super().__init__()

        self.task_id = task_id
        self.issues_added_url = issues_added_url
        self.issues_fixed_url = issues_fixed_url
        self.scan_results_url = scan_results_url
