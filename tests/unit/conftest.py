# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from flexmock import flexmock

import packit


@pytest.fixture()
def mock_get_aliases():
    mock_aliases_module = flexmock(packit.config.aliases)
    mock_aliases_module.should_receive("get_aliases").and_return(
        {
            "fedora-all": ["fedora-31", "fedora-32", "fedora-33", "fedora-rawhide"],
            "fedora-stable": ["fedora-31", "fedora-32"],
            "fedora-development": ["fedora-33", "fedora-rawhide"],
            "epel-all": ["epel-6", "epel-7", "epel-8"],
        }
    )
