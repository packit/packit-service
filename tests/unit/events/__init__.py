# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest

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
    ServiceConfig.service_config = service_config
