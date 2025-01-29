# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import enum

from .abstract import OpenScanHubEvent


class Status(str, enum.Enum):
    success = "success"
    cancel = "cancel"
    interrupt = "interrupt"
    fail = "fail"


class Started(OpenScanHubEvent):
    @classmethod
    def event_type(cls) -> str:
        return "openscanhub.task.Started"


class Finished(OpenScanHubEvent):
    def __init__(
        self,
        status: Status,
        issues_added_url: str,
        issues_fixed_url: str,
        scan_results_url: str,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.status = status
        self.issues_added_url = issues_added_url
        self.issues_fixed_url = issues_fixed_url
        self.scan_results_url = scan_results_url

    @classmethod
    def event_type(cls) -> str:
        return "openscanhub.task.Finished"
