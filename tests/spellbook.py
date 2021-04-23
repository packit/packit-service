# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
A book with our finest spells
"""
from pathlib import Path
from typing import Any, List, Tuple
from packit_service.worker.result import TaskResults

TESTS_DIR = Path(__file__).parent
DATA_DIR = TESTS_DIR / "data"
SAVED_HTTPD_REQS = DATA_DIR / "http-requests"


def first_dict_value(a_dict: dict) -> Any:
    return a_dict[next(iter(a_dict))]


def get_parameters_from_results(
    results: List[TaskResults],
) -> Tuple[dict, str, dict, dict]:

    assert len(results) == 1

    event_dict = results[0]["details"]["event"]
    job = results[0]["details"]["job"]
    job_config = results[0]["details"]["job_config"]
    package_config = results[0]["details"]["package_config"]
    return event_dict, job, job_config, package_config
