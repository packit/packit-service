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
import os
from pathlib import Path

import pytest
from packit.config import Config

from tests.spellbook import SAVED_HTTPD_REQS


@pytest.fixture()
def dump_http_com():
    """
    This fixture is able to dump whole http traffic of a single test case so that no http comm is happening while testing

    Usage:
    1. add it to your test case and pass the test path
      def test_something(dump_http_com):
        config = dump_http_com(f"{__file__.rsplit('/', 1)[1]}/pr_handle.yaml")
    2. Run your test
      GITHUB_TOKEN=asdqwe pytest-3 -k test_something
    3. Your http communication should now be stored in tests/data/http-requests/{path}
    4. Once you rerun the tests WITHOUT the token, the offline communication should be picked up
    """
    def f(path: str):
        """ path points to a file where the http communication will be saved """
        conf = Config()
        # TODO: add pagure support
        # conf._pagure_user_token = os.environ.get("PAGURE_TOKEN", "test")
        # conf._pagure_fork_token = os.environ.get("PAGURE_FORK_TOKEN", "test")
        conf._github_token = os.getenv("GITHUB_TOKEN", None)
        conf.dry_run = True
        target_path: Path = SAVED_HTTPD_REQS / path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        conf.github_requests_log_path = str(target_path)
        return conf

    return f
