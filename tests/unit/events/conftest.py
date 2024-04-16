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
    fake_tf = flexmock(pipeline_id="1", submitted_time=time, target="target")
    flexmock(TFTTestRunTargetModel).new_instances(fake_tf)
    tf = TFTTestRunTargetModel()
    tf.__class__ = TFTTestRunTargetModel

    another_fake_tf = flexmock(
        pipeline_id="2", submitted_time=latest_time, target="target"
    )
    flexmock(TFTTestRunTargetModel).new_instances(another_fake_tf)
    another_tf = TFTTestRunTargetModel()
    another_tf.__class__ = TFTTestRunTargetModel
    yield [tf, another_tf]
