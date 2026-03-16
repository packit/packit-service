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


@pytest.mark.parametrize(
    "disabled, project_url, expected",
    [
        pytest.param(
            set(),
            "https://src.fedoraproject.org/rpms/pkg",
            True,
            id="empty disabled list",
        ),
        pytest.param(
            {"https://src.fedoraproject.org/rpms/other"},
            "https://src.fedoraproject.org/rpms/pkg",
            True,
            id="project not in disabled list",
        ),
        pytest.param(
            {"https://src.fedoraproject.org/rpms/pkg"},
            "https://src.fedoraproject.org/rpms/pkg",
            False,
            id="project in disabled list",
        ),
    ],
)
def test_is_eln_enabled(disabled, project_url, expected):
    config = ServiceConfig(
        deployment=Deployment.stg,
        disabled_projects_for_eln=disabled,
    )
    ci_config = FedoraCIConfig(config)
    assert ci_config.is_eln_enabled(project_url) == expected


@pytest.mark.parametrize(
    "global_enabled, disabled, project_url, expected",
    [
        pytest.param(
            False,
            set(),
            "https://src.fedoraproject.org/rpms/pkg",
            False,
            id="globally disabled",
        ),
        pytest.param(
            True,
            set(),
            "https://src.fedoraproject.org/rpms/pkg",
            True,
            id="globally enabled, empty disabled list",
        ),
        pytest.param(
            True,
            {"https://src.fedoraproject.org/rpms/pkg"},
            "https://src.fedoraproject.org/rpms/pkg",
            False,
            id="globally enabled, project in disabled list",
        ),
        pytest.param(
            True,
            {"https://src.fedoraproject.org/rpms/other"},
            "https://src.fedoraproject.org/rpms/pkg",
            True,
            id="globally enabled, project not in disabled list",
        ),
    ],
)
def test_is_logdetective_enabled(global_enabled, disabled, project_url, expected):
    config = ServiceConfig(
        deployment=Deployment.stg,
        logdetective_enabled=global_enabled,
        disabled_projects_for_logdetective=disabled,
    )
    ci_config = FedoraCIConfig(config)
    assert ci_config.is_logdetective_enabled(project_url) == expected
