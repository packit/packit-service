# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version(__name__)
except PackageNotFoundError:
    # package is not installed
    __version__ = None
