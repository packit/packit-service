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

from packit_service.config import ServiceConfig

DASHBOARD_URL = ServiceConfig().get_service_config().dashboard_url


def _get_url_for_dashboard_results(job_type: str, id_: int) -> str:
    """
    Generates an URL to the dashboard result page that can be used for setting
    URL in a commit status.

    Args:
        job_type (str): Type of results. Represents route on dashboard.

            Specifically: `srpm-builds`, `copr-builds`, `koji-builds` or `testing-farm`.
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
