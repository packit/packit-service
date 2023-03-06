# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit_service.config import ServiceConfig

DASHBOARD_URL = ServiceConfig().get_service_config().dashboard_url


def _get_url_for_dashboard_results(job_type: str, id_: int) -> str:
    """
    Generates an URL to the dashboard result page that can be used for setting
    URL in a commit status.

    Args:
        job_type (str): Type of results. Represents route on dashboard.

            Specifically: `srpm-builds`, `copr-builds`, `koji-builds`, `testing-farm`
                or `propose-downstream`.
        id_ (int): Packit ID of the build.

    Returns:
        URL to the results of `id_` entry of type `type`.
    """
    return f"{DASHBOARD_URL}/results/{job_type}/{id_}"


def get_srpm_build_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("srpm-builds", id_)


def get_copr_build_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("copr-builds", id_)


def get_koji_build_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("koji-builds", id_)


def get_testing_farm_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("testing-farm", id_)


def get_propose_downstream_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("propose-downstream", id_)


def get_pull_from_upstream_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("pull-from-upstream", id_)
