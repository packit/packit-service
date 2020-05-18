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

from flask import url_for

from packit_service.service.app import application


def get_srpm_log_url_from_flask(id_: int = None,) -> str:
    """
    provide absolute URL to p-s srpm build logs view meant to set in a commit status
    """
    # flask magic
    with application.app_context():
        return url_for(
            "builds.get_srpm_build_logs_by_id",
            id_=id_,
            _external=True,  # _external = generate a URL with FQDN, not a relative one
        )


def get_copr_build_log_url_from_flask(id_: int = None,) -> str:
    """
    provide absolute URL to p-s copr build logs view meant to set in a commit status
    """
    with application.app_context():
        return url_for(
            "builds.get_copr_build_logs_by_id",
            id_=id_,
            _external=True,  # _external = generate a URL with FQDN, not a relative one
        )


def get_koji_build_log_url_from_flask(id_: int = None,) -> str:
    """
    provide absolute URL to p-s koji build logs view meant to set in a commit status
    """
    with application.app_context():
        return url_for(
            "builds.get_koji_build_logs_by_id",
            id_=id_,
            _external=True,  # _external = generate a URL with FQDN, not a relative one
        )
