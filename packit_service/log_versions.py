# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging

# Mypy errors out with Module 'flask' has no attribute '__version__'.
# Python can find flask's version but mypy cannot.
# So we use "type: ignore" to cause mypy to ignore that line.
from flask import __version__ as flask_version  # type: ignore
from flask_restx import __version__ as restx_version
from sqlalchemy import __version__ as sqlal_version

from ogr import __version__ as ogr_version
from packit import __version__ as packit_version
from packit_service import __version__ as ps_version

logger = logging.getLogger(__name__)


def log_package_versions(package_versions: list):
    """It does the actual logging. Input is a list of tuples having pkg name and version."""
    log_string = "\nPackage Versions:"
    for name, version in package_versions:
        log_string += f"\n* {name} {version}"
    logger.info(log_string)


def log_worker_versions():
    """Log essential package versions used in the worker."""
    package_versions = [
        ("OGR", ogr_version),
        ("Packit", packit_version),
        ("Packit Service", ps_version),
        ("SQL Alchemy", sqlal_version),
    ]
    log_package_versions(package_versions)


def log_service_versions():
    """Log versions of packages used in the service."""
    package_versions = [
        ("Flask", flask_version),
        ("Flask RestX", restx_version),
        ("Packit Service", ps_version),
    ]
    log_package_versions(package_versions)
