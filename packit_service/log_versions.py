import logging

from ogr import __version__ as ogr_version
from packit_service import __version__ as ps_version
from sqlalchemy import __version__ as sqlal_version
from flask_restx import __version__ as restx_version

# Mypy errors out with Module 'flask' has no attribute '__version__'.
# Python can find flask's version but mypy cannot.
# So we use "type: ignore" to cause mypy to ignore that line.
from flask import __version__ as flask_version  # type: ignore


logger = logging.getLogger(__name__)


def log_package_versions(package_versions: list):
    """It does the actual logging. Input is a list of tuples having pkg name and version."""
    log_string = "\nPackage Versions:"
    for name, version in package_versions:
        log_string += f"\n* {name} {version}"
    logger.info(log_string)


def log_job_versions():
    """Log essential package versions before running a job."""
    package_versions = [
        ("OGR", ogr_version),
        ("Packit Service", ps_version),
        ("SQL Alchemy", sqlal_version),
        # NOTE: Can't log packit's version for now because it does not provide one.
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
