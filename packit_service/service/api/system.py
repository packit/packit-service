# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import re
from http import HTTPStatus
from logging import getLogger
from typing import Optional

import ogr
import packit
import specfile
from flask_restx import Namespace, Resource
from setuptools_scm import get_version

import packit_service
from packit_service.service.api.utils import response_maker

logger = getLogger("packit_service")

ns = Namespace("system", description="System information")


def get_commit_from_version(version) -> Optional[str]:
    """
    Version can look like this:
        0.76.0.post18+g116edc5
        0.1.dev1+gc03b1bd.d20230615
        0.18.0.post4+g28cb117
        0.45.1.dev2+g3b0fc3b

    The 7 characters after the "+g" is the short version of the git commit hash.
    """
    if matches := re.search(r"\+g([A-Za-z0-9]{7})", version):
        return matches.groups()[0]
    return None


@ns.route("")
class SystemInformation(Resource):
    @ns.response(HTTPStatus.OK.value, "OK")
    def get(self):
        """System information"""
        packages_and_versions = {
            project: project.__version__ for project in [ogr, specfile, packit]
        }
        # packit_service might not be installed (i.e. when running locally)
        # so it's treated differently
        packages_and_versions[packit_service] = packit_service.__version__ or get_version(
            root="..",
            relative_to=packit_service.__file__,
        )

        response_data = {
            project.__name__: {
                "commit": get_commit_from_version(version),
                "version": version,
            }
            for project, version in packages_and_versions.items()
            if version
        }

        return response_maker(response_data)
