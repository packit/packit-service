# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest
from packit.config.common_package_config import Deployment

from packit_service.config import ServiceConfig
from packit_service.fedora_ci_config import FedoraCIConfig


@pytest.mark.parametrize(
    "fedora_ci_run_by_default, enabled_projects, disabled_projects, project_url, expected",
    [
        # Opt-in mode (fedora_ci_run_by_default=False)
        pytest.param(
            False,
            {"https://src.fedoraproject.org/rpms/test"},
            set(),
            "https://src.fedoraproject.org/rpms/test",
            True,
            id="opt-in: project enabled",
        ),
        pytest.param(
            False,
            {"https://src.fedoraproject.org/rpms/test"},
            set(),
            "https://src.fedoraproject.org/rpms/other",
            False,
            id="opt-in: project not enabled",
        ),
        pytest.param(
            False,
            set(),
            set(),
            "https://src.fedoraproject.org/rpms/test",
            False,
            id="opt-in: no project opted in",
        ),
        pytest.param(
            False,
            None,
            None,
            "https://src.fedoraproject.org/rpms/test",
            False,
            id="opt-in: None treated as empty",
        ),
        pytest.param(
            False,
            {
                "https://src.fedoraproject.org/rpms/test1",
                "https://src.fedoraproject.org/rpms/test2",
            },
            set(),
            "https://src.fedoraproject.org/rpms/test1",
            True,
            id="opt-in: project enabled, multiple projects opted in",
        ),
        pytest.param(
            False,
            {
                "https://src.fedoraproject.org/rpms/test1",
                "https://src.fedoraproject.org/rpms/test2",
            },
            set(),
            "https://src.fedoraproject.org/rpms/test3",
            False,
            id="opt-in: project not enabled, multiple projects opted in",
        ),
        # Opt-out mode (fedora_ci_run_by_default=True)
        pytest.param(
            True,
            set(),
            {"https://src.fedoraproject.org/rpms/test"},
            "https://src.fedoraproject.org/rpms/test",
            False,
            id="opt-out: project disabled",
        ),
        pytest.param(
            True,
            set(),
            {"https://src.fedoraproject.org/rpms/test"},
            "https://src.fedoraproject.org/rpms/other",
            True,
            id="opt-out: project not disabled",
        ),
        pytest.param(
            True,
            set(),
            set(),
            "https://src.fedoraproject.org/rpms/test",
            True,
            id="opt-out: no project disabled - empty set",
        ),
        pytest.param(
            True,
            set(),
            None,
            "https://src.fedoraproject.org/rpms/test",
            True,
            id="opt-out: no project disabled - None",
        ),
        pytest.param(
            True,
            set(),
            {
                "https://src.fedoraproject.org/rpms/test1",
                "https://src.fedoraproject.org/rpms/test2",
            },
            "https://src.fedoraproject.org/rpms/test1",
            False,
            id="opt-out: multiple projects disabled, project in list",
        ),
        pytest.param(
            True,
            set(),
            {
                "https://src.fedoraproject.org/rpms/test1",
                "https://src.fedoraproject.org/rpms/test2",
            },
            "https://src.fedoraproject.org/rpms/test3",
            True,
            id="opt-out: multiple projects disabled, project not in list",
        ),
        # Opt-out mode: non-rpms URLs should not be processed
        pytest.param(
            True,
            set(),
            set(),
            "https://src.fedoraproject.org/containers/test",
            False,
            id="opt-out: containers namespace not processed",
        ),
        pytest.param(
            True,
            set(),
            set(),
            "https://src.fedoraproject.org/modules/test",
            False,
            id="opt-out: modules namespace not processed",
        ),
        pytest.param(
            True,
            set(),
            set(),
            "https://github.com/some/repo",
            False,
            id="opt-out: non-fedora URL not processed",
        ),
    ],
)
def test_is_project_enabled(
    fedora_ci_run_by_default, enabled_projects, disabled_projects, project_url, expected
):
    config = ServiceConfig(
        deployment=Deployment.stg,
        fedora_ci_run_by_default=fedora_ci_run_by_default,
        enabled_projects_for_fedora_ci=enabled_projects,
        disabled_projects_for_fedora_ci=disabled_projects,
    )
    ci_config = FedoraCIConfig(config)
    result = ci_config.is_project_enabled(project_url)
    assert result == expected, (
        f"Expected {expected} but got {result} for project_url={project_url}, "
        f"fedora_ci_run_by_default={fedora_ci_run_by_default}, "
        f"enabled_projects={enabled_projects}, "
        f"disabled_projects={disabled_projects}"
    )
