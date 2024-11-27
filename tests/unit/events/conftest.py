# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from datetime import datetime

import pytest
from flexmock import flexmock
from ogr import GithubService, GitlabService, PagureService

from packit_service.config import ServiceConfig
from packit_service.models import TFTTestRunTargetModel


@pytest.fixture()
def mock_config():
    service_config = ServiceConfig()
    service_config.services = {
        GithubService(token="token"),
        GitlabService(token="token"),
        PagureService(instance_url="https://src.fedoraproject.org", token="1234"),
    }
    service_config.github_requests_log_path = "/path"
    ServiceConfig.service_config = service_config


@pytest.fixture()
def tf_models():
    time = datetime(2000, 4, 28, 14, 9, 33, 860293)
    latest_time = datetime.utcnow()
    tf = flexmock(TFTTestRunTargetModel).new_instances().mock()
    tf.pipeline_id = "1"
    tf.submitted_time = time
    tf.target = "target"

    another_tf = flexmock(TFTTestRunTargetModel).new_instances().mock()
    another_tf.pipeline_id = "2"
    another_tf.submitted_time = latest_time
    another_tf.target = "target"

    yield [tf, another_tf]
