# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from datetime import datetime

import pytest
from flexmock import flexmock
from ogr import GithubService, GitlabService, PagureService

from packit_service.config import ServiceConfig


@pytest.fixture()
def mock_config():
    service_config = ServiceConfig()
    service_config.services = {
        GithubService(token="token"),
        GitlabService(token="token"),
        PagureService(instance_url="https://src.fedoraproject.org", token="1234"),
    }
    service_config.github_requests_log_path = "/path"
    service_config.logdetective_koji_enabled = True
    ServiceConfig.service_config = service_config


@pytest.fixture()
def tf_models():
    time = datetime(2000, 4, 28, 14, 9, 33, 860293)
    latest_time = datetime.utcnow()
    # This test only reads attributes; standalone mocks avoid patching the model class.
    tf = flexmock(pipeline_id="1", submitted_time=time, target="target", identifier=None)
    another_tf = flexmock(
        pipeline_id="2", submitted_time=latest_time, target="target", identifier=None
    )

    yield [tf, another_tf]
