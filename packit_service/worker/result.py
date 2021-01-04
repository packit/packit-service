from typing import Any, Dict

from packit.config import JobConfig
from packit_service.service.events import Event
from packit_service.utils import dump_job_config, dump_package_config


class TaskResults(dict):
    """
    Job handler results.
    Inherit from dict to be JSON serializable.
    """

    def __init__(self, success: bool, details: Dict[str, Any] = None):
        """

        :param success: has the job handler succeeded:
                          True - we processed the event
                          False - there was an error while processing it -
                                  usually an exception
        :param details: more info from job handler
                        (optional) 'msg' key contains a message
                        more keys to be defined
        """
        super().__init__(self, success=success, details=details or {})

    @classmethod
    def create_from(
        cls, success: bool, msg: str, event: Event, job_config: JobConfig = None
    ):
        details = {
            "msg": msg,
            "event": event.get_dict(),
            "package_config": dump_package_config(event.package_config),
        }

        if job_config:
            details.update(
                {
                    "job": job_config.type.value,
                    "job_config": dump_job_config(job_config),
                }
            )

        return cls(success=success, details=details)
