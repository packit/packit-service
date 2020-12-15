from typing import Any, Dict


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
