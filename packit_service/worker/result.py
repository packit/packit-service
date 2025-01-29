# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Any, Optional

from packit.config import JobConfig

from packit_service.events.event import Event
from packit_service.utils import dump_job_config, dump_package_config


class TaskResults(dict):
    """
    Job handler results.
    Inherit from dict to be JSON serializable.
    """

    def __init__(self, success: bool, details: Optional[dict[str, Any]] = None):
        """
        Args:
            success: Represents the resulting state of the job handler.
                `True`, if we processed the event; `False` an error occurred
                while processing it (usually an exception)
            details: More information provided by the handler. Optionally
                contains the `msg` key with message from the handler. Other keys
                to be defined.
        """
        super().__init__(self, success=success, details=details or {})

    @classmethod
    def create_from(
        cls,
        success: bool,
        msg: str,
        event: Event,
        job_config: JobConfig = None,
    ):
        package_config = (
            event.packages_config.get_package_config_for(job_config)
            if event.packages_config
            else None
        )
        details = {
            "msg": msg,
            "event": event.get_dict(),
            "package_config": dump_package_config(package_config),
        }

        details.update(
            {
                "job": job_config.type.value if job_config else None,
                "job_config": dump_job_config(job_config),
            },
        )

        return cls(success=success, details=details)
