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

import json
import logging

from flask import Blueprint, abort, request
from packit_service.config import Config

from packit_service.celerizer import celery_app

logger = logging.getLogger("packit_service")

config = Config.get_service_config()


testing_farm_api = Blueprint("testing_farm_api", __name__)


@testing_farm_api.route("/testing-farm/results", methods=["POST"])
def testing_farm_results():
    """
    Expected format:

    {
        "artifact": {
        "commit-sha": "08bfc38f15082bdf9ba964c3bbd04878666d1d56",
        "copr-chroot": "fedora-29-x86_64",
        "copr-repo-name": "packit/packit-service-hello-world-14",
        "git-ref": "08bfc38f15082bdf9ba964c3bbd04878666d1d56",
        "git-url": "https://github.com/packit-service/hello-world",
        "repo-name": "hello-world",
        "repo-namespace": "packit-service"
        },
        "message": "Command 'git' not found",
        "pipeline": {
            "id": "614d240a-1e27-4758-ad6a-ed3d34281924"
        },
        "result": "error",
        "token": "HERE-IS-A-VALID-TOKEN",
        "url": "https://console-testing-farm.apps.ci.centos.org/pipeline/<ID>"
    }

    :return: {"status": 200, "message": "Test results accepted"}
    """
    msg = request.get_json()

    if not msg:
        logger.debug("/testing-farm/results: we haven't received any JSON data.")
        return "We haven't received any JSON data."

    if not validate_testing_farm_request():
        abort(401)  # Unauthorized

    celery_app.send_task(name="task.steve_jobs.process_message", kwargs={"event": msg})

    return json.dumps({"status": 200, "message": "Test results accepted"})


def validate_testing_farm_request():
    """
    Validate testing farm token received in request with the one in packit-service.yaml
    :return:
    """
    testing_farm_secret = config.testing_farm_secret
    if not testing_farm_secret:
        logger.error("testing_farm_secret not specified in config")
        return False

    token = request.get_json().get("token")
    if not token:
        logger.info("the request doesn't contain any token")
        return False
    if token == testing_farm_secret:
        return True

    logger.warning("Invalid testing farm secret provided!")
    return False
