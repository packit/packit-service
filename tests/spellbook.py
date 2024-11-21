# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
A book with our finest spells
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packit_service.worker.result import TaskResults

TESTS_DIR = Path(__file__).parent
DATA_DIR = TESTS_DIR / "data"
SAVED_HTTPD_REQS = DATA_DIR / "http-requests"


def first_dict_value(a_dict: dict) -> Any:
    return a_dict[next(iter(a_dict))]


def get_parameters_from_results(
    results: list[TaskResults],
) -> tuple[dict, str, dict, dict]:
    assert len(results) == 1

    event_dict = results[0]["details"]["event"]
    job = results[0]["details"].get("job")
    job_config = results[0]["details"].get("job_config")
    package_config = results[0]["details"].get("package_config")
    return event_dict, job, job_config, package_config


def load_the_message_from_file(message_file):
    return squash_the_message_structure_like_listener(json.load(message_file))


def squash_the_message_structure_like_listener(message):
    """
    In listener, we use just the `body` key
    but add the `topic` from the top level.
    See: https://github.com/packit/packit-service-fedmsg/blob/
    e53586bf7ace0c46fd6812fe8dc11491e5e6cf41/packit_service_fedmsg/consumer.py#L137
    """

    # Some older message used a `msg` key
    body = message.get("body") or message.get("msg")
    body["topic"] = message["topic"]
    body["timestamp"] = datetime.now(timezone.utc).timestamp()
    return body
