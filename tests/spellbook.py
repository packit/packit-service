# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

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
