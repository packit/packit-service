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

            Specifically: `srpm`, `copr`, `koji`, `testing-farm`,
                 `propose-downstream`, `pull-from-upstream` or `openscanhub`.
        id_ (int): Packit ID of the item.

    Returns:
        URL to the results of `id_` entry of type `type`.
    """
    return f"{DASHBOARD_URL}/jobs/{job_type}/{id_}"


def get_srpm_build_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("srpm", id_)


def get_copr_build_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("copr", id_)


def get_koji_build_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("koji", id_)


def get_testing_farm_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("testing-farm", id_)


def get_propose_downstream_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("propose-downstream", id_)


def get_pull_from_upstream_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("pull-from-upstream", id_)


def get_openscanhub_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("openscanhub", id_)


def get_bodhi_update_info_url(id_: int) -> str:
    return _get_url_for_dashboard_results("bodhi", id_)
