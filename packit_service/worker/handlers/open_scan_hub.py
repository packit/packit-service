# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import json
import logging
from typing import Optional, Union

import requests
from packit.config import (
    JobType,
)

from packit_service.events import openscanhub
from packit_service.models import OSHScanStatus
from packit_service.service.urls import get_openscanhub_info_url
from packit_service.worker.checker.abstract import Checker
from packit_service.worker.checker.open_scan_hub import IsEventForJob, RawhideX86Target
from packit_service.worker.handlers.abstract import (
    RetriableJobHandler,
    TaskName,
    configured_as,
    reacts_to,
)
from packit_service.worker.handlers.mixin import (
    ConfigFromEventMixin,
)
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.helpers.open_scan_hub import CoprOpenScanHubHelper
from packit_service.worker.mixin import (
    LocalProjectMixin,
    PackitAPIWithUpstreamMixin,
)
from packit_service.worker.reporting import BaseCommitStatus
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


class OpenScanHubAbstractHandler(
    RetriableJobHandler,
    LocalProjectMixin,
    ConfigFromEventMixin,
    PackitAPIWithUpstreamMixin,
):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.event: Union[openscanhub.task.Started, openscanhub.task.Finished] = (
            self.data.to_event()
        )

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (RawhideX86Target, IsEventForJob)

    def get_helper(self) -> CoprOpenScanHubHelper:
        build_helper = CoprBuildJobHelper(
            service_config=self.service_config,
            package_config=self.package_config,
            project=self.project,
            metadata=self.data,
            db_project_event=self.data.db_project_event,
            job_config=self.job_config,
            celery_task=self.celery_task,
        )

        return CoprOpenScanHubHelper(
            copr_build_helper=build_helper,
            build=self.event.build,
        )

    def check_scan_and_build(self):
        task_id = self.data.event_dict["task_id"]
        if not self.event.scan or not self.event.build:
            return TaskResults(
                success=True,
                details={
                    "msg": f"Scan {task_id} not found or not associated with a Copr build",
                },
            )

        if not self.job_config:
            return TaskResults(
                success=True,
                details={
                    "msg": (
                        f"No job configuration found for OpenScanHub task in {self.project.repo}"
                    ),
                },
            )

        return None


@configured_as(job_type=JobType.copr_build)
@reacts_to(openscanhub.task.Finished)
class CoprOpenScanHubTaskFinishedHandler(
    OpenScanHubAbstractHandler,
):
    event: openscanhub.task.Finished
    task_name = TaskName.openscanhub_task_finished

    def get_number_of_new_findings_identified(self) -> Optional[int]:
        """
        Downloads a JSON file from the task issues added URL and
        returns the number of items in the 'defects' array.

        Returns:
            Optional[int]: Number of items in the 'defects' array,
             or None if not found or on error.
        """
        url = self.event.issues_added_url
        logger.info(f"About to get the number of new findings identified by the scan from {url}.")

        try:
            with requests.get(url, timeout=10) as response:
                response.raise_for_status()
                data = response.json()

                defects = data.get("defects")
                if defects is None:
                    logger.debug("No 'defects' array found in the JSON data.")
                    return None

                return len(defects)

        except requests.exceptions.RequestException as e:
            logger.error(f"Error while downloading the JSON file: {e}")
            return None
        except json.JSONDecodeError:
            logger.error("The response is not a valid JSON format.")
            return None

    def get_issues_added_url(
        self,
        openscanhub_url: str = "https://openscanhub.fedoraproject.org",
        file_format: str = "html",
    ) -> str:
        """
        Constructs the URL for the added issues in the specified
        format for the given OpenScanHub task.

        Parameters:
            openscanhub_url (str)
            file_format (str): The format of the added issues file ('html' or 'json').

        Returns:
            str: The full URL to access the added issues in the specified format.
        """
        return f"{openscanhub_url}/task/{self.event.task_id}/log/added.{file_format}"

    def run(self) -> TaskResults:
        self.check_scan_and_build()
        external_links = {"OpenScanHub task": self.event.scan.url}
        if self.event.status == openscanhub.task.Status.success:
            state = BaseCommitStatus.success
            number_of_new_findings = self.get_number_of_new_findings_identified()
            base_description = "Scan in OpenScanHub is finished."

            if number_of_new_findings is None:
                description = (
                    f"{base_description} We were not able to analyse the findings; "
                    f"please check the URL."
                )
                external_links.update({"Added issues": self.get_issues_added_url()})
            elif number_of_new_findings > 0:
                description = (
                    f"{base_description} {number_of_new_findings} new findings identified."
                )
                external_links.update({"Added issues": self.get_issues_added_url()})
                self.event.scan.set_issues_added_count(number_of_new_findings)
            else:
                description = f"{base_description} No new findings identified."
                self.event.scan.set_issues_added_count(number_of_new_findings)

            self.event.scan.set_status(OSHScanStatus.succeeded)
            self.event.scan.set_issues_added_url(self.event.issues_added_url)
            self.event.scan.set_issues_fixed_url(self.event.issues_fixed_url)
            self.event.scan.set_scan_results_url(self.event.scan_results_url)
        else:
            state = BaseCommitStatus.neutral
            description = f"Scan in OpenScanHub is finished in a {self.event.status} state."
            if self.event.status == openscanhub.task.Status.cancel:
                self.event.scan.set_status(OSHScanStatus.canceled)
            else:
                self.event.scan.set_status(OSHScanStatus.failed)

        self.get_helper().report(
            state=state,
            description=description,
            url=get_openscanhub_info_url(self.event.scan.id),
            links_to_external_services=external_links,
        )

        return TaskResults(
            success=True,
            details={},
        )


@configured_as(job_type=JobType.copr_build)
@reacts_to(openscanhub.task.Started)
class CoprOpenScanHubTaskStartedHandler(
    OpenScanHubAbstractHandler,
):
    task_name = TaskName.openscanhub_task_started

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.event: openscanhub.task.Started = self.data.to_event()

    def run(self) -> TaskResults:
        self.check_scan_and_build()

        state = BaseCommitStatus.running
        description = "Scan in OpenScanHub has started."
        self.event.scan.set_status(OSHScanStatus.running)

        self.get_helper().report(
            state=state,
            description=description,
            url=self.event.scan.url,
        )

        return TaskResults(
            success=True,
            details={},
        )
